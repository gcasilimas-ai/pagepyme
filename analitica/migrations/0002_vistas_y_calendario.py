"""Crea las vistas de hechos del esquema en estrella y llena la dimensión fecha.

El SQL usa solo construcciones estándar (JOIN, CASE, CAST) para que funcione
igual en PostgreSQL (producción) y en SQLite (desarrollo local).
"""
from datetime import date, timedelta

from django.db import migrations

VISTAS = [
    (
        "analitica_fact_ventas",
        """
        CREATE VIEW analitica_fact_ventas AS
        SELECT iv.id AS id,
               v.empresa_id AS empresa_id,
               v.id AS venta_id,
               v.fecha AS fecha,
               v.cliente_id AS cliente_id,
               iv.producto_id AS producto_id,
               p.categoria_id AS categoria_id,
               v.forma_pago AS forma_pago,
               iv.cantidad AS cantidad,
               CAST(iv.cantidad * iv.precio_unitario AS NUMERIC(18, 2)) AS ingreso,
               CAST(iv.cantidad * iv.costo_unitario AS NUMERIC(18, 2)) AS costo
        FROM comercial_itemventa iv
        JOIN comercial_venta v ON v.id = iv.venta_id
        JOIN maestros_producto p ON p.id = iv.producto_id
        WHERE v.estado = 'emitida'
        """,
    ),
    (
        "analitica_fact_movimientos",
        """
        CREATE VIEW analitica_fact_movimientos AS
        SELECT m.id AS id,
               m.empresa_id AS empresa_id,
               m.fecha AS fecha,
               m.producto_id AS producto_id,
               p.categoria_id AS categoria_id,
               m.tipo AS tipo,
               CASE WHEN m.tipo IN ('entrada', 'ajuste_positivo', 'devolucion')
                    THEN m.cantidad ELSE -m.cantidad END AS cantidad_neta,
               CAST((CASE WHEN m.tipo IN ('entrada', 'ajuste_positivo', 'devolucion')
                    THEN m.cantidad ELSE -m.cantidad END) * m.costo_unitario AS NUMERIC(18, 2)) AS valor_neto
        FROM inventario_movimientoinventario m
        JOIN maestros_producto p ON p.id = m.producto_id
        """,
    ),
    (
        "analitica_fact_recaudos",
        """
        CREATE VIEW analitica_fact_recaudos AS
        SELECT pg.id AS id,
               v.empresa_id AS empresa_id,
               pg.fecha AS fecha,
               pg.venta_id AS venta_id,
               v.cliente_id AS cliente_id,
               v.forma_pago AS forma_pago,
               pg.valor AS valor
        FROM comercial_pago pg
        JOIN comercial_venta v ON v.id = pg.venta_id
        WHERE v.estado = 'emitida'
        """,
    ),
]

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def llenar_calendario(apps, schema_editor):
    DimFecha = apps.get_model("analitica", "DimFecha")
    filas = []
    dia = date(2024, 1, 1)
    fin = date(2032, 12, 31)
    while dia <= fin:
        iso = dia.isocalendar()
        filas.append(DimFecha(
            fecha=dia,
            anio=dia.year,
            trimestre=(dia.month - 1) // 3 + 1,
            mes=dia.month,
            nombre_mes=MESES[dia.month - 1],
            anio_iso=iso[0],
            semana_iso=iso[1],
            dia_semana=iso[2],
            nombre_dia=DIAS[iso[2] - 1],
            es_fin_de_semana=iso[2] >= 6,
        ))
        dia += timedelta(days=1)
    DimFecha.objects.bulk_create(filas, batch_size=1000)


def vaciar_calendario(apps, schema_editor):
    apps.get_model("analitica", "DimFecha").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("analitica", "0001_initial"),
        ("comercial", "0001_initial"),
        ("inventario", "0001_initial"),
        ("maestros", "0001_initial"),
    ]

    operations = [
        *[
            migrations.RunSQL(sql=sql, reverse_sql=f"DROP VIEW IF EXISTS {nombre}")
            for nombre, sql in VISTAS
        ],
        migrations.RunPython(llenar_calendario, vaciar_calendario),
    ]
