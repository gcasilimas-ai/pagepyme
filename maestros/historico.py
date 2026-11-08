"""Carga histórica de ventas y compras, y reconstrucción del inventario.

Una pyme que empieza a usar PAGE-PYME ya tiene meses de ventas registradas en
hojas de cálculo. Para que los indicadores se calculen sobre esa historia, las
ventas y compras se cargan con su fecha original y el inventario se reconstruye
por balance:

    saldo inicial = existencias actuales − compras del histórico + ventas del histórico

El saldo inicial se ubica el día anterior al primer movimiento histórico. Así, la
suma de los movimientos de cada producto coincide siempre con sus existencias
actuales, sin importar el orden en que se carguen los archivos.

Supuestos (se registran en las notas del lote):

* El costo de lo vendido se valora con el costo unitario vigente del producto,
  porque el histórico de costos de la empresa no suele estar disponible.
* Una factura a crédito ya pagada sin fecha de pago se considera pagada en su
  fecha de vencimiento (o hoy, si aún no ha vencido).
* Si la empresa no carga su inventario actual, se asume existencia cero.
"""
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Case, F, IntegerField, Sum, When
from django.utils import timezone

from comercial.models import ItemVenta, Pago, Venta
from inventario.models import MovimientoInventario

from .models import Cliente, Producto

PLAZO_POR_DEFECTO = 30
FECHA_MINIMA = date(2024, 1, 1)  # inicio del calendario de la capa analítica

FORMAS_PAGO = {
    "contado": Venta.CONTADO, "efectivo": Venta.CONTADO, "co": Venta.CONTADO, "cont": Venta.CONTADO,
    "credito": Venta.CREDITO, "cr": Venta.CREDITO, "cred": Venta.CREDITO, "a_credito": Venta.CREDITO,
}


def convertir_fecha(valor):
    """Acepta fechas de Excel y textos dd/mm/aaaa, dd-mm-aaaa o aaaa-mm-dd."""
    if valor in (None, ""):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, (int, float)) and 20000 < float(valor) < 80000:  # número de serie de Excel
        return date(1899, 12, 30) + timedelta(days=int(valor))
    texto = str(valor).strip().split(" ")[0]
    for formato in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise ValueError(f"'{valor}' no es una fecha válida (usa dd/mm/aaaa)")


def _fecha(crudo, campo, mensajes, requerido=False):
    try:
        valor = convertir_fecha(crudo.get(campo))
    except ValueError as exc:
        mensajes.append(f"{campo}: {exc}")
        return None
    if valor is None:
        if requerido:
            mensajes.append(f"{campo}: es obligatoria")
        return None
    hoy = timezone.localdate()
    if valor > hoy:
        mensajes.append(f"{campo}: {valor:%d/%m/%Y} es una fecha futura")
        return None
    if valor < FECHA_MINIMA:
        mensajes.append(f"{campo}: la plataforma admite datos desde el 1 de enero de 2024")
        return None
    return valor


def contexto_ventas(empresa):
    return {
        "vistos": {},
        "productos": {p.codigo.upper(): p for p in Producto.objects.filter(empresa=empresa)},
        "clientes": {c.documento: c for c in Cliente.objects.filter(empresa=empresa)},
        "facturas": set(
            Venta.objects.filter(empresa=empresa).exclude(documento_externo="").values_list("documento_externo", flat=True)
        ),
    }


