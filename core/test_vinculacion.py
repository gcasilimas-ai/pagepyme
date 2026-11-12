"""Pruebas de la vinculación de empresas al piloto."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from comercial import servicios as com
from core.models import Empresa, Perfil, RegistroActividad
from inventario.models import MovimientoInventario
from inventario.servicios import registrar_movimiento
from maestros.models import Cliente, Producto


@override_settings(ALLOWED_HOSTS=["testserver"])
class VinculacionTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.consultor = User.objects.create_user("consultor", password="clave-larga-123", is_staff=True)
        self.demo = Empresa.objects.create(nombre="Demo", es_demo=True)
        u = User.objects.create_user("gerente.demo", password="clave-larga-123")
        Perfil.objects.create(usuario=u, empresa=self.demo, rol="gerente")

    def datos(self, **extra):
        datos = {
            "empresa-nombre": "Ferretería Industrial", "empresa-nit": "901", "empresa-ciudad": "Neiva",
            "empresa-departamento": "Huila", "empresa-sector": "EPP", "empresa-numero_empleados": "6",
            "empresa-anios_operacion": "8", "empresa-herramienta_previa": "Excel", "empresa-fecha_vinculacion": "2026-11-09",
            "gerente-username": "gerente.fi", "gerente-first_name": "Ana", "gerente-last_name": "",
            "gerente-email": "", "gerente-password1": "Clave-Segura-2026", "gerente-password2": "Clave-Segura-2026",
        }
        datos.update(extra)
        return datos

    def test_solo_consultor(self):
        self.client.login(username="gerente.demo", password="clave-larga-123")
        self.assertEqual(self.client.get("/piloto/empresas/").status_code, 403)

    def test_vincular_empresa_con_gerente(self):
        self.client.login(username="consultor", password="clave-larga-123")
        respuesta = self.client.post("/piloto/empresas/", self.datos())
        self.assertRedirects(respuesta, "/maestros/importar/")
        empresa = Empresa.objects.get(nombre="Ferretería Industrial")
        self.assertEqual((empresa.numero_empleados, empresa.es_demo), (6, False))
        perfil = Perfil.objects.get(usuario__username="gerente.fi")
        self.assertEqual((perfil.empresa, perfil.rol), (empresa, "gerente"))
        self.assertEqual(self.client.session["empresa_id"], empresa.pk)
        # El registro de la vinculación no cuenta como uso de la empresa.
        self.assertTrue(RegistroActividad.objects.filter(empresa=empresa, usuario=self.consultor).exists())
        self.client.logout()
        self.client.login(username="gerente.fi", password="Clave-Segura-2026")
        uso = self.client.get("/evidencia/uso/")
        self.assertEqual(uso.status_code, 200)
        propias = RegistroActividad.objects.filter(empresa=empresa, usuario__is_staff=False).count()
        total = RegistroActividad.objects.filter(empresa=empresa).count()
        self.assertEqual(sum(f["acciones"] for f in uso.context["filas"]), propias)
        self.assertLess(propias, total)

    def test_reiniciar_datos(self):
        e = Empresa.objects.create(nombre="Piloto Uno")
        p = Producto.objects.create(empresa=e, codigo="A", nombre="A", costo_unitario=10, precio_venta=20)
        c = Cliente.objects.create(empresa=e, documento="1", nombre="Uno")
        registrar_movimiento(e, p, MovimientoInventario.ENTRADA, 5, costo_unitario=10)
        com.crear_venta(e, c, [(p, 2, 20)])
        self.client.login(username="consultor", password="clave-larga-123")
        mal = self.client.post(f"/piloto/empresas/{e.pk}/reiniciar/", {"confirmacion": "otra"})
        self.assertEqual(mal.status_code, 200)
        self.client.post(f"/piloto/empresas/{e.pk}/reiniciar/", {"confirmacion": "Piloto Uno"})
        p.refresh_from_db()
        self.assertEqual((p.stock_actual, e.ventas.count(), e.movimientos.count()), (0, 0, 0))
        self.client.post(f"/piloto/empresas/{e.pk}/reiniciar/", {"confirmacion": "Piloto Uno", "incluir_maestros": "on"})
        self.assertEqual((e.productos.count(), e.clientes.count()), (0, 0))
        self.assertEqual(self.client.get(f"/piloto/empresas/{self.demo.pk}/reiniciar/").status_code, 404)


@override_settings(ALLOWED_HOSTS=["testserver"])
class EvidenciaPilotoTest(TestCase):
    def test_descarga_solo_consultor(self):
        from io import BytesIO
        from openpyxl import load_workbook
        User = get_user_model()
        User.objects.create_user("consultor", password="clave-larga-123", is_staff=True)
        e = Empresa.objects.create(nombre="Piloto Uno")
        p = Producto.objects.create(empresa=e, codigo="A", nombre="A", costo_unitario=10, precio_venta=20)
        c = Cliente.objects.create(empresa=e, documento="1", nombre="Cliente Secreto")
        registrar_movimiento(e, p, MovimientoInventario.ENTRADA, 5, costo_unitario=10)
        com.crear_venta(e, c, [(p, 2, 20)])
        g = User.objects.create_user("gerente", password="clave-larga-123")
        Perfil.objects.create(usuario=g, empresa=e, rol="gerente")
        self.client.login(username="gerente", password="clave-larga-123")
        self.assertEqual(self.client.get("/piloto/evidencia.xlsx").status_code, 403)
        self.client.login(username="consultor", password="clave-larga-123")
        r = self.client.get("/piloto/evidencia.xlsx")
        self.assertEqual(r.status_code, 200)
        libro = load_workbook(BytesIO(r.content))
        self.assertIn("Indicadores", libro.sheetnames)
        self.assertEqual(libro["Empresas"]["A2"].value, "Piloto Uno")
        textos = " ".join(str(c.value) for h in libro.worksheets for fila in h.iter_rows() for c in fila if c.value)
        self.assertNotIn("Cliente Secreto", textos)
