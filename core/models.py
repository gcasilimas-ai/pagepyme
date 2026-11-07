from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Empresa(models.Model):
    """Pyme participante. Cada registro operativo pertenece a una empresa."""

    nombre = models.CharField(max_length=150)
    nit = models.CharField("NIT", max_length=30, blank=True)
    ciudad = models.CharField(max_length=80, default="Neiva")
    departamento = models.CharField(max_length=80, default="Huila")
    sector = models.CharField(
        max_length=150, default="Comercialización de elementos de protección personal"
    )
    numero_empleados = models.PositiveIntegerField("Número de empleados", null=True, blank=True)
    anios_operacion = models.PositiveIntegerField("Años de operación", null=True, blank=True)
    herramienta_previa = models.CharField(
        "Herramienta de gestión usada antes del piloto", max_length=150, blank=True,
        help_text="Por ejemplo: hojas de cálculo en Excel, cuaderno, software contable.",
    )
    fecha_vinculacion = models.DateField("Fecha de vinculación al piloto", null=True, blank=True)
    es_demo = models.BooleanField(
        "Empresa de demostración", default=False,
        help_text="Marca las empresas con datos ficticios. Se excluyen de los indicadores del piloto.",
    )
    activa = models.BooleanField(default=True)

    # Metas de los indicadores de negocio (configurables por empresa)
    meta_rotacion = models.DecimalField(
        "Meta de rotación de inventario (veces al año)", max_digits=6, decimal_places=2,
        default=Decimal("4.00"), validators=[MinValueValidator(Decimal("0.01"))],
    )
    meta_margen = models.DecimalField(
        "Meta de margen de contribución (%)", max_digits=5, decimal_places=2,
        default=Decimal("30.00"), validators=[MinValueValidator(Decimal("0.01"))],
    )
    meta_dso = models.DecimalField(
        "Meta de días de cartera (DSO, máximo)", max_digits=6, decimal_places=1,
        default=Decimal("30.0"), validators=[MinValueValidator(Decimal("0.1"))],
    )
    meta_quiebres = models.DecimalField(
        "Meta de quiebres de stock (%, máximo)", max_digits=5, decimal_places=2,
        default=Decimal("5.00"), validators=[MinValueValidator(Decimal("0.01"))],
    )
    meta_recurrentes = models.DecimalField(
        "Meta de clientes recurrentes (%)", max_digits=5, decimal_places=2,
        default=Decimal("40.00"), validators=[MinValueValidator(Decimal("0.01"))],
    )
    meta_ticket = models.DecimalField(
        "Meta de ticket promedio (COP)", max_digits=14, decimal_places=0,
        null=True, blank=True, validators=[MinValueValidator(Decimal("1"))],
        help_text="Valor mínimo esperado por venta. Si se deja vacío, el indicador se muestra sin meta.",
    )

    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["es_demo", "nombre"]
        verbose_name = "empresa"

    def __str__(self):
        return self.nombre


class Perfil(models.Model):
    GERENTE = "gerente"
    COMERCIAL = "comercial"
    BODEGA = "bodega"
    ROLES = [
        (GERENTE, "Gerente / propietario"),
        (COMERCIAL, "Comercial"),
        (BODEGA, "Bodega"),
    ]

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil")
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="perfiles", null=True, blank=True)
    rol = models.CharField(max_length=20, choices=ROLES, default=GERENTE)

    class Meta:
        verbose_name = "perfil"
        verbose_name_plural = "perfiles"

    def __str__(self):
        return f"{self.usuario.get_username()} ({self.get_rol_display()})"


class RegistroActividad(models.Model):
    """Bitácora de uso. Sirve como evidencia de adopción durante el piloto."""

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="actividades")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    modulo = models.CharField(max_length=40)
    accion = models.CharField(max_length=60)
    detalle = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "registro de actividad"
        verbose_name_plural = "registros de actividad"

    def __str__(self):
        return f"{self.fecha:%Y-%m-%d %H:%M} {self.modulo}: {self.accion}"


ESCALA_LIKERT = [MinValueValidator(1), MaxValueValidator(5)]


class RespuestaSUS(models.Model):
    """Respuesta al cuestionario System Usability Scale (Brooke, 1996)."""

    PREGUNTAS = [
        "Creo que me gustaría utilizar este sistema con frecuencia.",
        "Encontré el sistema innecesariamente complejo.",
        "Pensé que el sistema era fácil de usar.",
        "Creo que necesitaría el apoyo de una persona experta para poder utilizar este sistema.",
        "Encontré que las diversas funciones de este sistema estaban bien integradas.",
        "Pensé que había demasiada inconsistencia en este sistema.",
        "Imagino que la mayoría de las personas aprendería a utilizar este sistema muy rápidamente.",
        "Encontré el sistema muy difícil de utilizar.",
        "Me sentí muy seguro utilizando el sistema.",
        "Necesité aprender muchas cosas antes de poder empezar a utilizar este sistema.",
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="respuestas_sus")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    fecha = models.DateTimeField(auto_now_add=True)
    p1 = models.PositiveSmallIntegerField(validators=ESCALA_LIKERT)
    p2 = models.PositiveSmallIntegerField(validators=ESCALA_LIKERT)
    p3 = models.PositiveSmallIntegerField(validators=ESCALA_LIKERT)
    p4 = models.PositiveSmallIntegerField(validators=ESCALA_LIKERT)
    p5 = models.PositiveSmallIntegerField(validators=ESCALA_LIKERT)
    p6 = models.PositiveSmallIntegerField(validators=ESCALA_LIKERT)
    p7 = models.PositiveSmallIntegerField(validators=ESCALA_LIKERT)
    p8 = models.PositiveSmallIntegerField(validators=ESCALA_LIKERT)
    p9 = models.PositiveSmallIntegerField(validators=ESCALA_LIKERT)
    p10 = models.PositiveSmallIntegerField(validators=ESCALA_LIKERT)
    comentario = models.TextField("Comentario (opcional)", blank=True)

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "respuesta SUS"
        verbose_name_plural = "respuestas SUS"

    @property
    def respuestas(self):
        return [getattr(self, f"p{i}") for i in range(1, 11)]

    @property
    def puntaje(self):
        """Puntaje SUS de 0 a 100.

        Ítems impares (redacción positiva): respuesta − 1.
        Ítems pares (redacción negativa): 5 − respuesta.
        La suma (0–40) se multiplica por 2,5.
        """
        total = 0
        for i, r in enumerate(self.respuestas, start=1):
            total += (r - 1) if i % 2 == 1 else (5 - r)
        return total * 2.5

    def __str__(self):
        return f"SUS {self.puntaje:.1f} — {self.empresa}"
