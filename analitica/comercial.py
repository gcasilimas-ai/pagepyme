"""Cálculos del tablero comercial.

Reglas usadas:

* **Días hábiles**: lunes a sábado (el comercio de EPP atiende los sábados).
  No se descuentan festivos.
* **Ritmo esperado**: la meta del mes se reparte en partes iguales entre los
  días hábiles; lo esperado a la fecha es meta × días hábiles transcurridos ÷
  días hábiles del mes.
* **Proyección al cierre**: ventas a la fecha ÷ días hábiles transcurridos ×
  días hábiles del mes.
* **Estado de la meta**: mientras el mes está abierto se evalúa la proyección;
  cerrado el mes, las ventas reales. "Cumple" si llega a la meta, "En riesgo"
  si llega al menos al 90 %, "Crítico" por debajo.
* **Clientes que dejaron de comprar**: para clientes con dos o más compras se
  calcula su intervalo promedio entre compras. Está *en riesgo* si lleva más de
  1,5 veces ese intervalo (y más de 21 días) sin comprar, y *dejó de comprar* si
  lleva más del doble (y más de 30 días). Un cliente de una sola compra *no
  volvió* si pasaron más de 60 días.
"""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Max, Min, Sum

from comercial.models import Cotizacion, MetaComercial, Seguimiento
from maestros.models import Cliente

from .indicadores import UMBRAL_RIESGO, redondear
from .models import FactVenta

CERO = Decimal("0")
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _d(valor):
    return Decimal(valor) if valor is not None else CERO


def limites_mes(anio, mes):
    return date(anio, mes, 1), date(anio, mes, monthrange(anio, mes)[1])


def mes_anterior(anio, mes, pasos=1):
    total = anio * 12 + (mes - 1) - pasos
    return total // 12, total % 12 + 1


def es_habil(dia):
    return dia.weekday() < 6


def dias_habiles(inicio, fin):
    if fin < inicio:
        return 0
    total, dia = 0, inicio
    while dia <= fin:
        total += es_habil(dia)
        dia += timedelta(days=1)
    return total


def ventas_entre(empresa, inicio, fin):
    if fin < inicio:
        return CERO
    valor = FactVenta.objects.filter(empresa=empresa, fecha_id__gte=inicio, fecha_id__lte=fin) \
        .aggregate(t=Sum("ingreso"))["t"]
    return _d(valor)


def meta_del_mes(empresa, anio, mes):
    meta = MetaComercial.objects.filter(empresa=empresa, anio=anio, mes=mes).first()
    return meta.valor if meta else None


@dataclass
class AvanceMeta:
    anio: int
    mes: int
    inicio: date
    fin: date
    corte: date
    ventas: Decimal
    meta: Decimal | None
    habiles_total: int
    habiles_transcurridos: int

    @property
    def nombre_mes(self):
        return f"{MESES[self.mes - 1]} de {self.anio}"

    @property
    def cerrado(self):
        return self.corte >= self.fin

    @property
    def habiles_restantes(self):
        return self.habiles_total - self.habiles_transcurridos

    @property
    def cumplimiento(self):
        if not self.meta:
            return None
        return redondear(self.ventas / self.meta * 100, 0)

    @property
    def esperado(self):
        if not self.meta or not self.habiles_total:
            return None
        return self.meta * self.habiles_transcurridos / self.habiles_total

    @property
    def esperado_pct(self):
        if not self.meta or self.esperado is None:
            return None
        return float(min(Decimal(100), self.esperado / self.meta * 100))

    @property
    def diferencia_ritmo(self):
        if self.esperado is None:
            return None
        return self.ventas - self.esperado

    @property
    def proyeccion(self):
        if not self.habiles_transcurridos:
            return None
        if self.cerrado:
            return self.ventas
        return self.ventas / self.habiles_transcurridos * self.habiles_total

    @property
    def falta(self):
        if not self.meta:
            return None
        return max(self.meta - self.ventas, CERO)

    @property
    def por_dia(self):
        if self.falta is None:
            return None
        if self.habiles_restantes <= 0:
            return self.falta
        return self.falta / self.habiles_restantes

    @property
    def avance_pct(self):
        if not self.meta:
            return None
        return float(min(Decimal(100), self.ventas / self.meta * 100))

    @property
    def estado(self):
        if not self.meta:
            return "sin_meta"
        referencia = self.ventas if self.cerrado else self.proyeccion
        if referencia is None:
            return "sin_datos"
        if referencia >= self.meta:
            return "cumple"
        if referencia / self.meta * 100 >= UMBRAL_RIESGO:
            return "riesgo"
        return "critico"

    @property
    def estado_texto(self):
        if self.cerrado:
            return {"cumple": "Meta cumplida", "riesgo": "Cerca de la meta", "critico": "Meta no cumplida",
                    "sin_meta": "Sin meta definida", "sin_datos": "Sin ventas"}[self.estado]
        return {"cumple": "Al ritmo actual se cumple", "riesgo": "Ritmo por debajo de la meta",
                "critico": "Ritmo muy por debajo de la meta", "sin_meta": "Sin meta definida",
                "sin_datos": "Aún sin ventas"}[self.estado]


