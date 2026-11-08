from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from core.models import Empresa


class Categoria(models.Model):
    """Línea de producto. Puede tener su propia meta de margen."""

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="categorias")
    nombre = models.CharField(max_length=100)
    meta_margen = models.DecimalField(
        "Meta de margen de la línea (%)", max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Si se deja vacía, se usa la meta general de la empresa.",
    )

    class Meta:
        ordering = ["nombre"]
        constraints = [models.UniqueConstraint(fields=["empresa", "nombre"], name="categoria_unica_por_empresa")]
        verbose_name = "categoría"

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    UNIDADES = [
        ("unidad", "Unidad"),
        ("par", "Par"),
        ("caja", "Caja"),
        ("paquete", "Paquete"),
        ("rollo", "Rollo"),
        ("kit", "Kit"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="productos")
    codigo = models.CharField("Código", max_length=40)
    nombre = models.CharField(max_length=150)
    categoria = models.ForeignKey(Categoria, on_delete=models.SET_NULL, null=True, blank=True, related_name="productos")
    unidad = models.CharField(max_length=20, choices=UNIDADES, default="unidad")
    costo_unitario = models.DecimalField(
        "Costo unitario (COP)", max_digits=14, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Se actualiza con costo promedio ponderado en cada entrada de inventario.",
    )
    precio_venta = models.DecimalField(
        "Precio de venta (COP)", max_digits=14, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    stock_minimo = models.PositiveIntegerField("Stock mínimo", default=0)
    stock_actual = models.IntegerField("Stock actual", default=0, editable=False)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]
        constraints = [models.UniqueConstraint(fields=["empresa", "codigo"], name="producto_codigo_unico_por_empresa")]
        verbose_name = "producto"

    def __str__(self):
        return f"{self.codigo} · {self.nombre}"

    @property
    def margen_unitario_pct(self):
        if self.precio_venta and self.precio_venta > 0:
            return (self.precio_venta - self.costo_unitario) / self.precio_venta * 100
        return None

    @property
    def estado_stock(self):
        if self.stock_actual <= 0:
            return "quiebre"
        if self.stock_actual <= self.stock_minimo:
            return "bajo"
        return "ok"


class Cliente(models.Model):
    TIPOS_DOCUMENTO = [("NIT", "NIT"), ("CC", "Cédula de ciudadanía"), ("CE", "Cédula de extranjería")]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="clientes")
    tipo_documento = models.CharField(max_length=5, choices=TIPOS_DOCUMENTO, default="NIT")
    documento = models.CharField(max_length=30)
    nombre = models.CharField("Nombre o razón social", max_length=150)
    ciudad = models.CharField(max_length=80, blank=True)
    telefono = models.CharField("Teléfono", max_length=40, blank=True)
    email = models.EmailField("Correo electrónico", blank=True)
    plazo_credito_dias = models.PositiveIntegerField(
        "Plazo de crédito (días)", default=0, help_text="0 si el cliente solo compra de contado."
    )
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]
        constraints = [models.UniqueConstraint(fields=["empresa", "documento"], name="cliente_documento_unico_por_empresa")]
        verbose_name = "cliente"

    def __str__(self):
        return self.nombre


class LoteImportacion(models.Model):
    """Registro de cada carga de datos desde Excel/CSV (trazabilidad del proceso ETL)."""

    TIPOS = [
        ("productos", "Productos"), ("clientes", "Clientes"), ("ventas", "Ventas históricas"),
        ("compras", "Compras históricas"), ("inventario", "Inventario actual"),
    ]
    ESTADOS = [("pendiente", "Pendiente de confirmar"), ("aplicado", "Aplicado"), ("descartado", "Descartado")]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="lotes_importacion")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    tipo = models.CharField(max_length=20, choices=TIPOS)
    archivo_nombre = models.CharField(max_length=200)
    fecha = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=ESTADOS, default="pendiente")
    total_filas = models.PositiveIntegerField(default=0)
    filas_validas = models.PositiveIntegerField(default=0)
    filas_con_error = models.PositiveIntegerField(default=0)
    creados = models.PositiveIntegerField(default=0)
    actualizados = models.PositiveIntegerField(default=0)
    errores = models.JSONField(default=list, blank=True)
    datos = models.JSONField(default=list, blank=True)
    notas = models.JSONField(default=list, blank=True, help_text="Supuestos y hallazgos registrados al aplicar la carga.")

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "lote de importación"
        verbose_name_plural = "lotes de importación"

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.archivo_nombre}"
