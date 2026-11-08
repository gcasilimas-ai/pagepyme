from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import Empresa
from maestros.models import Producto


class MovimientoInventario(models.Model):
    ENTRADA = "entrada"
    SALIDA_VENTA = "salida_venta"
    AJUSTE_POSITIVO = "ajuste_positivo"
    AJUSTE_NEGATIVO = "ajuste_negativo"
    DEVOLUCION = "devolucion"
    TIPOS = [
        (ENTRADA, "Entrada por compra"),
        (SALIDA_VENTA, "Salida por venta"),
        (AJUSTE_POSITIVO, "Ajuste positivo (conteo físico)"),
        (AJUSTE_NEGATIVO, "Ajuste negativo (conteo, daño o pérdida)"),
        (DEVOLUCION, "Devolución por anulación de venta"),
    ]
    TIPOS_SUMAN = {ENTRADA, AJUSTE_POSITIVO, DEVOLUCION}

    OPERACION = "operacion"
    CONTEO = "conteo"
    HISTORICO = "historico"
    SALDO_INICIAL = "saldo_inicial"
    ORIGENES = [
        (OPERACION, "Operación registrada en la plataforma"),
        (CONTEO, "Inventario actual cargado desde archivo"),
        (HISTORICO, "Carga histórica de ventas o compras"),
        (SALDO_INICIAL, "Saldo inicial reconstruido"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="movimientos")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name="movimientos")
    fecha = models.DateField(default=timezone.localdate, db_index=True)
    tipo = models.CharField(max_length=20, choices=TIPOS)
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    costo_unitario = models.DecimalField(
        "Costo unitario (COP)", max_digits=14, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    venta = models.ForeignKey("comercial.Venta", on_delete=models.CASCADE, null=True, blank=True, related_name="movimientos")
    observacion = models.CharField("Observación", max_length=200, blank=True)
    origen = models.CharField(max_length=20, choices=ORIGENES, default=OPERACION, db_index=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        verbose_name = "movimiento de inventario"
        verbose_name_plural = "movimientos de inventario"

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.producto} · {self.cantidad}"

    @property
    def signo(self):
        return 1 if self.tipo in self.TIPOS_SUMAN else -1

    @property
    def cantidad_neta(self):
        return self.signo * self.cantidad