def avance_meta(empresa, anio, mes, corte):
    inicio, fin = limites_mes(anio, mes)
    corte = min(corte, fin)
    return AvanceMeta(
        anio=anio, mes=mes, inicio=inicio, fin=fin, corte=corte,
        ventas=ventas_entre(empresa, inicio, corte),
        meta=meta_del_mes(empresa, anio, mes),
        habiles_total=dias_habiles(inicio, fin),
        habiles_transcurridos=dias_habiles(inicio, corte),
    )


def serie_diaria(empresa, avance):
    """Ventas acumuladas día a día frente a la meta acumulada (repartida por días hábiles)."""
    por_dia = {
        f["fecha_id"]: _d(f["t"])
        for f in FactVenta.objects.filter(empresa=empresa, fecha_id__gte=avance.inicio, fecha_id__lte=avance.corte)
        .values("fecha_id").annotate(t=Sum("ingreso"))
    }
    serie, acumulado, habiles = [], CERO, 0
    dia = avance.inicio
    while dia <= avance.fin:
        habiles += es_habil(dia)
        if dia <= avance.corte:
            acumulado += por_dia.get(dia, CERO)
        serie.append({
            "dia": dia.day,
            "fecha": dia.isoformat(),
            "ventas": float(acumulado) if dia <= avance.corte else None,
            "meta": float(avance.meta * habiles / avance.habiles_total) if avance.meta and avance.habiles_total else None,
        })
        dia += timedelta(days=1)
    return serie


def serie_mensual(empresa, anio, mes, corte, meses=12):
    periodos = [mes_anterior(anio, mes, i) for i in range(meses - 1, -1, -1)]
    inicio = date(*periodos[0], 1)
    fin = min(limites_mes(anio, mes)[1], corte)
    ventas = {
        (f["fecha__anio"], f["fecha__mes"]): _d(f["t"])
        for f in FactVenta.objects.filter(empresa=empresa, fecha_id__gte=inicio, fecha_id__lte=fin)
        .values("fecha__anio", "fecha__mes").annotate(t=Sum("ingreso"))
    }
    metas = {
        (m.anio, m.mes): m.valor
        for m in MetaComercial.objects.filter(empresa=empresa, anio__gte=periodos[0][0], anio__lte=anio)
    }
    resultado = []
    for a, m in periodos:
        valor = ventas.get((a, m), CERO)
        meta = metas.get((a, m))
        en_curso = (a, m) == (anio, mes) and corte < limites_mes(a, m)[1]
        resultado.append({
            "etiqueta": f"{MESES_CORTOS[m - 1]} {str(a)[2:]}",
            "ventas": float(valor),
            "meta": float(meta) if meta else None,
            "cumplimiento": float(redondear(valor / meta * 100, 0)) if meta else None,
            "en_curso": en_curso,
        })
    # Una empresa con menos de un año de historia no necesita meses vacíos al inicio de la gráfica.
    while len(resultado) > 1 and not resultado[0]["ventas"] and resultado[0]["meta"] is None:
        resultado.pop(0)
    return resultado