def validar_venta(crudo, contexto, utilidades):
    limpiar, numero = utilidades["limpiar"], utilidades["numero"]
    mensajes, advertencias = [], []
    fecha = _fecha(crudo, "fecha", mensajes, requerido=True)
    factura = limpiar(crudo.get("factura")).upper()
    documento = limpiar(crudo.get("documento_cliente")).replace(" ", "").replace(".", "")
    nombre_cliente = limpiar(crudo.get("nombre_cliente"))
    codigo = limpiar(crudo.get("codigo")).upper()
    if not factura:
        mensajes.append("factura: es obligatoria")
    elif factura in contexto["facturas"]:
        mensajes.append(f"factura: la {factura} ya se cargó en una carga anterior")
    if not documento:
        mensajes.append("documento_cliente: es obligatorio")
    elif documento not in contexto["clientes"]:
        if nombre_cliente:
            advertencias.append(f"el cliente {documento} no existe; se creará como «{nombre_cliente}»")
        else:
            mensajes.append(f"documento_cliente: el cliente {documento} no existe; impórtalo primero o agrega la columna nombre_cliente")
    if not codigo:
        mensajes.append("codigo: es obligatorio")
    elif codigo not in contexto["productos"]:
        mensajes.append(f"codigo: el producto {codigo} no existe; impórtalo primero")
    cantidad = numero(crudo, "cantidad", mensajes, requerido=True, entero=True, minimo=Decimal("1"))
    precio = numero(crudo, "precio_unitario", mensajes, requerido=True)

    texto_forma = utilidades["clave"](crudo.get("forma_pago"))
    forma = FORMAS_PAGO.get(texto_forma) if texto_forma else Venta.CONTADO
    if forma is None:
        advertencias.append(f"forma de pago «{limpiar(crudo.get('forma_pago'))}» no reconocida; se tomará como contado")
        forma = Venta.CONTADO
    saldo = numero(crudo, "saldo_pendiente", mensajes) if crudo.get("saldo_pendiente") not in (None, "") else None
    fecha_pago = _fecha(crudo, "fecha_pago", mensajes)
    if saldo is not None and Decimal(saldo) > 0 and forma == Venta.CONTADO:
        advertencias.append("venta de contado con saldo pendiente; se tomará como venta a crédito")
        forma = Venta.CREDITO
    if fecha and fecha_pago and fecha_pago < fecha:
        mensajes.append("fecha_pago: es anterior a la fecha de la venta")

    datos = {
        "fecha": fecha.isoformat() if fecha else None, "factura": factura, "documento_cliente": documento,
        "nombre_cliente": nombre_cliente, "codigo": codigo, "cantidad": cantidad, "precio_unitario": precio,
        "forma_pago": forma, "saldo_pendiente": saldo, "fecha_pago": fecha_pago.isoformat() if fecha_pago else None,
        "accion": "crear",
    }
    return mensajes, advertencias, datos


def consolidar_facturas(validas, errores):
    """Revisa que las filas de una misma factura sean coherentes y calcula su total."""
    grupos = OrderedDict()
    for fila in validas:
        grupos.setdefault(fila["datos"]["factura"], []).append(fila)
    conservadas = []
    for factura, filas in grupos.items():
        primera = filas[0]["datos"]
        problemas = []
        for campo, nombre in (("fecha", "fecha"), ("documento_cliente", "cliente")):
            if len({f["datos"][campo] for f in filas}) > 1:
                problemas.append(f"la factura {factura} tiene filas con distinta {nombre}")
        formas = {f["datos"]["forma_pago"] for f in filas}
        saldos = {f["datos"]["saldo_pendiente"] for f in filas if f["datos"]["saldo_pendiente"] is not None}
        pagos = {f["datos"]["fecha_pago"] for f in filas if f["datos"]["fecha_pago"]}
        total = sum(Decimal(f["datos"]["precio_unitario"]) * f["datos"]["cantidad"] for f in filas)
        saldo = max((Decimal(s) for s in saldos), default=None)
        if saldo is not None and saldo > total:
            problemas.append(f"el saldo pendiente de la factura {factura} ({saldo:.0f}) supera su total ({total:.0f})")
        if problemas:
            for f in filas:
                errores.append({"fila": f["fila"], "mensajes": problemas,
                                "valores": {k: str(v if v is not None else "") for k, v in f["datos"].items() if k != "accion"}})
            continue
        forma = Venta.CREDITO if Venta.CREDITO in formas or (saldo or 0) > 0 else Venta.CONTADO
        for f in filas:
            f["datos"].update({
                "forma_pago": forma,
                "saldo_pendiente": str(saldo) if saldo is not None else None,
                "fecha_pago": sorted(pagos)[-1] if pagos else None,
                "total_factura": str(total),
            })
            if len(saldos) > 1:
                f["advertencias"].append("la factura tiene saldos distintos en sus filas; se tomó el mayor")
            conservadas.append(f)
    errores.sort(key=lambda e: e["fila"])
    return conservadas


