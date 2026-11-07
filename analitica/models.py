"""Capa analítica de PAGE-PYME.

Modelo dimensional (esquema en estrella) construido sobre las tablas
transaccionales:

* ``DimFecha`` es una tabla de calendario (dimensión de tiempo).
* Las dimensiones de producto, categoría y cliente son las tablas maestras.
* ``FactVenta``, ``FactMovimiento`` y ``FactRecaudo`` son vistas SQL de solo
  lectura (``managed = False``) que definen la granularidad de cada hecho.

El tablero de control consulta exclusivamente estas vistas, de modo que la
lógica de cálculo de los indicadores queda separada de la operación diaria y
puede reutilizarse desde otra herramienta de BI conectada a la misma base.
"""
from django.db import models


class DimFecha(models.Model):
    fecha = models.DateField(primary_key=True)
    anio = models.PositiveSmallIntegerField()
    trimestre = models.PositiveSmallIntegerField()
    mes = models.PositiveSmallIntegerField()
    nombre_mes = models.CharField(max_length=12)
    anio_iso = models.PositiveSmallIntegerField()
    semana_iso = models.PositiveSmallIntegerField()
    dia_semana = models.PositiveSmallIntegerField(help_text="1 = lunes, 7 = domingo")
    nombre_dia = models.CharField(max_length=12)
    es_fin_de_semana = models.BooleanField(default=False)

    class Meta:
        ordering = ["fecha"]
        verbose_name = "dimensión fecha"
        verbose_name_plural = "dimensión fecha"

    def __str__(self):
        return self.fecha.isoformat()


class _Hecho(models.Model):
    """Base de las vistas de hechos (solo lectura)."""

    class Meta:
        abstract = True
        managed = False

    def save(self, *args, **kwargs):  # pragma: no cover - protección
        raise NotImplementedError("Las vistas de hechos son de solo lectura.")


class FactVenta(_Hecho):
    """Granularidad: una línea de venta emitida."""

    id = models.BigIntegerField(primary_key=True)
    empresa = models.ForeignKey("core.Empresa", on_delete=models.DO_NOTHING, db_constraint=False)
    venta = models.ForeignKey("comercial.Venta", on_delete=models.DO_NOTHING, db_constraint=False)
    fecha = models.ForeignKey(DimFecha, on_delete=models.DO_NOTHING, db_constraint=False, db_column="fecha")
    cliente = models.ForeignKey("maestros.Cliente", on_delete=models.DO_NOTHING, db_constraint=False)
    producto = models.ForeignKey("maestros.Producto", on_delete=models.DO_NOTHING, db_constraint=False)
    categoria = models.ForeignKey("maestros.Categoria", on_delete=models.DO_NOTHING, db_constraint=False, null=True)
    forma_pago = models.CharField(max_length=10)
    cantidad = models.IntegerField()
    ingreso = models.DecimalField(max_digits=18, decimal_places=2)
    costo = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta(_Hecho.Meta):
        db_table = "analitica_fact_ventas"


class FactMovimiento(_Hecho):
    """Granularidad: un movimiento de inventario (cantidad con signo)."""

    id = models.BigIntegerField(primary_key=True)
    empresa = models.ForeignKey("core.Empresa", on_delete=models.DO_NOTHING, db_constraint=False)
    fecha = models.ForeignKey(DimFecha, on_delete=models.DO_NOTHING, db_constraint=False, db_column="fecha")
    producto = models.ForeignKey("maestros.Producto", on_delete=models.DO_NOTHING, db_constraint=False)
    categoria = models.ForeignKey("maestros.Categoria", on_delete=models.DO_NOTHING, db_constraint=False, null=True)
    tipo = models.CharField(max_length=20)
    cantidad_neta = models.IntegerField()
    valor_neto = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta(_Hecho.Meta):
        db_table = "analitica_fact_movimientos"


class FactRecaudo(_Hecho):
    """Granularidad: un abono recibido sobre una venta emitida."""

    id = models.BigIntegerField(primary_key=True)
    empresa = models.ForeignKey("core.Empresa", on_delete=models.DO_NOTHING, db_constraint=False)
    fecha = models.ForeignKey(DimFecha, on_delete=models.DO_NOTHING, db_constraint=False, db_column="fecha")
    venta = models.ForeignKey("comercial.Venta", on_delete=models.DO_NOTHING, db_constraint=False)
    cliente = models.ForeignKey("maestros.Cliente", on_delete=models.DO_NOTHING, db_constraint=False)
    forma_pago = models.CharField(max_length=10)
    valor = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta(_Hecho.Meta):
        db_table = "analitica_fact_recaudos"