def resumen_mes(empresa, inicio, corte):
    ventas = FactVenta.objects.filter(empresa=empresa, fecha_id__gte=inicio, fecha_id__lte=corte)
    agregado = ventas.aggregate(
        ingresos=Sum("ingreso"), n=Count("venta_id", distinct=True),
        clientes=Count("cliente_id", distinct=True), unidades=Sum("cantidad"),
    )
    compradores = set(ventas.values_list("cliente_id", flat=True))
    anteriores = set(
        FactVenta.objects.filter(empresa=empresa, cliente_id__in=compradores, fecha_id__lt=inicio)
        .values_list("cliente_id", flat=True)
    )
    ingresos = _d(agregado["ingresos"])
    n = agregado["n"] or 0

    # Mismo tramo del mes anterior (del día 1 al mismo día del mes)
    anio_prev, mes_prev = mes_anterior(inicio.year, inicio.month)
    inicio_prev, fin_prev = limites_mes(anio_prev, mes_prev)
    corte_prev = min(inicio_prev + (corte - inicio), fin_prev)
    previo = ventas_entre(empresa, inicio_prev, corte_prev)

    cotizaciones = Cotizacion.objects.filter(empresa=empresa, fecha__gte=inicio, fecha__lte=corte)
    n_cot = cotizaciones.count()
    n_conv = cotizaciones.filter(estado=Cotizacion.CONVERTIDA).count()
    return {
        "ingresos": ingresos,
        "n_ventas": n,
        "ticket": ingresos / n if n else None,
        "clientes": agregado["clientes"] or 0,
        "clientes_nuevos": len(compradores - anteriores),
        "unidades": agregado["unidades"] or 0,
        "previo": previo,
        "corte_previo": corte_prev,
        "variacion_previo": redondear((ingresos - previo) / previo * 100, 1) if previo else None,
        "cotizaciones": n_cot,
        "cotizaciones_convertidas": n_conv,
        "conversion": redondear(Decimal(n_conv) / n_cot * 100, 0) if n_cot else None,
    }


def top_productos(empresa, inicio, corte, limite=10):
    dias = (corte - inicio).days
    inicio_prev = inicio - timedelta(days=dias + 1)
    fin_prev = inicio - timedelta(days=1)
    anteriores = {
        f["producto_id"]: _d(f["t"])
        for f in FactVenta.objects.filter(empresa=empresa, fecha_id__gte=inicio_prev, fecha_id__lte=fin_prev)
        .values("producto_id").annotate(t=Sum("ingreso"))
    }
    total = ventas_entre(empresa, inicio, corte)
    filas = (
        FactVenta.objects.filter(empresa=empresa, fecha_id__gte=inicio, fecha_id__lte=corte)
        .values("producto_id", "producto__nombre", "producto__codigo", "producto__stock_actual",
                "producto__stock_minimo", "producto__unidad")
        .annotate(ingreso=Sum("ingreso"), unidades=Sum("cantidad"), costo=Sum("costo"))
        .order_by("-ingreso")[:limite]
    )
    resultado = []
    for f in filas:
        ingreso = _d(f["ingreso"])
        previo = anteriores.get(f["producto_id"])
        stock, minimo = f["producto__stock_actual"], f["producto__stock_minimo"]
        resultado.append({
            "id": f["producto_id"],
            "producto": f["producto__nombre"],
            "codigo": f["producto__codigo"],
            "ingreso": ingreso,
            "unidades": f["unidades"],
            "margen": redondear((ingreso - _d(f["costo"])) / ingreso * 100, 1) if ingreso else None,
            "participacion": redondear(ingreso / total * 100, 1) if total else None,
            "variacion": redondear((ingreso - previo) / previo * 100, 0) if previo else None,
            "stock": stock,
            "minimo": minimo,
            "estado_stock": "quiebre" if stock <= 0 else ("bajo" if stock <= minimo else "ok"),
        })
    return resultado


def ventas_por_linea(empresa, inicio, corte):
    dias = (corte - inicio).days
    inicio_prev, fin_prev = inicio - timedelta(days=dias + 1), inicio - timedelta(days=1)

    def agrupar(desde, hasta):
        return {
            (f["categoria_id"], f["categoria__nombre"] or "Sin categoría"): _d(f["t"])
            for f in FactVenta.objects.filter(empresa=empresa, fecha_id__gte=desde, fecha_id__lte=hasta)
            .values("categoria_id", "categoria__nombre").annotate(t=Sum("ingreso"))
        }

    actual, previo = agrupar(inicio, corte), agrupar(inicio_prev, fin_prev)
    total = sum(actual.values(), CERO)
    filas = []
    for clave in set(actual) | set(previo):
        valor, antes = actual.get(clave, CERO), previo.get(clave, CERO)
        filas.append({
            "linea": clave[1],
            "ventas": valor,
            "participacion": redondear(valor / total * 100, 1) if total else None,
            "anterior": antes,
            "variacion": redondear((valor - antes) / antes * 100, 0) if antes else None,
        })
    return sorted(filas, key=lambda f: f["ventas"], reverse=True)