def contexto_compras(empresa):
    return {"vistos": {}, "productos": {p.codigo.upper(): p for p in Producto.objects.filter(empresa=empresa)}}


def validar_compra(crudo, contexto, utilidades):
    limpiar, numero = utilidades["limpiar"], utilidades["numero"]
    mensajes, advertencias = [], []
    fecha = _fecha(crudo, "fecha", mensajes, requerido=True)
    codigo = limpiar(crudo.get("codigo")).upper()
    if not codigo:
        mensajes.append("codigo: es obligatorio")
    elif codigo not in contexto["productos"]:
        mensajes.append(f"codigo: el producto {codigo} no existe; impórtalo primero")
    cantidad = numero(crudo, "cantidad", mensajes, requerido=True, entero=True, minimo=Decimal("1"))
    costo = numero(crudo, "costo_unitario", mensajes, requerido=True)
    if costo is not None and Decimal(costo) == 0:
        advertencias.append("costo en cero")
    datos = {
        "fecha": fecha.isoformat() if fecha else None, "codigo": codigo, "cantidad": cantidad,
        "costo_unitario": costo, "proveedor": limpiar(crudo.get("proveedor")),
        "documento": limpiar(crudo.get("documento")), "accion": "entrada",
    }
    return mensajes, advertencias, datos


# --------------------------------------------------------------------------- aplicación

