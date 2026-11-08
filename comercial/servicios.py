"""Reglas de negocio del módulo comercial."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from inventario.models import MovimientoInventario
from inventario.servicios import StockInsuficiente, registrar_movimiento
from maestros.models import Producto

from .models import Cotizacion, ItemCotizacion, ItemVenta, Pago, Venta

PLAZO_CREDITO_POR_DEFECTO = 30


def _siguiente_numero(modelo, empresa):
    ultimo = modelo.objects.select_for_update().filter(empresa=empresa).aggregate(m=Max("numero"))["m"]
    return (ultimo or 0) + 1


def _validar_items(empresa, items):
    if not items:
        raise ValidationError("Agrega al menos un producto.")
    for producto, cantidad, precio in items:
        if producto.empresa_id != empresa.id:
            raise ValidationError("El producto no pertenece a la empresa.")
        if cantidad <= 0:
            raise ValidationError("Las cantidades deben ser mayores que cero.")
        if precio < 0:
            raise ValidationError("Los precios no pueden ser negativos.")


@transaction.atomic
def crear_cotizacion(empresa, cliente, items, fecha=None, vigencia_dias=15, observaciones="", usuario=None):
    _validar_items(empresa, items)
    cotizacion = Cotizacion.objects.create(
        empresa=empresa,
        numero=_siguiente_numero(Cotizacion, empresa),
        cliente=cliente,
        fecha=fecha or timezone.localdate(),
        vigencia_dias=vigencia_dias,
        observaciones=observaciones,
        creado_por=usuario,
    )
    total = Decimal("0")
    for producto, cantidad, precio in items:
        ItemCotizacion.objects.create(cotizacion=cotizacion, producto=producto, cantidad=cantidad, precio_unitario=precio)
        total += cantidad * precio
    cotizacion.total = total
    cotizacion.save(update_fields=["total"])
    return cotizacion


@transaction.atomic
def crear_venta(empresa, cliente, items, forma_pago=Venta.CONTADO, fecha=None, usuario=None,
                cotizacion=None, observaciones="", medio_pago="efectivo"):
    """Emite una venta, descuenta el inventario y, si es de contado, registra el pago.

    ``items`` es una lista de tuplas ``(producto, cantidad, precio_unitario)``.
    """
    _validar_items(empresa, items)
    fecha = fecha or timezone.localdate()

    # Validación previa de stock agregando cantidades del mismo producto.
    requerido = defaultdict(int)
    for producto, cantidad, _ in items:
        requerido[producto.pk] += cantidad
    for producto_id, cantidad in requerido.items():
        producto = Producto.objects.select_for_update().get(pk=producto_id)
        if producto.stock_actual < cantidad:
            raise StockInsuficiente(producto, producto.stock_actual, cantidad)

    fecha_vencimiento = None
    if forma_pago == Venta.CREDITO:
        plazo = cliente.plazo_credito_dias or PLAZO_CREDITO_POR_DEFECTO
        fecha_vencimiento = fecha + timedelta(days=plazo)

    venta = Venta.objects.create(
        empresa=empresa,
        numero=_siguiente_numero(Venta, empresa),
        cliente=cliente,
        fecha=fecha,
        forma_pago=forma_pago,
        fecha_vencimiento=fecha_vencimiento,
        cotizacion=cotizacion,
        observaciones=observaciones,
        creado_por=usuario,
    )
    total = Decimal("0")
    for producto, cantidad, precio in items:
        producto.refresh_from_db(fields=["costo_unitario", "stock_actual"])
        ItemVenta.objects.create(
            venta=venta, producto=producto, cantidad=cantidad,
            precio_unitario=precio, costo_unitario=producto.costo_unitario,
        )
        registrar_movimiento(
            empresa, producto, MovimientoInventario.SALIDA_VENTA, cantidad,
            fecha=fecha, venta=venta, usuario=usuario, observacion=venta.codigo,
        )
        total += cantidad * precio
    venta.total = total
    venta.save(update_fields=["total"])

    if forma_pago == Venta.CONTADO and total > 0:
        Pago.objects.create(venta=venta, fecha=fecha, valor=total, medio=medio_pago,
                            referencia="Pago de contado", registrado_por=usuario)

    if cotizacion is not None:
        cotizacion.estado = Cotizacion.CONVERTIDA
        cotizacion.save(update_fields=["estado"])
    return venta


@transaction.atomic
def convertir_cotizacion(cotizacion, forma_pago, usuario=None, fecha=None):
    if cotizacion.estado in {Cotizacion.CONVERTIDA, Cotizacion.RECHAZADA}:
        raise ValidationError("Esta cotización ya no se puede convertir en venta.")
    items = [(i.producto, i.cantidad, i.precio_unitario) for i in cotizacion.items.select_related("producto")]
    return crear_venta(
        cotizacion.empresa, cotizacion.cliente, items, forma_pago=forma_pago, fecha=fecha,
        usuario=usuario, cotizacion=cotizacion, observaciones=f"Generada desde {cotizacion.codigo}",
    )


@transaction.atomic
def anular_venta(venta, usuario=None, motivo=""):
    if venta.estado == Venta.ANULADA:
        raise ValidationError("La venta ya está anulada.")
    if venta.documento_externo:
        raise ValidationError(
            "Las ventas de la carga histórica no se anulan desde aquí: corrige el archivo de origen y vuelve a cargarlo."
        )
    abonos = venta.pagos.exclude(referencia="Pago de contado")
    if venta.forma_pago == Venta.CREDITO and abonos.exists():
        raise ValidationError("La venta tiene abonos registrados; elimínalos antes de anularla.")
    for item in venta.items.select_related("producto"):
        registrar_movimiento(
            venta.empresa, item.producto, MovimientoInventario.DEVOLUCION, item.cantidad,
            costo_unitario=item.costo_unitario, usuario=usuario, fecha=venta.fecha, venta=venta,
            observacion=f"Anulación {venta.codigo}",
        )
    venta.pagos.all().delete()
    venta.estado = Venta.ANULADA
    venta.observaciones = (venta.observaciones + f"\nAnulada: {motivo}").strip()
    venta.save(update_fields=["estado", "observaciones"])
    return venta


@transaction.atomic
def registrar_abono(venta, valor, fecha=None, medio="efectivo", referencia="", usuario=None):
    venta = Venta.objects.select_for_update().get(pk=venta.pk)
    if venta.estado != Venta.EMITIDA:
        raise ValidationError("No se pueden registrar abonos a una venta anulada.")
    if venta.forma_pago != Venta.CREDITO:
        raise ValidationError("Las ventas de contado ya están pagadas.")
    valor = Decimal(valor)
    if valor <= 0:
        raise ValidationError("El valor del abono debe ser mayor que cero.")
    if valor > venta.saldo:
        raise ValidationError(f"El abono supera el saldo pendiente ({venta.saldo:.0f}).")
    return Pago.objects.create(
        venta=venta, fecha=fecha or timezone.localdate(), valor=valor, medio=medio,
        referencia=referencia, registrado_por=usuario,
    )