def mejores_clientes(empresa, inicio, corte, limite=8):
    filas = (
        FactVenta.objects.filter(empresa=empresa, fecha_id__gte=inicio, fecha_id__lte=corte)
        .values("cliente_id", "cliente__nombre")
        .annotate(total=Sum("ingreso"), compras=Count("venta_id", distinct=True))
        .order_by("-total")[:limite]
    )
    return [{"id": f["cliente_id"], "cliente": f["cliente__nombre"], "total": _d(f["total"]), "compras": f["compras"]}
            for f in filas]


def ultimos_seguimientos(empresa):
    """Última gestión registrada por cliente."""
    ultimos = {}
    for s in Seguimiento.objects.filter(empresa=empresa).select_related("usuario").order_by("cliente_id", "-fecha", "-id"):
        ultimos.setdefault(s.cliente_id, s)
    return ultimos


def clasificar_clientes(empresa, corte):
    """Clientes con señales de abandono a la fecha de corte."""
    historico = (
        FactVenta.objects.filter(empresa=empresa, fecha_id__lte=corte)
        .values("cliente_id")
        .annotate(compras=Count("venta_id", distinct=True), primera=Min("fecha"), ultima=Max("fecha"),
                  total=Sum("ingreso"))
    )
    clientes = {c.id: c for c in Cliente.objects.filter(empresa=empresa, activo=True)}
    gestiones = ultimos_seguimientos(empresa)
    grupos = {"dejo": [], "riesgo": [], "no_volvio": []}
    for h in historico:
        cliente = clientes.get(h["cliente_id"])
        if cliente is None:
            continue
        dias = (corte - h["ultima"]).days
        compras = h["compras"]
        intervalo = None
        grupo = None
        if compras >= 2:
            intervalo = max((h["ultima"] - h["primera"]).days / (compras - 1), 7)
            if dias > max(2 * intervalo, 30):
                grupo = "dejo"
            elif dias > max(1.5 * intervalo, 21):
                grupo = "riesgo"
        elif dias > 60:
            grupo = "no_volvio"
        if grupo:
            gestion = gestiones.get(cliente.id)
            grupos[grupo].append({
                "grupo": grupo,
                "cliente": cliente,
                "compras": compras,
                "ultima": h["ultima"],
                "dias": dias,
                "intervalo": round(intervalo) if intervalo else None,
                "total": _d(h["total"]),
                "gestion": gestion if gestion and gestion.fecha >= h["ultima"] else None,
            })
    for lista in grupos.values():
        lista.sort(key=lambda c: c["total"], reverse=True)
    return grupos


def seguimientos_pendientes(empresa, corte, dias_adelante=7):
    limite = corte + timedelta(days=dias_adelante)
    pendientes = [
        s for s in ultimos_seguimientos(empresa).values()
        if s.proxima_fecha and s.proxima_fecha <= limite
    ]
    for s in pendientes:
        s.atraso = (corte - s.proxima_fecha).days
        s.faltan = max(0, -s.atraso)
    return sorted(pendientes, key=lambda s: s.proxima_fecha)


def cotizaciones_abiertas(empresa, corte, limite=8):
    abiertas = list(
        Cotizacion.objects.filter(empresa=empresa, estado__in=[Cotizacion.ENVIADA, Cotizacion.ACEPTADA, Cotizacion.BORRADOR],
                                  fecha__lte=corte)
        .select_related("cliente").order_by("-total")
    )
    for c in abiertas:
        c.dias = (corte - c.fecha).days
        c.vencida = c.vence < corte
    return abiertas[:limite], sum((c.total for c in abiertas), CERO), len(abiertas)


