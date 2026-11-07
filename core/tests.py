"""Pruebas de acceso por rol, aislamiento entre empresas y cálculo SUS."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from maestros.models import Producto

from .models import Empresa, Perfil, RegistroActividad, RespuestaSUS


@override_settings(ALLOWED_HOSTS=["testserver"])
class PermisosTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.e1 = Empresa.objects.create(nombre="Uno")
        self.e2 = Empresa.objects.create(nombre="Dos")
        self.otro = Producto.objects.create(empresa=self.e2, codigo="X", nombre="Ajeno")
        for rol in ("gerente", "comercial", "bodega"):
            u = User.objects.create_user(rol, password="clave-larga-123")
            Perfil.objects.create(usuario=u, empresa=self.e1, rol=rol)

    def entrar(self, rol):
        self.client.login(username=rol, password="clave-larga-123")

    def test_bodega_no_ve_ventas_ni_tablero(self):
        self.entrar("bodega")
        self.assertEqual(self.client.get("/comercial/ventas/").status_code, 403)
        self.assertEqual(self.client.get("/analitica/tablero/").status_code, 403)
        self.assertEqual(self.client.get("/inventario/alertas/").status_code, 200)

    def test_comercial_no_configura(self):
        self.entrar("comercial")
        self.assertEqual(self.client.get("/configuracion/metas/").status_code, 403)
        self.assertEqual(self.client.get("/comercial/ventas/").status_code, 200)

    def test_no_se_ven_datos_de_otra_empresa(self):
        self.entrar("gerente")
        self.assertEqual(self.client.get(f"/maestros/productos/{self.otro.pk}/").status_code, 404)

    def test_ingreso_queda_en_bitacora(self):
        self.entrar("gerente")
        self.assertTrue(RegistroActividad.objects.filter(empresa=self.e1, accion="Inicio de sesión").exists())

    def test_resumen_piloto_solo_consultor(self):
        self.entrar("gerente")
        self.assertEqual(self.client.get("/evidencia/piloto/").status_code, 403)


class SUSTest(TestCase):
    def test_puntajes_extremos_y_medio(self):
        e = Empresa.objects.create(nombre="Uno")
        mejor = RespuestaSUS(empresa=e, **{f"p{i}": (5 if i % 2 else 1) for i in range(1, 11)})
        peor = RespuestaSUS(empresa=e, **{f"p{i}": (1 if i % 2 else 5) for i in range(1, 11)})
        neutro = RespuestaSUS(empresa=e, **{f"p{i}": 3 for i in range(1, 11)})
        self.assertEqual((mejor.puntaje, peor.puntaje, neutro.puntaje), (100, 0, 50))
