"""Reglas de negocio del inventario.

Todo cambio de existencias pasa por ``registrar_movimiento`` para garantizar
que el stock nunca quede negativo y que el costo unitario se recalcule con el
método de costo promedio ponderado.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from maestros.models import Producto

from .models import MovimientoInventario


class StockInsuficiente(Exception):
    def __init__(self, producto, disponible, solicitado):
        self.producto = producto
        self.disponible = disponible
        self.solicitado = solicitado
        super().__init__(
            f"Stock insuficiente de {producto.nombre}: hay {disponible} y se necesitan {solicitado}."
        )


def _redondear(valor):
    return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@transaction.atomic
def registrar_movimiento(empresa, producto, tipo, cantidad, costo_unitario=None, fecha=None,
                         venta=None, usuario=None, observacion="", origen=MovimientoInventario.OPERACION):
    if cantidad is None or int(cantidad) <= 0:
        raise ValueError("La cantidad debe ser mayor que cero.")
    cantidad = int(cantidad)
    producto = Producto.objects.select_for_update().get(pk=producto.pk, empresa=empresa)
    signo = 1 if tipo in MovimientoInventario.TIPOS_SUMAN else -1
    nuevo_stock = producto.stock_actual + signo * cantidad
    if nuevo_stock < 0:
        raise StockInsuficiente(producto, producto.stock_actual, cantidad)

    if tipo == MovimientoInventario.ENTRADA and costo_unitario is not None:
        costo_unitario = _redondear(costo_unitario)
        existencias = max(producto.stock_actual, 0)
        if nuevo_stock > 0:
            producto.costo_unitario = _redondear(
                (existencias * producto.costo_unitario + cantidad * costo_unitario) / nuevo_stock
            )
        costo_movimiento = costo_unitario
    else:
        costo_movimiento = _redondear(costo_unitario) if costo_unitario is not None else producto.costo_unitario

    producto.stock_actual = nuevo_stock
    producto.save(update_fields=["stock_actual", "costo_unitario"])

    return MovimientoInventario.objects.create(
        empresa=empresa,
        producto=producto,
        tipo=tipo,
        cantidad=cantidad,
        costo_unitario=costo_movimiento,
        fecha=fecha or timezone.localdate(),
        venta=venta,
        usuario=usuario,
        observacion=observacion,
        origen=origen,
    )
