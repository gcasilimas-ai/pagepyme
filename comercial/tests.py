"""Pruebas de las reglas de negocio de ventas, inventario y cartera."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Empresa
from inventario.models import MovimientoInventario
from inventario.servicios import StockInsuficiente, registrar_movimiento
from maestros.models import Cliente, Producto

from . import servicios as com
from .models import Venta


class ReglasComercialesTest(TestCase):
    def setUp(self):
        self.e = Empresa.objects.create(nombre="Prueba")
        self.p = Producto.objects.create(empresa=self.e, codigo="A", nombre="A", costo_unitario=0, precio_venta=150)
        self.c = Cliente.objects.create(empresa=self.e, documento="1", nombre="C", plazo_credito_dias=15)

    def test_costo_promedio_ponderado(self):
        registrar_movimiento(self.e, self.p, MovimientoInventario.ENTRADA, 10, costo_unitario=100)
        registrar_movimiento(self.e, self.p, MovimientoInventario.ENTRADA, 10, costo_unitario=120)
        self.p.refresh_from_db()
        self.assertEqual(self.p.costo_unitario, Decimal("110.00"))
        self.assertEqual(self.p.stock_actual, 20)

    def test_no_permite_stock_negativo(self):
        registrar_movimiento(self.e, self.p, MovimientoInventario.ENTRADA, 3, costo_unitario=100)
        with self.assertRaises(StockInsuficiente):
            com.crear_venta(self.e, self.c, [(self.p, 2, Decimal(150)), (self.p, 2, Decimal(150))])
        self.p.refresh_from_db()
        self.assertEqual(self.p.stock_actual, 3)
        self.assertFalse(Venta.objects.exists())

    def test_venta_contado_y_credito(self):
        registrar_movimiento(self.e, self.p, MovimientoInventario.ENTRADA, 10, costo_unitario=100)
        contado = com.crear_venta(self.e, self.c, [(self.p, 2, Decimal(150))])
        self.assertEqual((contado.total, contado.saldo), (Decimal("300"), Decimal("0")))
        credito = com.crear_venta(self.e, self.c, [(self.p, 1, Decimal(150))], forma_pago=Venta.CREDITO)
        self.assertEqual(credito.saldo, Decimal("150"))
        self.assertEqual((credito.fecha_vencimiento - credito.fecha).days, 15)
        self.assertEqual(credito.items.get().costo_unitario, Decimal("100.00"))
        with self.assertRaises(ValidationError):
            com.registrar_abono(credito, 200)
        com.registrar_abono(credito, 150)
        self.assertEqual(credito.saldo, Decimal("0"))

    def test_anulacion_devuelve_inventario(self):
        registrar_movimiento(self.e, self.p, MovimientoInventario.ENTRADA, 5, costo_unitario=100)
        venta = com.crear_venta(self.e, self.c, [(self.p, 5, Decimal(150))])
        com.anular_venta(venta, motivo="error")
        self.p.refresh_from_db()
        self.assertEqual(self.p.stock_actual, 5)
        self.assertEqual(venta.saldo, Decimal("0"))

    def test_convertir_cotizacion(self):
        registrar_movimiento(self.e, self.p, MovimientoInventario.ENTRADA, 5, costo_unitario=100)
        cot = com.crear_cotizacion(self.e, self.c, [(self.p, 2, Decimal(140))])
        venta = com.convertir_cotizacion(cot, Venta.CONTADO)
        cot.refresh_from_db()
        self.assertEqual((venta.total, cot.estado), (Decimal("280"), "convertida"))
        with self.assertRaises(ValidationError):
            com.convertir_cotizacion(cot, Venta.CONTADO)
