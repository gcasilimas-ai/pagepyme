"""Pruebas del proceso de importación (extracción, transformación, validación y carga)."""
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.models import Empresa

from .importacion import aplicar_lote, analizar_archivo, convertir_numero
from .models import Categoria, Cliente, Producto


def csv(nombre, texto):
    return SimpleUploadedFile(nombre, texto.encode("utf-8"), content_type="text/csv")


class ConversionNumerosTest(TestCase):
    def test_formatos_colombianos(self):
        self.assertEqual(convertir_numero("12.000"), Decimal("12000"))
        self.assertEqual(convertir_numero("$ 1.234.567"), Decimal("1234567"))
        self.assertEqual(convertir_numero("12.000,50"), Decimal("12000.50"))
        self.assertEqual(convertir_numero("1234.5"), Decimal("1234.5"))
        self.assertEqual(convertir_numero("15,5"), Decimal("15.5"))
        self.assertEqual(convertir_numero(42), Decimal("42"))
        with self.assertRaises(ValueError):
            convertir_numero("doce")


class ImportarProductosTest(TestCase):
    def setUp(self):
        self.e = Empresa.objects.create(nombre="Prueba")
        Producto.objects.create(empresa=self.e, codigo="CAS-001", nombre="Casco viejo", costo_unitario=1, precio_venta=2)

    def test_valida_y_carga(self):
        archivo = csv("productos.csv",
                      "Código;Producto;Categoría;Costo;Precio;Mínimo\n"
                      "cas-001; Casco de seguridad ;Cabeza;21.000;35.000;10\n"
                      "GUA-1;Guante;Manos;6500;5000;20\n"
                      "GUA-1;Guante repetido;Manos;6500;9000;20\n"
                      "BOT-1;Bota;Pies;abc;90000;5\n"
                      ";Sin código;Pies;1000;2000;1\n")
        lote = analizar_archivo(self.e, "productos", archivo)
        self.assertEqual((lote.total_filas, lote.filas_validas, lote.filas_con_error), (5, 2, 3))
        # Transformación: código en mayúsculas, espacios recortados, número con separador de miles
        primera = lote.datos[0]["datos"]
        self.assertEqual((primera["codigo"], primera["nombre"], primera["costo_unitario"], primera["accion"]),
                         ("CAS-001", "Casco de seguridad", "21000.00", "actualizar"))
        # Advertencia de margen negativo en el guante
        self.assertTrue(any("menor que el costo" in a for a in lote.datos[1]["advertencias"]))
        aplicar_lote(lote)
        self.assertEqual(Producto.objects.filter(empresa=self.e).count(), 2)
        self.assertEqual(Producto.objects.get(empresa=self.e, codigo="CAS-001").nombre, "Casco de seguridad")
        self.assertTrue(Categoria.objects.filter(empresa=self.e, nombre="Cabeza").exists())
        self.assertEqual((lote.creados, lote.actualizados), (1, 1))

    def test_rechaza_columnas_faltantes(self):
        from .importacion import ArchivoInvalido
        with self.assertRaises(ArchivoInvalido):
            analizar_archivo(self.e, "productos", csv("p.csv", "codigo,nombre\nA,B\n"))


class ImportarClientesTest(TestCase):
    def test_detecta_posible_duplicado(self):
        e = Empresa.objects.create(nombre="Prueba")
        Cliente.objects.create(empresa=e, documento="900", nombre="Constructora Andes S.A.S.")
        lote = analizar_archivo(e, "clientes", csv("c.csv",
                                "nit,razon social,correo\n901,Constructora Andes SAS,compras@andes.co\n902,Otro,correo-malo\n"))
        self.assertEqual((lote.filas_validas, lote.filas_con_error), (1, 1))
        self.assertTrue(any("posible duplicado" in a for a in lote.datos[0]["advertencias"]))
