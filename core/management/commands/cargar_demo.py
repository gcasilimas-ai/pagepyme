"""Crea una empresa de demostración con datos ficticios.

Los datos se generan con una semilla fija (resultados reproducibles) y pasan
por las mismas reglas de negocio que usa la aplicación: inventario con costo
promedio ponderado, ventas que descuentan existencias y abonos que no superan
el saldo. La empresa queda marcada con ``es_demo=True`` y se excluye de los
resultados del piloto. No se generan registros de uso ni respuestas SUS.
"""
import random
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from comercial import servicios as com
from comercial.models import Cotizacion, ItemCotizacion, ItemVenta, Pago, Venta
from core.models import Empresa, Perfil, RegistroActividad, RespuestaSUS
from inventario.models import MovimientoInventario
from inventario.servicios import registrar_movimiento
from maestros.models import Categoria, Cliente, LoteImportacion, Producto

NOMBRE_DEMO = "Distribuidora Demo EPP (datos ficticios)"

# (categoría, meta de margen propia, [(código, nombre, unidad, costo, margen objetivo, stock mínimo, demanda relativa)])
CATALOGO = [
    ("Protección de cabeza", None, [
        ("CAB-001", "Casco de seguridad tipo I", "unidad", 21000, 0.38, 12, 5),
        ("CAB-002", "Casco dieléctrico tipo II", "unidad", 34000, 0.35, 8, 3),
        ("CAB-003", "Barbuquejo de tres puntos", "unidad", 4500, 0.48, 20, 4),
    ]),
    ("Protección visual", None, [
        ("VIS-001", "Gafas de seguridad lente claro", "unidad", 5200, 0.50, 30, 9),
        ("VIS-002", "Gafas de seguridad lente oscuro", "unidad", 5600, 0.50, 20, 5),
        ("VIS-003", "Monogafa de ventilación indirecta", "unidad", 9800, 0.42, 10, 3),
        ("VIS-004", "Careta facial con visor", "unidad", 18500, 0.36, 6, 1),
    ]),
    ("Protección auditiva", None, [
        ("AUD-001", "Tapón auditivo de espuma", "par", 650, 0.60, 200, 10),
        ("AUD-002", "Tapón reutilizable con cordón", "par", 2100, 0.52, 60, 5),
        ("AUD-003", "Protector auditivo tipo copa", "unidad", 23000, 0.34, 6, 2),
    ]),
    ("Protección respiratoria", None, [
        ("RES-001", "Respirador desechable N95", "unidad", 3900, 0.46, 80, 8),
        ("RES-002", "Respirador media cara", "unidad", 52000, 0.30, 5, 2),
        ("RES-003", "Cartucho para vapores orgánicos", "par", 38000, 0.31, 8, 2),
        ("RES-004", "Filtro P100", "par", 27000, 0.33, 8, 1),
    ]),
    ("Protección de manos", None, [
        ("MAN-001", "Guante de nitrilo calibre 18", "par", 6200, 0.42, 60, 9),
        ("MAN-002", "Guante de vaqueta", "par", 7800, 0.40, 50, 8),
        ("MAN-003", "Guante dieléctrico clase 0", "par", 165000, 0.24, 2, 1),
        ("MAN-004", "Guante anticorte nivel 5", "par", 15500, 0.36, 20, 3),
        ("MAN-005", "Guante de látex industrial", "par", 3600, 0.47, 40, 4),
    ]),
    ("Calzado de seguridad", Decimal("28.00"), [
        ("CAL-001", "Bota de caucho caña alta", "par", 39000, 0.28, 12, 4),
        ("CAL-002", "Bota de seguridad con puntera de acero", "par", 98000, 0.26, 8, 3),
        ("CAL-003", "Bota dieléctrica", "par", 132000, 0.25, 4, 1),
        ("CAL-004", "Zapato de seguridad liviano", "par", 86000, 0.27, 5, 1),
    ]),
    ("Trabajo en alturas", Decimal("25.00"), [
        ("ALT-001", "Arnés de cuerpo completo", "unidad", 118000, 0.21, 4, 2),
        ("ALT-002", "Eslinga de posicionamiento", "unidad", 64000, 0.22, 4, 1),
        ("ALT-003", "Eslinga con absorbedor de impacto", "unidad", 92000, 0.20, 4, 2),
        ("ALT-004", "Línea de vida vertical de 15 m", "unidad", 210000, 0.19, 1, 0.3),
        ("ALT-005", "Mosquetón de acero", "unidad", 19000, 0.30, 10, 1),
    ]),
    ("Ropa de trabajo y señalización", None, [
        ("ROP-001", "Chaleco reflectivo", "unidad", 9500, 0.45, 30, 6),
        ("ROP-002", "Overol de drill", "unidad", 54000, 0.32, 8, 2),
        ("ROP-003", "Impermeable de dos piezas", "unidad", 32000, 0.34, 8, 2),
        ("ROP-004", "Cono de señalización de 70 cm", "unidad", 26000, 0.33, 10, 1),
        ("ROP-005", "Cinta de peligro de 500 m", "rollo", 33000, 0.31, 6, 1),
    ]),
]