def aplicar_ventas(lote, usuario, siguiente_numero):
    empresa = lote.empresa
    hoy = timezone.localdate()
    productos = {p.codigo.upper(): p for p in Producto.objects.filter(empresa=empresa)}
    clientes = {c.documento: c for c in Cliente.objects.filter(empresa=empresa)}
    grupos = OrderedDict()
    for fila in sorted(lote.datos, key=lambda f: (f["datos"]["fecha"], f["datos"]["factura"], f["fila"])):
        grupos.setdefault(fila["datos"]["factura"], []).append(fila["datos"])

    clientes_creados = 0
    for d in (filas[0] for filas in grupos.values()):
        if d["documento_cliente"] not in clientes:
            clientes[d["documento_cliente"]] = Cliente.objects.create(
                empresa=empresa, documento=d["documento_cliente"], nombre=d["nombre_cliente"] or d["documento_cliente"],
            )
            clientes_creados += 1

    numero = siguiente_numero
    ventas = []
    for factura, filas in grupos.items():
        d = filas[0]
        cliente = clientes[d["documento_cliente"]]
        fecha = date.fromisoformat(d["fecha"])
        vence = None
        if d["forma_pago"] == Venta.CREDITO:
            vence = fecha + timedelta(days=cliente.plazo_credito_dias or PLAZO_POR_DEFECTO)
        ventas.append(Venta(
            empresa=empresa, numero=numero, cliente=cliente, fecha=fecha, forma_pago=d["forma_pago"],
            fecha_vencimiento=vence, documento_externo=factura, total=Decimal(d["total_factura"]),
            observaciones=f"Carga histórica (lote {lote.pk})", creado_por=usuario,
        ))
        numero += 1
    ventas = Venta.objects.bulk_create(ventas)

    items, movimientos, pagos = [], [], []
    pagos_supuestos = 0
    for venta, filas in zip(ventas, grupos.values()):
        for d in filas:
            producto = productos[d["codigo"]]
            items.append(ItemVenta(
                venta=venta, producto=producto, cantidad=d["cantidad"],
                precio_unitario=Decimal(d["precio_unitario"]), costo_unitario=producto.costo_unitario,
            ))
            movimientos.append(MovimientoInventario(
                empresa=empresa, producto=producto, tipo=MovimientoInventario.SALIDA_VENTA, cantidad=d["cantidad"],
                costo_unitario=producto.costo_unitario, fecha=venta.fecha, venta=venta, usuario=usuario,
                observacion=f"Histórico · factura {venta.documento_externo}", origen=MovimientoInventario.HISTORICO,
            ))
        d = filas[0]
        if venta.forma_pago == Venta.CONTADO:
            pagos.append(Pago(venta=venta, fecha=venta.fecha, valor=venta.total, referencia="Pago de contado",
                              registrado_por=usuario))
            continue
        saldo = min(Decimal(d["saldo_pendiente"] or 0), venta.total)
        pagado = venta.total - saldo
        if pagado > 0:
            if d["fecha_pago"]:
                fecha_pago = date.fromisoformat(d["fecha_pago"])
            else:
                fecha_pago = min(venta.fecha_vencimiento, hoy)
                pagos_supuestos += 1
            pagos.append(Pago(venta=venta, fecha=fecha_pago, valor=pagado, medio="otro",
                              referencia="Pago histórico", registrado_por=usuario))
    ItemVenta.objects.bulk_create(items)
    MovimientoInventario.objects.bulk_create(movimientos)
    Pago.objects.bulk_create(pagos)

    credito = [v for v in ventas if v.forma_pago == Venta.CREDITO]
    notas = [
        f"Se cargaron {len(ventas)} facturas con {len(items)} líneas, del "
        f"{min(v.fecha for v in ventas):%d/%m/%Y} al {max(v.fecha for v in ventas):%d/%m/%Y}.",
        f"{len(credito)} facturas a crédito y {len(ventas) - len(credito)} de contado.",
        "El costo de lo vendido se valoró con el costo unitario vigente de cada producto.",
    ]
    if clientes_creados:
        notas.append(f"Se crearon {clientes_creados} clientes que no estaban en el maestro.")
    if pagos_supuestos:
        notas.append(f"{pagos_supuestos} facturas a crédito pagadas no traían fecha de pago; se asumió el pago en su fecha de vencimiento.")
    notas += reconstruir_inventario(empresa, {m.producto_id for m in movimientos})
    return len(ventas), notas


def aplicar_compras(lote, usuario):
    empresa = lote.empresa
    productos = {p.codigo.upper(): p for p in Producto.objects.filter(empresa=empresa)}
    movimientos = []
    for fila in lote.datos:
        d = fila["datos"]
        detalle = " · ".join(x for x in (d.get("proveedor"), d.get("documento")) if x)
        movimientos.append(MovimientoInventario(
            empresa=empresa, producto=productos[d["codigo"]], tipo=MovimientoInventario.ENTRADA,
            cantidad=d["cantidad"], costo_unitario=Decimal(d["costo_unitario"]), fecha=date.fromisoformat(d["fecha"]),
            usuario=usuario, observacion=f"Histórico · compra {detalle}".strip()[:200], origen=MovimientoInventario.HISTORICO,
        ))
    MovimientoInventario.objects.bulk_create(movimientos)
    fechas = [m.fecha for m in movimientos]
    notas = [f"Se cargaron {len(movimientos)} compras del {min(fechas):%d/%m/%Y} al {max(fechas):%d/%m/%Y}."]
    notas += reconstruir_inventario(empresa, {m.producto_id for m in movimientos})
    return len(movimientos), notas


def _neto(qs):
    return qs.aggregate(t=Sum(Case(
        When(tipo__in=MovimientoInventario.TIPOS_SUMAN, then=F("cantidad")),
        default=-F("cantidad"), output_field=IntegerField(),
    )))["t"] or 0


