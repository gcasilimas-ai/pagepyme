"""Pruebas del tablero comercial: días hábiles, avance de meta, abandono de clientes y seguimientos."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from comercial import servicios as com
from comercial.models import MetaComercial, Seguimiento
from core.models import Empresa, Perfil
from inventario.models import MovimientoInventario
from inventario.servicios import registrar_movimiento
from maestros.models import Cliente, Producto

from . import comercial as c


class DiasHabilesTest(TestCase):
    def test_lunes_a_sabado(self):
        # Noviembre de 2026 tiene 30 días y 5 domingos (1, 8, 15, 22 y 29)
        self.assertEqual(c.dias_habiles(date(2026, 11, 1), date(2026, 11, 30)), 25)
        self.assertEqual(c.dias_habiles(date(2026, 11, 2), date(2026, 11, 7)), 6)
        self.assertEqual(c.dias_habiles(date(2026, 11, 8), date(2026, 11, 8)), 0)

    def test_mes_anterior(self):
        self.assertEqual(c.mes_anterior(2026, 1), (2025, 12))
        self.assertEqual(c.mes_anterior(2026, 11, 12), (2025, 11))


class EscenarioComercial(TestCase):
    def setUp(self):
        self.e = Empresa.objects.create(nombre="Prueba")
        self.p = Producto.objects.create(empresa=self.e, codigo="A", nombre="A", costo_unitario=100, precio_venta=1000)
        registrar_movimiento(self.e, self.p, MovimientoInventario.ENTRADA, 1000, costo_unitario=100, fecha=date(2026, 1, 1))

    def cliente(self, doc):
        return Cliente.objects.create(empresa=self.e, documento=doc, nombre=f"Cliente {doc}")

    def vender(self, cliente, fecha, unidades=1):
        return com.crear_venta(self.e, cliente, [(self.p, unidades, Decimal(1000))], fecha=fecha)


class AvanceMetaTest(EscenarioComercial):
    def test_avance_proyeccion_y_ritmo(self):
        MetaComercial.objects.create(empresa=self.e, anio=2026, mes=11, valor=Decimal("100000"))
        x = self.cliente("1")
        self.vender(x, date(2026, 11, 2), 10)   # 10.000
        self.vender(x, date(2026, 11, 5), 8)    # 8.000
        avance = c.avance_meta(self.e, 2026, 11, date(2026, 11, 7))
        self.assertEqual(avance.ventas, Decimal("18000"))
        self.assertEqual((avance.habiles_transcurridos, avance.habiles_total), (6, 25))
        self.assertEqual(avance.cumplimiento, Decimal("18"))
        # Proyección: 18.000 / 6 × 25 = 75.000 → 75 % de la meta → crítico
        self.assertEqual(round(avance.proyeccion), 75000)
        self.assertEqual(avance.estado, "critico")
        # Esperado a la fecha: 100.000 × 6 / 25 = 24.000
        self.assertEqual(round(avance.esperado), 24000)
        # Falta 82.000 en 19 días hábiles
        self.assertEqual(avance.falta, Decimal("82000"))
        self.assertEqual(round(avance.por_dia), round(82000 / 19))

    def test_mes_cerrado_se_evalua_con_ventas_reales(self):
        MetaComercial.objects.create(empresa=self.e, anio=2026, mes=10, valor=Decimal("10000"))
        self.vender(self.cliente("1"), date(2026, 10, 15), 11)
        avance = c.avance_meta(self.e, 2026, 10, date(2026, 11, 20))
        self.assertTrue(avance.cerrado)
        self.assertEqual((avance.estado, avance.proyeccion), ("cumple", Decimal("11000")))

    def test_sin_meta(self):
        avance = c.avance_meta(self.e, 2026, 11, date(2026, 11, 7))
        self.assertEqual((avance.estado, avance.cumplimiento, avance.falta), ("sin_meta", None, None))

    def test_serie_diaria_acumula(self):
        MetaComercial.objects.create(empresa=self.e, anio=2026, mes=11, valor=Decimal("25000"))
        x = self.cliente("1")
        self.vender(x, date(2026, 11, 2), 1)
        self.vender(x, date(2026, 11, 3), 2)
        serie = c.serie_diaria(self.e, c.avance_meta(self.e, 2026, 11, date(2026, 11, 3)))
        self.assertEqual([d["ventas"] for d in serie[:4]], [0.0, 1000.0, 3000.0, None])
        self.assertEqual(serie[1]["meta"], 1000.0)   # 25.000 / 25 días hábiles
        self.assertEqual(serie[-1]["meta"], 25000.0)


class ClientesYSeguimientosTest(EscenarioComercial):
    def test_clasificacion_de_abandono(self):
        corte = date(2026, 6, 30)
        frecuente = self.cliente("1")       # cada 10 días, última compra hace 35 → dejó de comprar
        for d in (0, 10, 20):
            self.vender(frecuente, corte - timedelta(days=55 - d))
        demorado = self.cliente("2")        # cada 10 días, última compra hace 25 → en riesgo
        for d in (0, 10, 20):
            self.vender(demorado, corte - timedelta(days=45 - d))
        unico = self.cliente("3")           # una compra hace 70 días → no volvió
        self.vender(unico, corte - timedelta(days=70))
        al_dia = self.cliente("4")          # compra reciente
        for d in (0, 10, 20):
            self.vender(al_dia, corte - timedelta(days=22 - d))
        grupos = c.clasificar_clientes(self.e, corte)
        nombres = {g: [x["cliente"].documento for x in filas] for g, filas in grupos.items()}
        self.assertEqual(nombres, {"dejo": ["1"], "riesgo": ["2"], "no_volvio": ["3"]})
        self.assertEqual(grupos["dejo"][0]["intervalo"], 10)

    def test_seguimiento_pendiente_es_el_ultimo(self):
        x, y = self.cliente("1"), self.cliente("2")
        hoy = date(2026, 6, 30)
        Seguimiento.objects.create(empresa=self.e, cliente=x, fecha=hoy - timedelta(days=10), resultado="interesado",
                                   proxima_fecha=hoy - timedelta(days=3), proxima_accion="Llamar")
        Seguimiento.objects.create(empresa=self.e, cliente=y, fecha=hoy - timedelta(days=10), resultado="interesado",
                                   proxima_fecha=hoy - timedelta(days=5), proxima_accion="Llamar")
        Seguimiento.objects.create(empresa=self.e, cliente=y, fecha=hoy - timedelta(days=1), resultado="compra")
        pendientes = c.seguimientos_pendientes(self.e, hoy)
        self.assertEqual([(s.cliente.documento, s.atraso) for s in pendientes], [("1", 3)])


@override_settings(ALLOWED_HOSTS=["testserver"])
class VistasComercialesTest(TestCase):
    def setUp(self):
        self.e = Empresa.objects.create(nombre="Uno")
        User = get_user_model()
        for rol in ("gerente", "comercial", "bodega"):
            u = User.objects.create_user(rol, password="clave-larga-123")
            Perfil.objects.create(usuario=u, empresa=self.e, rol=rol)

    def test_acceso_por_rol(self):
        self.client.login(username="comercial", password="clave-larga-123")
        self.assertEqual(self.client.get("/analitica/comercial/").status_code, 200)
        self.assertEqual(self.client.get("/comercial/seguimientos/").status_code, 200)
        self.assertRedirects(self.client.get("/"), "/analitica/comercial/")
        self.client.login(username="bodega", password="clave-larga-123")
        self.assertEqual(self.client.get("/analitica/comercial/").status_code, 403)

    def test_guardar_metas_del_anio(self):
        self.client.login(username="gerente", password="clave-larga-123")
        datos = {"seccion": "comercial", "anio": "2027", **{f"mes_{i}": "" for i in range(1, 13)}}
        datos.update({"mes_1": "20000000", "mes_2": "22000000"})
        self.client.post("/configuracion/metas/", datos)
        self.assertEqual(list(MetaComercial.objects.filter(empresa=self.e, anio=2027).values_list("mes", "valor")),
                         [(1, Decimal("20000000")), (2, Decimal("22000000"))])
        datos["mes_2"] = ""
        self.client.post("/configuracion/metas/", datos)
        self.assertEqual(MetaComercial.objects.filter(empresa=self.e, anio=2027).count(), 1)