# Productos que no se reponen en las últimas semanas (generan quiebres de stock)
SIN_REPOSICION_FINAL = {"CAL-001", "ROP-001", "CAB-001", "MAN-003"}

# (nombre, documento, plazo de crédito, frecuencia relativa, comportamiento de pago)
CLIENTES = [
    ("Constructora Piedra Alta S.A.S.", "900000101", 30, 9, "puntual"),
    ("Obras y Vías del Sur S.A.S.", "900000102", 45, 7, "tardio"),
    ("Agroindustrial El Guadual S.A.S.", "900000103", 30, 6, "puntual"),
    ("Talleres Metalmecánicos La Ceiba", "900000104", 30, 5, "tardio"),
    ("Montajes Eléctricos Andaquí S.A.S.", "900000105", 60, 5, "puntual"),
    ("Petroservicios Yaguará S.A.S.", "900000106", 60, 4, "moroso"),
    ("Cooperativa Arrocera del Valle Alto", "900000107", 30, 4, "puntual"),
    ("Clínica Veterinaria San Roque", "900000108", 0, 2, "contado"),
    ("Ferretería La Gaitana", "900000109", 30, 6, "tardio"),
    ("Constructora Horizonte Opita S.A.S.", "900000110", 45, 3, "moroso"),
    ("Molinos y Granos del Huila S.A.S.", "900000111", 30, 3, "puntual"),
    ("Servicios Ambientales Magdalena", "900000112", 30, 2, "puntual"),
    ("Transportes Rápido Neiva S.A.S.", "900000113", 0, 3, "contado"),
    ("Alcaldía Municipal (contratista de obra)", "900000114", 60, 2, "tardio"),
    ("Taller Automotriz El Pistón", "900000115", 0, 3, "contado"),
    ("Panadería Industrial Doña Rosa", "900000116", 0, 1, "contado"),
    ("Invernaderos La Plata S.A.S.", "900000117", 30, 2, "puntual"),
    ("Ingeniería y Redes del Huila", "900000118", 45, 3, "puntual"),
    ("Carpintería Industrial Tello", "1075000119", 0, 1, "contado"),
    ("Estación de Servicio El Caguán", "900000120", 30, 2, "tardio"),
    ("Hotel Termales de Rivera", "900000121", 0, 1, "contado"),
    ("Piscícola Betania S.A.S.", "900000122", 30, 2, "puntual"),
    ("Soldaduras y Estructuras Garzón", "900000123", 30, 2, "moroso"),
    ("Clientes de mostrador", "222222222", 0, 6, "contado"),
]

CIUDADES = ["Neiva", "Neiva", "Neiva", "Pitalito", "Garzón", "La Plata", "Campoalegre", "Rivera", "Aipe", "Palermo"]
SEMANAS = 22


