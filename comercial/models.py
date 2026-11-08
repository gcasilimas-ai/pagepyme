from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from core.models import Empresa
from maestros.models import Cliente, Producto


class Cotizacion(models.Model):
    BORRADOR = "borrador"
    ENVIADA = "enviada"
    ACEPTADA = "aceptada"
    RECHAZADA = "rechazada"
    CONVERTIDA = "convertida"
    ESTADOS = [
        (BORRADOR, "Borrador"),
        (ENVIADA, "Enviada al cliente"),
        (ACEPTADA, "Aceptada"),
        (RECHAZADA, "Rechazada"),
        (CONVERTIDA, "Convertida en venta"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="cotizaciones")
    numero = models.PositiveIntegerField("Número")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="cotizaciones")
    fecha = models.DateField(default=timezone.localdate)
    vigencia_dias = models.PositiveIntegerField("Vigencia (días)", default=15)
    estado = models.CharField(max_length=20, choices=ESTADOS, default=BORRADOR)
    observaciones = models.TextField(blank=True)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-numero"]
        constraints = [models.UniqueConstraint(fields=["empresa", "numero"], name="cotizacion_numero_unico")]
        verbose_name = "cotización"
        verbose_name_plural = "cotizaciones"

    def __str__(self):
        return f"COT-{self.numero:05d}"

    @property
    def codigo(self):
        return f"COT-{self.numero:05d}"

    @property
    def vence(self):
        return self.fecha + timedelta(days=self.vigencia_dias)


class ItemCotizacion(models.Model):
    cotizacion = models.ForeignKey(Cotizacion, on_delete=models.CASCADE, related_name="items")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    precio_unitario = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])

    @property
    def subtotal(self):
        return self.cantidad * self.precio_unitario


class Venta(models.Model):
    CONTADO = "contado"
    CREDITO = "credito"
    FORMAS_PAGO = [(CONTADO, "Contado"), (CREDITO, "Crédito")]
    EMITIDA = "emitida"
    ANULADA = "anulada"
    ESTADOS = [(EMITIDA, "Emitida"), (ANULADA, "Anulada")]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="ventas")
    numero = models.PositiveIntegerField("Número")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="ventas")
    fecha = models.DateField(default=timezone.localdate, db_index=True)
    forma_pago = models.CharField("Forma de pago", max_length=10, choices=FORMAS_PAGO, default=CONTADO)
    fecha_vencimiento = models.DateField("Fecha de vencimiento", null=True, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default=EMITIDA)
    cotizacion = models.OneToOneField(Cotizacion, on_delete=models.SET_NULL, null=True, blank=True, related_name="venta")
    observaciones = models.TextField(blank=True)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-numero"]
        constraints = [models.UniqueConstraint(fields=["empresa", "numero"], name="venta_numero_unico")]
        verbose_name = "venta"

    def __str__(self):
        return self.codigo

    @property
    def codigo(self):
        return f"VTA-{self.numero:05d}"

    def pagado_hasta(self, fecha=None):
        qs = self.pagos.all()
        if fecha:
            qs = qs.filter(fecha__lte=fecha)
        return qs.aggregate(t=Sum("valor"))["t"] or Decimal("0")

    @property
    def pagado(self):
        return self.pagado_hasta()

    @property
    def saldo(self):
        if self.estado == self.ANULADA:
            return Decimal("0")
        return self.total - self.pagado

    def dias_vencida(self, fecha=None):
        fecha = fecha or timezone.localdate()
        if self.forma_pago != self.CREDITO or not self.fecha_vencimiento:
            return 0
        return max(0, (fecha - self.fecha_vencimiento).days)


class ItemVenta(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name="items")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name="items_venta")
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    precio_unitario = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    costo_unitario = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0"),
        help_text="Costo promedio del producto en el momento de la venta.",
    )

    @property
    def subtotal(self):
        return self.cantidad * self.precio_unitario


class Pago(models.Model):
    MEDIOS = [
        ("efectivo", "Efectivo"),
        ("transferencia", "Transferencia"),
        ("consignacion", "Consignación"),
        ("otro", "Otro"),
    ]

    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name="pagos")
    fecha = models.DateField(default=timezone.localdate, db_index=True)
    valor = models.DecimalField(max_digits=16, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    medio = models.CharField(max_length=20, choices=MEDIOS, default="efectivo")
    referencia = models.CharField(max_length=80, blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        verbose_name = "abono"

    def __str__(self):
        return f"Abono {self.valor} a {self.venta}"


class MetaComercial(models.Model):
    """Meta de ventas de la empresa para un mes (en pesos, antes de impuestos)."""

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="metas_comerciales")
    anio = models.PositiveSmallIntegerField("Año")
    mes = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    valor = models.DecimalField("Meta de ventas (COP)", max_digits=16, decimal_places=0,
                                validators=[MinValueValidator(Decimal("1"))])
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["anio", "mes"]
        constraints = [models.UniqueConstraint(fields=["empresa", "anio", "mes"], name="meta_comercial_unica")]
        verbose_name = "meta comercial"
        verbose_name_plural = "metas comerciales"

    def __str__(self):
        return f"{self.empresa} {self.anio}-{self.mes:02d}: {self.valor}"


class Seguimiento(models.Model):
    """Gestión comercial con un cliente (llamada, visita, mensaje) y su próximo paso."""

    CANALES = [
        ("llamada", "Llamada"),
        ("whatsapp", "WhatsApp"),
        ("visita", "Visita"),
        ("correo", "Correo electrónico"),
    ]
    RESULTADOS = [
        ("interesado", "Interesado en comprar"),
        ("cotizacion", "Pidió cotización"),
        ("compra", "Confirmó pedido"),
        ("sin_respuesta", "No respondió"),
        ("no_interesado", "No está interesado por ahora"),
        ("reclamo", "Tiene un reclamo o inconformidad"),
        ("otro", "Otro"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="seguimientos")
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="seguimientos")
    fecha = models.DateField("Fecha de la gestión", default=timezone.localdate, db_index=True)
    canal = models.CharField(max_length=20, choices=CANALES, default="llamada")
    resultado = models.CharField(max_length=20, choices=RESULTADOS)
    nota = models.TextField("Detalle", blank=True)
    proxima_fecha = models.DateField("Próximo contacto", null=True, blank=True)
    proxima_accion = models.CharField("Próxima acción", max_length=150, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        verbose_name = "seguimiento"

    def __str__(self):
        return f"{self.cliente} · {self.get_canal_display()} · {self.fecha}"
