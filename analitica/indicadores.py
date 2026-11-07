"""Cálculo de los indicadores de negocio del tablero de control.

Cada indicador declara su fórmula, su meta, el sentido en que mejora y la
decisión empresarial que respalda. Las reglas de cumplimiento y estado son
las mismas para todos:

* Cumplimiento (% ) = valor / meta × 100 cuando "más es mejor".
* Cumplimiento (% ) = meta / valor × 100 cuando "menos es mejor".
* Estado: "Cumple" si alcanza la meta; "En riesgo" si no la alcanza pero el
  cumplimiento es de al menos 90 %; "Crítico" por debajo de 90 %.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce

from comercial.models import Venta
from maestros.models import Producto

from .models import DimFecha, FactMovimiento, FactRecaudo, FactVenta

UMBRAL_RIESGO = Decimal("90")
CERO = Decimal("0")
DIAS_ANIO = Decimal("365")


def _d(valor):
    if valor is None:
        return None
    return Decimal(valor)


def redondear(valor, decimales=1):
    if valor is None:
        return None
    cuantizador = Decimal(1).scaleb(-decimales)
    return Decimal(valor).quantize(cuantizador, rounding=ROUND_HALF_UP)


@dataclass
class Indicador:
    clave: str
    nombre: str
    valor: Decimal | None
    meta: Decimal | None
    direccion: str  # "mayor" o "menor": sentido en que el indicador mejora
    unidad: str
    formula: str
    decision: str
    calculo: str = ""
    anterior: Decimal | None = None
    detalle: dict = field(default_factory=dict)

    @property
    def cumple(self):
        if self.valor is None or self.meta is None:
            return None
        return self.valor >= self.meta if self.direccion == "mayor" else self.valor <= self.meta

    @property
    def cumplimiento(self):
        if self.valor is None or self.meta is None or self.meta == 0:
            return None
        if self.direccion == "mayor":
            return redondear(self.valor / self.meta * 100, 0)
        if self.valor == 0:
            return None  # sin valor de referencia para la razón; cumple por definición
        return redondear(self.meta / self.valor * 100, 0)

    @property
    def estado(self):
        if self.valor is None:
            return "sin_datos"
        if self.meta is None:
            return "sin_meta"
        if self.cumple:
            return "cumple"
        if self.cumplimiento is not None and self.cumplimiento >= UMBRAL_RIESGO:
            return "riesgo"
        return "critico"

    @property
    def estado_texto(self):
        return {
            "cumple": "Cumple la meta",
            "riesgo": "En riesgo",
            "critico": "Crítico",
            "sin_datos": "Sin datos en el periodo",
            "sin_meta": "Sin meta definida",
        }[self.estado]

    @property
    def variacion(self):
        if self.valor is None or self.anterior is None:
            return None
        return self.valor - self.anterior

    @property
    def variacion_favorable(self):
        if not self.variacion:
            return None
        return self.variacion > 0 if self.direccion == "mayor" else self.variacion < 0

    @property
    def bala(self):
        """Geometría de la gráfica de bala (Few, 2006) en porcentajes del ancho."""
        if self.valor is None or self.meta is None:
            return None
        maximo = max(self.valor, self.meta) * Decimal("1.25")
        if maximo <= 0:
            maximo = Decimal("1")

        def pct(x):
            return float(max(CERO, min(Decimal("100"), x / maximo * 100)))

        if self.direccion == "mayor":
            bandas = [
                ("malo", 0, pct(self.meta * UMBRAL_RIESGO / 100)),
                ("regular", pct(self.meta * UMBRAL_RIESGO / 100), pct(self.meta)),
                ("bueno", pct(self.meta), 100),
            ]
        else:
            limite_riesgo = self.meta * 100 / UMBRAL_RIESGO
            bandas = [
                ("bueno", 0, pct(self.meta)),
                ("regular", pct(self.meta), pct(limite_riesgo)),
                ("malo", pct(limite_riesgo), 100),
            ]
        return {
            "bandas": [{"tipo": t, "desde": a, "ancho": max(0.0, b - a)} for t, a, b in bandas],
            "valor": pct(self.valor),
            "meta": pct(self.meta),
        }


def periodo_anterior(inicio, fin):
    dias = (fin - inicio).days + 1
    return inicio - timedelta(days=dias), inicio - timedelta(days=1)


def _ventas(empresa, inicio, fin):
    return FactVenta.objects.filter(empresa=empresa, fecha_id__gte=inicio, fecha_id__lte=fin)


def valor_inventario(empresa, hasta):
    total = FactMovimiento.objects.filter(empresa=empresa, fecha_id__lte=hasta).aggregate(t=Sum("valor_neto"))["t"]
    return _d(total) or CERO


def stock_por_producto(empresa, hasta):
    filas = (
        FactMovimiento.objects.filter(empresa=empresa, fecha_id__lte=hasta)
        .values("producto_id")
        .annotate(stock=Sum("cantidad_neta"))
    )
    return {f["producto_id"]: f["stock"] or 0 for f in filas}


def productos_vigentes(empresa, hasta):
    return Producto.objects.filter(empresa=empresa, activo=True, creado__date__lte=hasta)


def saldo_cartera(empresa, hasta):
    vendido = FactVenta.objects.filter(
        empresa=empresa, forma_pago=Venta.CREDITO, fecha_id__lte=hasta
    ).aggregate(t=Sum("ingreso"))["t"]
    recaudado = FactRecaudo.objects.filter(
        empresa=empresa, forma_pago=Venta.CREDITO, fecha_id__lte=hasta
    ).aggregate(t=Sum("valor"))["t"]
    return (_d(vendido) or CERO) - (_d(recaudado) or CERO)


def valores_base(empresa, inicio, fin):
    """Cifras crudas del periodo, a partir de las vistas de hechos."""
    dias = Decimal((fin - inicio).days + 1)
    ventas = _ventas(empresa, inicio, fin)
    agregado = ventas.aggregate(
        ingresos=Sum("ingreso"),
        costo=Sum("costo"),
        ventas=Count("venta_id", distinct=True),
        clientes=Count("cliente_id", distinct=True),
        credito=Sum("ingreso", filter=Q(forma_pago=Venta.CREDITO)),
    )
    ingresos = _d(agregado["ingresos"]) or CERO
    costo = _d(agregado["costo"]) or CERO
    n_ventas = agregado["ventas"] or 0
    ventas_credito = _d(agregado["credito"]) or CERO

    inv_inicial = valor_inventario(empresa, inicio - timedelta(days=1))
    inv_final = valor_inventario(empresa, fin)
    inv_promedio = (inv_inicial + inv_final) / 2

    saldo = saldo_cartera(empresa, fin)

    vigentes = list(productos_vigentes(empresa, fin).values_list("id", flat=True))
    stock = stock_por_producto(empresa, fin)
    en_quiebre = [pid for pid in vigentes if stock.get(pid, 0) <= 0]

    compras_por_cliente = (
        ventas.values("cliente_id").annotate(n=Count("venta_id", distinct=True)).values_list("n", flat=True)
    )
    compras = list(compras_por_cliente)
    clientes_compradores = len(compras)
    clientes_recurrentes = sum(1 for n in compras if n >= 2)

    return {
        "dias": dias,
        "ingresos": ingresos,
        "costo": costo,
        "contribucion": ingresos - costo,
        "n_ventas": n_ventas,
        "ventas_credito": ventas_credito,
        "inv_inicial": inv_inicial,
        "inv_final": inv_final,
        "inv_promedio": inv_promedio,
        "saldo_cartera": saldo,
        "productos_vigentes": len(vigentes),
        "productos_quiebre": len(en_quiebre),
        "ids_quiebre": en_quiebre,
        "clientes_compradores": clientes_compradores,
        "clientes_recurrentes": clientes_recurrentes,
        "margen": (ingresos - costo) / ingresos * 100 if ingresos > 0 else None,
        "ticket": ingresos / n_ventas if n_ventas else None,
        "rotacion": (costo / inv_promedio) * (DIAS_ANIO / dias) if inv_promedio > 0 and costo > 0 else None,
        "dso": saldo / ventas_credito * dias if ventas_credito > 0 else None,
        "quiebres": Decimal(len(en_quiebre)) / len(vigentes) * 100 if vigentes else None,
        "recurrentes": Decimal(clientes_recurrentes) / clientes_compradores * 100 if clientes_compradores else None,
    }


def calcular_indicadores(empresa, inicio, fin):
    actual = valores_base(empresa, inicio, fin)
    previo = valores_base(empresa, *periodo_anterior(inicio, fin))

    def cop(x):
        return f"$ {redondear(x, 0):,.0f}".replace(",", ".")

    indicadores = [
        Indicador(
            clave="rotacion",
            nombre="Rotación de inventario",
            valor=redondear(actual["rotacion"], 1),
            anterior=redondear(previo["rotacion"], 1),
            meta=empresa.meta_rotacion,
            direccion="mayor",
            unidad="veces/año",
            formula="(Costo de la mercancía vendida ÷ inventario promedio a costo) × (365 ÷ días del periodo)",
            calculo=(
                f"Costo vendido {cop(actual['costo'])} ÷ inventario promedio {cop(actual['inv_promedio'])} "
                f"× 365 ÷ {actual['dias']} días"
            ),
            decision="Cuánto y cuándo comprar: una rotación baja indica capital inmovilizado en bodega.",
        ),
        Indicador(
            clave="margen",
            nombre="Margen de contribución",
            valor=redondear(actual["margen"], 1),
            anterior=redondear(previo["margen"], 1),
            meta=empresa.meta_margen,
            direccion="mayor",
            unidad="%",
            formula="(Ventas − costo de la mercancía vendida) ÷ ventas × 100",
            calculo=f"({cop(actual['ingresos'])} − {cop(actual['costo'])}) ÷ {cop(actual['ingresos'])}",
            decision="Fijación de precios y mezcla de productos: qué líneas sostienen la rentabilidad.",
        ),
        Indicador(
            clave="dso",
            nombre="Días de cartera (DSO)",
            valor=redondear(actual["dso"], 0),
            anterior=redondear(previo["dso"], 0),
            meta=empresa.meta_dso,
            direccion="menor",
            unidad="días",
            formula="(Saldo de cartera al cierre ÷ ventas a crédito del periodo) × días del periodo",
            calculo=(
                f"Saldo {cop(actual['saldo_cartera'])} ÷ ventas a crédito {cop(actual['ventas_credito'])} "
                f"× {actual['dias']} días"
            ),
            decision="Política de crédito y gestión de cobro: a quién dar plazo y a quién cobrar primero.",
        ),
        Indicador(
            clave="quiebres",
            nombre="Productos agotados (quiebres de stock)",
            valor=redondear(actual["quiebres"], 1),
            anterior=redondear(previo["quiebres"], 1),
            meta=empresa.meta_quiebres,
            direccion="menor",
            unidad="%",
            formula="Productos activos con existencia 0 al cierre ÷ productos activos × 100",
            calculo=f"{actual['productos_quiebre']} agotados de {actual['productos_vigentes']} productos activos",
            decision="Reposición urgente: qué referencias pedir hoy para no perder ventas.",
        ),
        Indicador(
            clave="ticket",
            nombre="Ticket promedio",
            valor=redondear(actual["ticket"], 0),
            anterior=redondear(previo["ticket"], 0),
            meta=empresa.meta_ticket,
            direccion="mayor",
            unidad="COP",
            formula="Ventas del periodo ÷ número de ventas emitidas",
            calculo=f"{cop(actual['ingresos'])} ÷ {actual['n_ventas']} ventas",
            decision="Estrategia comercial: impulsar venta cruzada (kits de dotación) cuando el ticket cae.",
        ),
        Indicador(
            clave="recurrentes",
            nombre="Clientes recurrentes",
            valor=redondear(actual["recurrentes"], 1),
            anterior=redondear(previo["recurrentes"], 1),
            meta=empresa.meta_recurrentes,
            direccion="mayor",
            unidad="%",
            formula="Clientes con 2 o más compras en el periodo ÷ clientes que compraron en el periodo × 100",
            calculo=f"{actual['clientes_recurrentes']} de {actual['clientes_compradores']} clientes compradores",
            decision="Fidelización: a qué clientes contactar para que vuelvan a comprar.",
        ),
    ]
    return indicadores, actual


# ---------------------------------------------------------------------------
# Series y tablas de apoyo para el tablero
# ---------------------------------------------------------------------------

def serie_semanal(empresa, inicio, fin):
    semanas = OrderedDict()
    for f in DimFecha.objects.filter(fecha__gte=inicio, fecha__lte=fin).order_by("fecha"):
        clave = (f.anio_iso, f.semana_iso)
        if clave not in semanas:
            semanas[clave] = {"desde": f.fecha, "ingresos": CERO, "costo": CERO}
    filas = (
        _ventas(empresa, inicio, fin)
        .values("fecha__anio_iso", "fecha__semana_iso")
        .annotate(ingresos=Sum("ingreso"), costo=Sum("costo"))
    )
    for fila in filas:
        clave = (fila["fecha__anio_iso"], fila["fecha__semana_iso"])
        if clave in semanas:
            semanas[clave]["ingresos"] = _d(fila["ingresos"]) or CERO
            semanas[clave]["costo"] = _d(fila["costo"]) or CERO
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    resultado = []
    for (anio, semana), datos in semanas.items():
        ingresos = datos["ingresos"]
        margen = (ingresos - datos["costo"]) / ingresos * 100 if ingresos > 0 else None
        resultado.append({
            "etiqueta": f"{datos['desde'].day} {meses[datos['desde'].month - 1]}",
            "semana": f"S{semana}",
            "ingresos": float(ingresos),
            "margen": float(redondear(margen, 1)) if margen is not None else None,
        })
    return resultado


TRAMOS_CARTERA = [
    ("Por vencer", None, 0),
    ("1–30 días", 1, 30),
    ("31–60 días", 31, 60),
    ("61–90 días", 61, 90),
    ("Más de 90 días", 91, None),
]


def ventas_credito_con_saldo(empresa, fecha):
    ventas = (
        Venta.objects.filter(empresa=empresa, estado=Venta.EMITIDA, forma_pago=Venta.CREDITO, fecha__lte=fecha)
        .select_related("cliente")
        .annotate(
            pagado_corte=Coalesce(
                Sum("pagos__valor", filter=Q(pagos__fecha__lte=fecha)),
                Value(CERO),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        )
    )
    for venta in ventas:
        saldo = venta.total - venta.pagado_corte
        if saldo > 0:
            venta.saldo_corte = saldo
            venta.dias_vencida_corte = (
                (fecha - venta.fecha_vencimiento).days if venta.fecha_vencimiento else 0
            )
            yield venta


def cartera_por_antiguedad(empresa, fecha):
    tramos = OrderedDict((nombre, CERO) for nombre, _, _ in TRAMOS_CARTERA)
    for venta in ventas_credito_con_saldo(empresa, fecha):
        dias = venta.dias_vencida_corte
        for nombre, desde, hasta in TRAMOS_CARTERA:
            if desde is None and dias <= 0:
                tramos[nombre] += venta.saldo_corte
                break
            if desde is not None and dias >= desde and (hasta is None or dias <= hasta):
                tramos[nombre] += venta.saldo_corte
                break
    return [{"tramo": k, "saldo": float(v)} for k, v in tramos.items()]


def clientes_con_cartera_vencida(empresa, fecha, limite=6):
    por_cliente = {}
    for venta in ventas_credito_con_saldo(empresa, fecha):
        if venta.dias_vencida_corte <= 0:
            continue
        datos = por_cliente.setdefault(
            venta.cliente_id, {"cliente": venta.cliente, "saldo": CERO, "dias": 0, "ventas": 0}
        )
        datos["saldo"] += venta.saldo_corte
        datos["dias"] = max(datos["dias"], venta.dias_vencida_corte)
        datos["ventas"] += 1
    return sorted(por_cliente.values(), key=lambda d: d["saldo"], reverse=True)[:limite]


def top_productos_contribucion(empresa, inicio, fin, limite=8):
    filas = (
        _ventas(empresa, inicio, fin)
        .values("producto_id", "producto__nombre")
        .annotate(ingreso=Sum("ingreso"), costo=Sum("costo"), unidades=Sum("cantidad"))
        .annotate(contribucion=F("ingreso") - F("costo"))
        .order_by("-contribucion")[:limite]
    )
    return [
        {
            "producto": f["producto__nombre"],
            "contribucion": float(f["contribucion"] or 0),
            "ingreso": float(f["ingreso"] or 0),
            "unidades": f["unidades"],
        }
        for f in filas
    ]


def margen_por_categoria(empresa, inicio, fin):
    filas = (
        _ventas(empresa, inicio, fin)
        .values("categoria_id", "categoria__nombre", "categoria__meta_margen")
        .annotate(ingreso=Sum("ingreso"), costo=Sum("costo"))
        .order_by("categoria__nombre")
    )
    resultado = []
    for f in filas:
        ingreso = _d(f["ingreso"]) or CERO
        if ingreso <= 0:
            continue
        margen = redondear((ingreso - (_d(f["costo"]) or CERO)) / ingreso * 100, 1)
        meta = f["categoria__meta_margen"] if f["categoria__meta_margen"] is not None else empresa.meta_margen
        resultado.append({
            "categoria": f["categoria__nombre"] or "Sin categoría",
            "ingreso": ingreso,
            "margen": margen,
            "meta": meta,
            "meta_propia": f["categoria__meta_margen"] is not None,
            "cumple": margen >= meta,
        })
    return resultado


def productos_sin_ventas_con_stock(empresa, inicio, fin, limite=6):
    vendidos = set(_ventas(empresa, inicio, fin).values_list("producto_id", flat=True))
    stock = stock_por_producto(empresa, fin)
    candidatos = productos_vigentes(empresa, fin).exclude(id__in=vendidos)
    filas = []
    for p in candidatos:
        unidades = stock.get(p.id, 0)
        if unidades > 0:
            filas.append({"producto": p, "unidades": unidades, "valor": unidades * p.costo_unitario})
    return sorted(filas, key=lambda d: d["valor"], reverse=True)[:limite]


def clientes_de_una_compra(empresa, inicio, fin, limite=6):
    filas = (
        _ventas(empresa, inicio, fin)
        .values("cliente_id", "cliente__nombre", "cliente__telefono")
        .annotate(n=Count("venta_id", distinct=True), total=Sum("ingreso"))
        .filter(n=1)
        .order_by("-total")[:limite]
    )
    return list(filas)


def alertas_stock(empresa):
    return (
        Producto.objects.filter(empresa=empresa, activo=True, stock_actual__lte=F("stock_minimo"))
        .select_related("categoria")
        .order_by("stock_actual", "nombre")
    )


def acciones_sugeridas(empresa, indicadores, inicio, fin):
    """Traduce el estado de cada indicador en una acción concreta y verificable."""
    por_clave = {i.clave: i for i in indicadores}
    acciones = []

    quiebres = por_clave["quiebres"]
    if quiebres.estado in {"riesgo", "critico"}:
        agotados = list(alertas_stock(empresa).filter(stock_actual__lte=0)[:5])
        acciones.append({
            "indicador": quiebres,
            "titulo": "Reponer productos agotados",
            "texto": "Hay referencias sin existencias. Pídelas al proveedor antes de perder ventas.",
            "items": [p.nombre for p in agotados],
            "enlace": "inventario:alertas",
        })

    dso = por_clave["dso"]
    if dso.estado in {"riesgo", "critico"}:
        morosos = clientes_con_cartera_vencida(empresa, fin, limite=5)
        acciones.append({
            "indicador": dso,
            "titulo": "Priorizar el cobro de cartera vencida",
            "texto": f"La cartera tarda {dso.valor} días en recaudarse; la meta es {dso.meta:.0f}. Empieza por los saldos vencidos más altos.",
            "items": [f"{m['cliente'].nombre} — {m['dias']} días de mora" for m in morosos],
            "enlace": "comercial:cartera",
        })

    margen = por_clave["margen"]
    if margen.estado in {"riesgo", "critico"}:
        bajas = [c for c in margen_por_categoria(empresa, inicio, fin) if not c["cumple"]]
        acciones.append({
            "indicador": margen,
            "titulo": "Revisar precios de las líneas con menor margen",
            "texto": "El margen del periodo está por debajo de la meta. Estas líneas son las que lo reducen:",
            "items": [f"{c['categoria']}: {c['margen']} % (meta {c['meta']:.0f} %)" for c in bajas],
            "enlace": "maestros:productos",
        })

    rotacion = por_clave["rotacion"]
    if rotacion.estado in {"riesgo", "critico"}:
        quietos = productos_sin_ventas_con_stock(empresa, inicio, fin, limite=5)
        acciones.append({
            "indicador": rotacion,
            "titulo": "Liberar inventario que no se mueve",
            "texto": "Estos productos tienen existencias y no se vendieron en el periodo. Evalúa promociones o pausa su compra.",
            "items": [f"{q['producto'].nombre} — {q['unidades']} und." for q in quietos],
            "enlace": "maestros:productos",
        })

    recurrentes = por_clave["recurrentes"]
    if recurrentes.estado in {"riesgo", "critico"}:
        unicos = clientes_de_una_compra(empresa, inicio, fin, limite=5)
        acciones.append({
            "indicador": recurrentes,
            "titulo": "Contactar clientes de una sola compra",
            "texto": "Una segunda compra es la señal de fidelización. Estos clientes compraron una vez en el periodo:",
            "items": [c["cliente__nombre"] for c in unicos],
            "enlace": "maestros:clientes",
        })

    ticket = por_clave["ticket"]
    if ticket.estado in {"riesgo", "critico"}:
        acciones.append({
            "indicador": ticket,
            "titulo": "Subir el valor de cada venta",
            "texto": "Ofrece kits de dotación completos (casco, gafas, guantes y botas) en lugar de piezas sueltas.",
            "items": [],
            "enlace": "comercial:cotizaciones",
        })
    return acciones