class Command(BaseCommand):
    help = "Crea (o recrea) la empresa de demostración con datos ficticios."

    def add_arguments(self, parser):
        parser.add_argument("--si-no-existe", action="store_true",
                            help="No hace nada si la empresa de demostración ya existe.")

    def handle(self, *args, **opciones):
        existente = Empresa.objects.filter(es_demo=True, nombre=NOMBRE_DEMO).first()
        if existente and opciones["si_no_existe"]:
            self.stdout.write("La empresa de demostración ya existe; no se modifica.")
            return
        with transaction.atomic():
            if existente:
                self._borrar(existente)
            empresa = self._crear()
        self.stdout.write(self.style.SUCCESS(f"Empresa de demostración lista: {empresa.nombre}"))

    def _borrar(self, empresa):
        Pago.objects.filter(venta__empresa=empresa).delete()
        MovimientoInventario.objects.filter(empresa=empresa).delete()
        ItemVenta.objects.filter(venta__empresa=empresa).delete()
        Venta.objects.filter(empresa=empresa).delete()
        ItemCotizacion.objects.filter(cotizacion__empresa=empresa).delete()
        Cotizacion.objects.filter(empresa=empresa).delete()
        for modelo in (Producto, Categoria, Cliente, LoteImportacion, RegistroActividad, RespuestaSUS):
            modelo.objects.filter(empresa=empresa).delete()
        empresa.delete()

    def _crear(self):
        rnd = random.Random(20261107)
        hoy = timezone.localdate()
        inicio = hoy - timedelta(weeks=SEMANAS)

        empresa = Empresa.objects.create(
            nombre=NOMBRE_DEMO, nit="000000000-0", ciudad="Neiva", departamento="Huila",
            numero_empleados=6, anios_operacion=8, herramienta_previa="Hojas de cálculo en Excel",
            fecha_vinculacion=inicio, es_demo=True, meta_ticket=Decimal("350000"),
        )
        usuarios = self._usuarios(empresa)
        gerente = usuarios["gerente"]

        productos = {}
        for nombre_cat, meta, items in CATALOGO:
            categoria = Categoria.objects.create(empresa=empresa, nombre=nombre_cat, meta_margen=meta)
            for codigo, nombre, unidad, costo, margen, minimo, demanda in items:
                precio = Decimal(round(costo / (1 - margen), -2))
                p = Producto.objects.create(
                    empresa=empresa, codigo=codigo, nombre=nombre, categoria=categoria, unidad=unidad,
                    costo_unitario=Decimal(costo), precio_venta=precio, stock_minimo=minimo,
                )
                p.demanda = demanda
                productos[codigo] = p
        Producto.objects.filter(empresa=empresa).update(
            creado=timezone.make_aware(datetime.combine(inicio - timedelta(days=1), time.min)))

        clientes = []
        for i, (nombre, documento, plazo, peso, conducta) in enumerate(CLIENTES):
            c = Cliente.objects.create(
                empresa=empresa, nombre=nombre, documento=documento,
                tipo_documento="CC" if documento.startswith("10") else "NIT",
                ciudad=CIUDADES[i % len(CIUDADES)], plazo_credito_dias=plazo,
                telefono="" if i in (9, 22) else f"310{rnd.randint(1000000, 9999999)}",
            )
            c.peso, c.conducta = peso, conducta
            clientes.append(c)

        # Inventario inicial
        for p in productos.values():
            cantidad = max(p.stock_minimo * rnd.choice([3, 4, 5]), 3)
            registrar_movimiento(empresa, p, MovimientoInventario.ENTRADA, cantidad, costo_unitario=p.costo_unitario,
                                 fecha=inicio - timedelta(days=1), usuario=gerente, observacion="Inventario inicial")

        lista_productos = list(productos.values())
        pesos_productos = [p.demanda for p in lista_productos]
        pesos_clientes = [c.peso for c in clientes]
        ultimas_semanas = hoy - timedelta(days=32)

        dia = inicio
        while dia <= hoy:
            dia_semana = dia.weekday()
            # Reposición los lunes (salvo productos que se dejan agotar al final)
            if dia_semana == 0:
                for p in lista_productos:
                    p.refresh_from_db(fields=["stock_actual", "costo_unitario"])
                    if p.codigo in SIN_REPOSICION_FINAL and dia >= ultimas_semanas:
                        continue
                    if p.stock_actual <= p.stock_minimo * 1.5:
                        cantidad = max(p.stock_minimo * rnd.choice([3, 4]) - p.stock_actual, 1)
                        costo = (p.costo_unitario * Decimal(str(rnd.uniform(0.97, 1.06)))).quantize(Decimal("1"))
                        registrar_movimiento(empresa, p, MovimientoInventario.ENTRADA, cantidad, costo_unitario=costo,
                                             fecha=dia, usuario=usuarios["bodega"], observacion="Compra a proveedor")
            if dia_semana < 6:
                n_ventas = rnd.choice([1, 2, 2, 3, 3, 4]) if dia_semana < 5 else rnd.choice([0, 1, 1])
                for _ in range(n_ventas):
                    self._venta(empresa, rnd, dia, clientes, pesos_clientes, lista_productos, pesos_productos, usuarios)
            if dia_semana == 3 and rnd.random() < 0.35:
                p = rnd.choice(lista_productos)
                p.refresh_from_db(fields=["stock_actual"])
                if p.stock_actual > 2:
                    registrar_movimiento(empresa, p, MovimientoInventario.AJUSTE_NEGATIVO, 1, fecha=dia,
                                         usuario=usuarios["bodega"], observacion="Unidad averiada en bodega")
            dia += timedelta(days=1)

        self._pagos(rnd, empresa, hoy, gerente)
        self._cotizaciones(rnd, empresa, inicio, hoy, clientes, lista_productos, usuarios)
        return empresa

    def _usuarios(self, empresa):
        User = get_user_model()
        resultado = {}
        for rol, nombre in (("gerente", "Gerente"), ("comercial", "Comercial"), ("bodega", "Bodega")):
            usuario, _ = User.objects.get_or_create(username=f"demo.{rol}")
            usuario.first_name = f"{nombre} (demo)"
            usuario.is_active = True
            usuario.set_password(settings.DEMO_CLAVE)
            usuario.save()
            Perfil.objects.update_or_create(usuario=usuario, defaults={"empresa": empresa, "rol": rol})
            resultado[rol] = usuario
        return resultado

    def _venta(self, empresa, rnd, dia, clientes, pesos_clientes, productos, pesos_productos, usuarios):
        cliente = rnd.choices(clientes, weights=pesos_clientes)[0]
        lineas = {}
        for p in rnd.choices(productos, weights=pesos_productos, k=rnd.choice([1, 1, 2, 2, 3, 4])):
            p.refresh_from_db(fields=["stock_actual"])
            base = {"par": 12, "unidad": 6, "rollo": 2}.get(p.unidad, 4)
            if p.costo_unitario > 80000:
                base = 2
            cantidad = min(rnd.randint(1, base * 2), p.stock_actual - lineas.get(p.pk, (p, 0))[1])
            if cantidad <= 0:
                continue
            cantidad_previa = lineas.get(p.pk, (p, 0))[1]
            lineas[p.pk] = (p, cantidad_previa + cantidad)
        if not lineas:
            return
        items = []
        for p, cantidad in lineas.values():
            descuento = Decimal("0.95") if cantidad >= 20 else Decimal("1")
            items.append((p, cantidad, (p.precio_venta * descuento).quantize(Decimal("1"))))
        credito = cliente.plazo_credito_dias > 0 and rnd.random() < 0.75
        com.crear_venta(
            empresa, cliente, items, forma_pago=Venta.CREDITO if credito else Venta.CONTADO, fecha=dia,
            usuario=rnd.choice([usuarios["comercial"], usuarios["gerente"]]),
            medio_pago=rnd.choice(["efectivo", "transferencia", "transferencia"]),
        )

    def _pagos(self, rnd, empresa, hoy, usuario):
        conductas = {documento: conducta for _, documento, _, _, conducta in CLIENTES}
        ventas = Venta.objects.filter(empresa=empresa, forma_pago=Venta.CREDITO).select_related("cliente")
        for venta in ventas:
            conducta = conductas.get(venta.cliente.documento, "puntual")
            plazo = (venta.fecha_vencimiento - venta.fecha).days
            if conducta == "puntual":
                dias = plazo + rnd.randint(-8, 4)
            elif conducta == "tardio":
                dias = plazo + rnd.randint(5, 40)
            else:
                dias = plazo + rnd.randint(30, 120)
            fecha_pago = venta.fecha + timedelta(days=max(1, dias))
            if conducta == "moroso" and rnd.random() < 0.45:
                continue
            if fecha_pago > hoy:
                continue
            if rnd.random() < 0.25:
                parcial = (venta.total * Decimal("0.5")).quantize(Decimal("1"))
                primera = venta.fecha + timedelta(days=max(1, dias // 2))
                if primera <= hoy:
                    com.registrar_abono(venta, parcial, fecha=primera, medio="transferencia", usuario=usuario)
                venta.refresh_from_db()
            saldo = venta.saldo
            if saldo > 0:
                com.registrar_abono(venta, saldo, fecha=fecha_pago, medio=rnd.choice(["transferencia", "consignacion"]),
                                    referencia="Pago cartera", usuario=usuario)

    def _cotizaciones(self, rnd, empresa, inicio, hoy, clientes, productos, usuarios):
        for _ in range(16):
            fecha = inicio + timedelta(days=rnd.randint(0, (hoy - inicio).days))
            cliente = rnd.choice([c for c in clientes if c.documento != "222222222"])
            items = [(p, rnd.randint(2, 20), p.precio_venta) for p in rnd.sample(productos, rnd.randint(2, 5))]
            cot = com.crear_cotizacion(empresa, cliente, items, fecha=fecha, usuario=usuarios["comercial"])
            destino = rnd.random()
            if destino < 0.35:
                cot.estado = Cotizacion.ENVIADA
            elif destino < 0.55:
                cot.estado = Cotizacion.RECHAZADA
            elif destino < 0.75:
                cot.estado = Cotizacion.ACEPTADA
            cot.save(update_fields=["estado"])