def acciones_comerciales(empresa, avance, resumen, top, clientes, pendientes, abiertas, total_abiertas, corte):
    acciones = []

    def cop(valor):
        return f"$ {redondear(valor, 0):,.0f}".replace(",", ".")

    if avance.meta and not avance.cerrado and avance.estado in {"riesgo", "critico"}:
        acciones.append({
            "estado": avance.estado,
            "etiqueta": "Meta del mes",
            "titulo": "Acelerar el cierre de ventas",
            "texto": (f"Al ritmo actual el mes cerraría en {cop(avance.proyeccion or 0)} frente a una meta de "
                      f"{cop(avance.meta)}. Para cumplirla se necesitan {cop(avance.por_dia)} por día hábil "
                      f"durante los {avance.habiles_restantes} días hábiles que quedan."),
            "items": [f"{c.codigo} · {c.cliente.nombre} — {cop(c.total)}" for c in abiertas[:4]],
            "items_titulo": "Cotizaciones abiertas que se pueden cerrar:" if abiertas else "",
            "enlace": "comercial:cotizaciones",
        })
    elif not avance.meta:
        acciones.append({
            "estado": "sin_meta",
            "etiqueta": "Meta del mes",
            "titulo": "Definir la meta de ventas del mes",
            "texto": "Sin meta no es posible medir el avance ni calcular cuánto falta por vender cada día.",
            "items": [], "items_titulo": "", "enlace": "metas",
        })

    perdidos = clientes["dejo"] + clientes["riesgo"]
    sin_gestion = [c for c in perdidos if c["gestion"] is None]
    if sin_gestion:
        acciones.append({
            "estado": "critico" if clientes["dejo"] else "riesgo",
            "etiqueta": "Clientes",
            "titulo": "Recuperar clientes que dejaron de comprar",
            "texto": (f"{len(sin_gestion)} cliente{'s' if len(sin_gestion) != 1 else ''} con compras frecuentes "
                      "no han vuelto y no tienen una gestión registrada. Empieza por los de mayor valor histórico."),
            "items": [f"{c['cliente'].nombre} — {c['dias']} días sin comprar" for c in sin_gestion[:4]],
            "items_titulo": "",
            "enlace": "comercial:seguimientos",
        })

    atrasados = [s for s in pendientes if s.atraso > 0]
    if atrasados:
        acciones.append({
            "estado": "riesgo",
            "etiqueta": "Seguimiento",
            "titulo": "Ponerse al día con los seguimientos",
            "texto": f"Hay {len(atrasados)} contacto{'s' if len(atrasados) != 1 else ''} programado{'s' if len(atrasados) != 1 else ''} con la fecha vencida.",
            "items": [f"{s.cliente.nombre} — {s.proxima_accion or 'contactar'} ({s.atraso} días de atraso)" for s in atrasados[:4]],
            "items_titulo": "",
            "enlace": "comercial:seguimientos",
        })

    if total_abiertas and abiertas:
        vencidas = [c for c in abiertas if c.vencida]
        acciones.append({
            "estado": "riesgo" if vencidas else "info",
            "etiqueta": "Cotizaciones",
            "titulo": "Hacer seguimiento a las cotizaciones abiertas",
            "texto": (f"Hay {cop(total_abiertas)} en cotizaciones sin convertir"
                      + (f"; {len(vencidas)} ya superaron su vigencia." if vencidas else ".")),
            "items": [f"{c.codigo} · {c.cliente.nombre} — {c.dias} días" for c in abiertas[:4]],
            "items_titulo": "",
            "enlace": "comercial:cotizaciones",
        })

    escasos = [p for p in top if p["estado_stock"] != "ok"]
    if escasos:
        acciones.append({
            "estado": "critico" if any(p["estado_stock"] == "quiebre" for p in escasos) else "riesgo",
            "etiqueta": "Inventario",
            "titulo": "Asegurar inventario de los productos más vendidos",
            "texto": "Estos productos están entre los que más venden y tienen existencias en el mínimo o agotadas.",
            "items": [f"{p['producto']} — quedan {p['stock']} (mínimo {p['minimo']})" for p in escasos[:4]],
            "items_titulo": "",
            "enlace": "inventario:alertas",
        })

    if resumen["variacion_previo"] is not None and resumen["variacion_previo"] < -10:
        acciones.append({
            "estado": "riesgo",
            "etiqueta": "Ventas",
            "titulo": "Revisar la caída frente al mes anterior",
            "texto": (f"Las ventas van {abs(resumen['variacion_previo'])} % por debajo del mismo tramo del mes "
                      "anterior. Ofrecer kits de dotación completos ayuda a subir el valor de cada venta."),
            "items": [], "items_titulo": "",
            "enlace": "comercial:ventas",
        })
    return acciones
