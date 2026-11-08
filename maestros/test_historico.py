"""Pruebas de la carga histórica de ventas y compras y de la reconstrucción del inventario."""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from analitica import indicadores as ind
from comercial import servicios as com
from comercial.models import Venta
from core.models import Empresa
from inventario.models import MovimientoInventario

from .historico import convertir_fecha
from .importacion import analizar_archivo, aplicar_lote, generar_plantilla
from .models import Cliente, Producto


def csv(texto, nombre="datos.csv"):
    return SimpleUploadedFile(nombre, texto.encode("utf-8"), content_type="text/csv")


def cargar(empresa, tipo, texto):
    lote = analizar_archivo(empresa, tipo, csv(texto))
    if lote.filas_validas:
        aplicar_lote(lote)
    lote.refresh_from_db()
    return lote


def neto(producto):
    return sum(m.cantidad_neta for m in MovimientoInventario.objects.filter(producto=producto))


class FechasTest(TestCase):
    def test_formatos(self):
        self.assertEqual(convertir_fecha("15/08/2026"), date(2026, 8, 15))
        self.assertEqual(convertir_fecha("2026-08-15"), date(2026, 8, 15))
        self.assertEqual(convertir_fecha(46249), date(2026, 8, 15))  # número de serie de Excel
        with self.assertRaises(ValueError):
            convertir_fecha("15 de agosto")


