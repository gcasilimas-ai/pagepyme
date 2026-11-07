"""Pruebas de los indicadores contra cálculos hechos a mano."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from comercial import servicios as com
from comercial.models import Venta
from core.models import Empresa
from inventario.models import MovimientoInventario
from inventario.servicios import registrar_movimiento
from maestros.models import Cliente, Producto

from . import indicadores as ind

INICIO = date(2026, 3, 1)
FIN = date(2026, 3, 30)  # 30 días


class IndicadoresTest(TestCase):
    """Escenario controlado:

    * Producto A: costo 100, precio 150. Entrada de 10 unidades antes del periodo.
    * Producto B: costo 50, precio 80. Entrada de 2 unidades antes del periodo.
    * Venta 1 (día 5, contado, cliente X): 4 de A  → ingreso 600, costo 400.
    * Venta 2 (día 6, crédito, cliente Y): 2 de A → ingreso 300, costo 200.
    * Venta 3 (día 10, contado, cliente X): 2 de B → ingreso 160, costo 100.
    * Abono de 100 a la venta 2 el día 20.
    """

    def setUp(self):
        self.e = Empresa.objects.create(nombre="Prueba", meta_ticket=Decimal("300"))
        self.a = Producto.objects.create(empresa=self.e, codigo="A", nombre="A", costo_unitario=100, precio_venta=150)
        self.b = Producto.objects.create(empresa=self.e, codigo="B", nombre="B", costo_unitario=50, precio_venta=80)
        Producto.objects.filter(empresa=self.e).update(creado="2026-01-01T00:00:00Z")
        self.x = Cliente.objects.create(empresa=self.e, documento="1", nombre="X")
        self.y = Cliente.objects.create(empresa=self.e, documento="2", nombre="Y", plazo_credito_dias=30)
        antes = INICIO - timedelta(days=1)
        registrar_movimiento(self.e, self.a, MovimientoInventario.ENTRADA, 10, costo_unitario=100, fecha=antes)
        registrar_movimiento(self.e, self.b, MovimientoInventario.ENTRADA, 2, costo_unitario=50, fecha=antes)
        com.crear_venta(self.e, self.x, [(self.a, 4, Decimal(150))], fecha=INICIO + timedelta(days=4))
        self.v2 = com.crear_venta(self.e, self.y, [(self.a, 2, Decimal(150))], forma_pago=Venta.CREDITO,
                                  fecha=INICIO + timedelta(days=5))
        com.crear_venta(self.e, self.x, [(self.b, 2, Decimal(80))], fecha=INICIO + timedelta(days=9))
        com.registrar_abono(self.v2, 100, fecha=INICIO + timedelta(days=19))
        self.lista, self.base = ind.calcular_indicadores(self.e, INICIO, FIN)
        self.por = {i.clave: i for i in self.lista}

    def test_cifras_base(self):
        self.assertEqual(self.base["ingresos"], Decimal("1060"))
        self.assertEqual(self.base["costo"], Decimal("700"))
        self.assertEqual(self.base["n_ventas"], 3)
        # Inventario: inicial 10×100 + 2×50 = 1100; final 4×100 + 0 = 400
        self.assertEqual(self.base["inv_inicial"], Decimal("1100"))
        self.assertEqual(self.base["inv_final"], Decimal("400"))

    def test_margen(self):
        # (1060 − 700) / 1060 × 100 = 33,96 %
        self.assertEqual(self.por["margen"].valor, Decimal("34.0"))
        self.assertEqual(self.por["margen"].estado, "cumple")

    def test_ticket(self):
        # 1060 / 3 = 353,33
        self.assertEqual(self.por["ticket"].valor, Decimal("353"))

    def test_rotacion(self):
        # 700 / ((1100 + 400) / 2) × 365 / 30 = 11,36
        self.assertEqual(self.por["rotacion"].valor, Decimal("11.4"))

    def test_dso(self):
        # saldo 300 − 100 = 200; ventas a crédito 300; 200 / 300 × 30 = 20 días
        self.assertEqual(self.por["dso"].valor, Decimal("20"))
        self.assertEqual(self.por["dso"].estado, "cumple")
        self.assertEqual(self.por["dso"].cumplimiento, Decimal("150"))

    def test_quiebres(self):
        # B quedó en 0 → 1 de 2 productos = 50 %
        self.assertEqual(self.por["quiebres"].valor, Decimal("50.0"))
        self.assertEqual(self.por["quiebres"].estado, "critico")
        self.assertEqual(self.por["quiebres"].cumplimiento, Decimal("10"))

    def test_recurrentes(self):
        # X compró 2 veces, Y una → 1 de 2 = 50 %
        self.assertEqual(self.por["recurrentes"].valor, Decimal("50.0"))

    def test_venta_anulada_no_cuenta(self):
        venta = Venta.objects.get(empresa=self.e, numero=3)
        com.anular_venta(venta)
        lista, base = ind.calcular_indicadores(self.e, INICIO, FIN)
        self.assertEqual(base["ingresos"], Decimal("900"))
        self.assertEqual(base["productos_quiebre"], 0)

    def test_regla_de_estado(self):
        i = ind.Indicador("x", "x", Decimal("27"), Decimal("30"), "mayor", "%", "", "")
        self.assertEqual((i.cumplimiento, i.estado), (Decimal("90"), "riesgo"))
        i = ind.Indicador("x", "x", Decimal("26"), Decimal("30"), "mayor", "%", "", "")
        self.assertEqual(i.estado, "critico")
        i = ind.Indicador("x", "x", Decimal("6.2"), Decimal("5"), "menor", "%", "", "")
        self.assertEqual((i.cumplimiento, i.estado), (Decimal("81"), "critico"))
        i = ind.Indicador("x", "x", None, Decimal("5"), "menor", "%", "", "")
        self.assertEqual(i.estado, "sin_datos")

    def test_cartera_por_antiguedad(self):
        tramos = {t["tramo"]: t["saldo"] for t in ind.cartera_por_antiguedad(self.e, FIN)}
        self.assertEqual(tramos["Por vencer"], 200.0)
        tramos = {t["tramo"]: t["saldo"] for t in ind.cartera_por_antiguedad(self.e, FIN + timedelta(days=40))}
        self.assertEqual(tramos["31–60 días"], 200.0)