def reconstruir_inventario(empresa, producto_ids=None):
    """Ubica el saldo inicial de cada producto con histórico para que sus movimientos sumen sus existencias."""
    hoy = timezone.localdate()
    con_historico = MovimientoInventario.objects.filter(empresa=empresa, origen=MovimientoInventario.HISTORICO)
    if producto_ids is not None:
        con_historico = con_historico.filter(producto_id__in=producto_ids)
    ids = set(con_historico.values_list("producto_id", flat=True))
    ajustados, sin_conteo, negativos = [], 0, 0
    for producto in Producto.objects.select_for_update().filter(pk__in=ids).order_by("pk"):
        movs = MovimientoInventario.objects.filter(empresa=empresa, producto=producto)
        conteos = list(movs.filter(origen=MovimientoInventario.CONTEO).order_by("fecha", "id"))
        previos = list(movs.filter(origen=MovimientoInventario.SALDO_INICIAL))
        if not conteos and not previos and producto.stock_actual == 0:
            sin_conteo += 1
        costo = conteos[-1].costo_unitario if conteos else producto.costo_unitario
        movs.filter(pk__in=[m.pk for m in conteos + previos]).delete()
        restantes = MovimientoInventario.objects.filter(empresa=empresa, producto=producto)
        apertura = producto.stock_actual - _neto(restantes)
        primera = restantes.order_by("fecha").values_list("fecha", flat=True).first() or hoy
        if apertura > 0:
            MovimientoInventario.objects.create(
                empresa=empresa, producto=producto, tipo=MovimientoInventario.ENTRADA, cantidad=apertura,
                costo_unitario=costo, fecha=max(primera - timedelta(days=1), FECHA_MINIMA),
                origen=MovimientoInventario.SALDO_INICIAL,
                observacion="Saldo inicial reconstruido: existencias actuales − compras + ventas del histórico",
            )
        elif apertura < 0:
            ajustados.append(producto.codigo)
            MovimientoInventario.objects.create(
                empresa=empresa, producto=producto, tipo=MovimientoInventario.AJUSTE_NEGATIVO, cantidad=-apertura,
                costo_unitario=costo, fecha=hoy, origen=MovimientoInventario.SALDO_INICIAL,
                observacion="Ajuste de conciliación: las compras del histórico superan ventas + existencias actuales",
            )
        # Fechas en que el inventario reconstruido queda negativo (compras registradas después de las ventas)
        saldo = 0
        for fecha_mov, neto in _netos_por_fecha(empresa, producto):
            saldo += neto
            if saldo < 0:
                negativos += 1
                break
    notas = []
    if ids:
        notas.append(f"Inventario reconstruido por balance para {len(ids)} productos con movimientos históricos.")
    if sin_conteo:
        notas.append(f"{sin_conteo} productos aún no tienen inventario actual cargado; se asume existencia cero. "
                     "Carga el inventario actual para completar la reconstrucción.")
    if ajustados:
        notas.append(f"En {len(ajustados)} productos las compras superan ventas más existencias (posible merma o ventas "
                     f"no registradas); se registró un ajuste negativo de conciliación: {', '.join(ajustados[:10])}"
                     f"{'…' if len(ajustados) > 10 else ''}.")
    if negativos:
        notas.append(f"En {negativos} productos el inventario reconstruido queda negativo en alguna fecha "
                     "(ventas registradas antes de la compra correspondiente).")
    return notas


def _netos_por_fecha(empresa, producto):
    filas = defaultdict(int)
    for tipo, cantidad, fecha in MovimientoInventario.objects.filter(empresa=empresa, producto=producto).values_list(
            "tipo", "cantidad", "fecha"):
        filas[fecha] += cantidad if tipo in MovimientoInventario.TIPOS_SUMAN else -cantidad
    return sorted(filas.items())