class CargaHistoricaTest(TestCase):
    def setUp(self):
        self.e = Empresa.objects.create(nombre="Piloto")
        self.hoy = timezone.localdate()
        self.d = lambda dias: (self.hoy - timedelta(days=dias)).strftime("%d/%m/%Y")
        cargar(self.e, "productos", "codigo,nombre,costo,precio\nA,Casco,100,150\nB,Guante,10,20\n")
        cargar(self.e, "clientes", "nit,razon social,plazo\n900,Constructora,30\n")
        self.a = Producto.objects.get(empresa=self.e, codigo="A")

    def ventas(self):
        return (
            "Fecha,No. Factura,NIT,Cliente,Referencia,Cantidad,Precio,Forma de pago,Saldo\n"
            f"{self.d(60)},F-1,900,,A,3,150,Crédito,0\n"
            f"{self.d(60)},F-1,900,,B,10,20,Crédito,0\n"
            f"{self.d(20)},F-2,800,Ferretería Nueva,A,2,150,contado,\n"
            f"{self.d(5)},F-3,900,,A,1,150,credito,150\n"
        )

    def test_ventas_facturas_pagos_y_clientes(self):
        lote = cargar(self.e, "ventas", self.ventas())
        self.assertEqual((lote.estado, lote.creados, lote.filas_con_error), ("aplicado", 3, 0))
        f1 = Venta.objects.get(empresa=self.e, documento_externo="F-1")
        self.assertEqual((f1.total, f1.forma_pago, f1.items.count(), f1.saldo), (Decimal("650"), "credito", 2, 0))
        # Pagada sin fecha de pago: se asume el pago al vencimiento (30 días después).
        self.assertEqual(f1.pagos.get().fecha, f1.fecha + timedelta(days=30))
        self.assertEqual(Venta.objects.get(documento_externo="F-3").saldo, Decimal("150"))
        self.assertTrue(Cliente.objects.filter(empresa=self.e, documento="800", nombre="Ferretería Nueva").exists())
        self.assertTrue(any("fecha de vencimiento" in n for n in lote.notas))
        # Repetir el archivo no duplica facturas.
        repetido = analizar_archivo(self.e, "ventas", csv(self.ventas()))
        self.assertEqual(repetido.filas_validas, 0)

    def test_factura_incoherente_se_rechaza(self):
        texto = ("fecha,factura,documento_cliente,codigo,cantidad,precio_unitario\n"
                 f"{self.d(10)},F-9,900,A,1,150\n{self.d(9)},F-9,900,B,1,20\n")
        lote = analizar_archivo(self.e, "ventas", csv(texto))
        self.assertEqual((lote.filas_validas, lote.filas_con_error), (0, 2))

    def test_reconstruccion_por_balance_sin_importar_el_orden(self):
        cargar(self.e, "ventas", self.ventas())                                  # A: vendió 6
        cargar(self.e, "compras", f"fecha,codigo,cantidad,costo\n{self.d(30)},A,4,100\n")  # A: compró 4
        cargar(self.e, "inventario", "codigo,cantidad\nA,10\n")                  # A: hay 10 hoy
        self.a.refresh_from_db()
        self.assertEqual(self.a.stock_actual, 10)
        self.assertEqual(neto(self.a), 10)
        apertura = MovimientoInventario.objects.get(producto=self.a, origen=MovimientoInventario.SALDO_INICIAL)
        self.assertEqual(apertura.cantidad, 10 - 4 + 6)
        self.assertEqual(apertura.fecha, self.hoy - timedelta(days=61))
        # Inventario valorizado del día anterior a la primera venta: A 12 × 100 y B 10 × 10
        # (B no tiene inventario actual cargado, así que se asume que hoy hay 0 y el saldo inicial son sus 10 vendidas).
        self.assertEqual(ind.valor_inventario(self.e, self.hoy - timedelta(days=61)), Decimal("1300"))

        otra = Empresa.objects.create(nombre="Otra")
        cargar(otra, "productos", "codigo,nombre,costo,precio\nA,Casco,100,150\nB,Guante,10,20\n")
        cargar(otra, "clientes", "nit,razon social\n900,Constructora\n")
        cargar(otra, "inventario", "codigo,cantidad\nA,10\n")
        cargar(otra, "compras", f"fecha,codigo,cantidad,costo\n{self.d(30)},A,4,100\n")
        cargar(otra, "ventas", self.ventas())
        a2 = Producto.objects.get(empresa=otra, codigo="A")
        self.assertEqual((a2.stock_actual, neto(a2)), (10, 10))
        self.assertEqual(MovimientoInventario.objects.get(producto=a2, origen="saldo_inicial").cantidad, 12)

    def test_compras_mayores_que_ventas_y_existencias(self):
        cargar(self.e, "compras", f"fecha,codigo,cantidad,costo\n{self.d(30)},A,20,100\n")
        lote = cargar(self.e, "inventario", "codigo,cantidad\nA,5\n")
        self.a.refresh_from_db()
        self.assertEqual((self.a.stock_actual, neto(self.a)), (5, 5))
        ajuste = MovimientoInventario.objects.get(producto=self.a, origen="saldo_inicial")
        self.assertEqual((ajuste.tipo, ajuste.cantidad), (MovimientoInventario.AJUSTE_NEGATIVO, 15))
        self.assertTrue(any("merma" in n for n in lote.notas))

    def test_operaciones_posteriores_conservan_el_balance(self):
        cargar(self.e, "ventas", self.ventas())
        cargar(self.e, "inventario", "codigo,cantidad\nA,10\nB,50\n")
        cliente = Cliente.objects.get(empresa=self.e, documento="900")
        com.crear_venta(self.e, cliente, [(self.a, 2, Decimal("150"))])
        cargar(self.e, "compras", f"fecha,codigo,cantidad,costo\n{self.d(40)},A,5,100\n")
        self.a.refresh_from_db()
        self.assertEqual((self.a.stock_actual, neto(self.a)), (8, 8))

    def test_venta_historica_no_se_anula(self):
        cargar(self.e, "ventas", self.ventas())
        with self.assertRaises(ValidationError):
            com.anular_venta(Venta.objects.get(empresa=self.e, documento_externo="F-2"))

    def test_indicadores_sobre_la_historia(self):
        cargar(self.e, "ventas", self.ventas())
        cargar(self.e, "inventario", "codigo,cantidad\nA,10\nB,50\n")
        base = ind.valores_base(self.e, self.hoy - timedelta(days=89), self.hoy)
        self.assertEqual(base["ingresos"], Decimal("1100"))
        self.assertEqual(base["saldo_cartera"], Decimal("150"))
        self.assertEqual(base["clientes_recurrentes"], 1)

    def test_plantillas(self):
        for tipo in ("ventas", "compras"):
            self.assertTrue(generar_plantilla(tipo).startswith(b"PK"))
