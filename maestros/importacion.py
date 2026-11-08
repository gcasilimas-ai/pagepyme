"""Importación de datos desde Excel o CSV (extracción, transformación, validación y carga).

El proceso se divide en dos pasos para que el usuario revise antes de cargar:

1. ``analizar_archivo`` extrae las filas, normaliza encabezados y valores,
   valida cada fila y detecta duplicados. El resultado se guarda en un
   ``LoteImportacion`` en estado *pendiente*.
2. ``aplicar_lote`` carga en una sola transacción las filas válidas y deja el
   lote como *aplicado*, con el conteo de registros creados y actualizados.
"""
import csv
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from openpyxl import Workbook, load_workbook

from inventario.models import MovimientoInventario
from inventario.servicios import registrar_movimiento

from . import historico
from .models import Categoria, Cliente, LoteImportacion, Producto

COLUMNAS = {
    "productos": {
        "requeridas": ["codigo", "nombre", "costo_unitario", "precio_venta"],
        "opcionales": ["categoria", "unidad", "stock_minimo"],
        "ejemplo": [
            ["CAS-001", "Casco de seguridad dieléctrico tipo II", "Protección de cabeza", "unidad", 28000, 42000, 10],
            ["GUA-010", "Guante de nitrilo calibre 18", "Protección de manos", "par", 6500, 11000, 40],
        ],
    },
    "clientes": {
        "requeridas": ["documento", "nombre"],
        "opcionales": ["tipo_documento", "ciudad", "telefono", "email", "plazo_credito_dias"],
        "ejemplo": [
            ["900123456", "Constructora Ejemplo S.A.S.", "NIT", "Neiva", "3100000000", "compras@ejemplo.com", 30],
            ["1075000000", "Juan Pérez", "CC", "Pitalito", "3200000000", "", 0],
        ],
    },
    "ventas": {
        "requeridas": ["fecha", "factura", "documento_cliente", "codigo", "cantidad", "precio_unitario"],
        "opcionales": ["nombre_cliente", "forma_pago", "saldo_pendiente", "fecha_pago"],
        "ejemplo": [
            ["15/08/2026", "FE-1201", "900123456", "Constructora Ejemplo S.A.S.", "CAS-001", 10, 42000, "credito", 0, "14/09/2026"],
            ["15/08/2026", "FE-1201", "900123456", "Constructora Ejemplo S.A.S.", "GUA-010", 24, 11000, "credito", 0, "14/09/2026"],
            ["16/08/2026", "FE-1202", "1075000000", "Juan Pérez", "GUA-010", 2, 11000, "contado", "", ""],
            ["02/10/2026", "FE-1310", "900123456", "Constructora Ejemplo S.A.S.", "CAS-001", 5, 42000, "credito", 210000, ""],
        ],
    },
    "compras": {
        "requeridas": ["fecha", "codigo", "cantidad", "costo_unitario"],
        "opcionales": ["proveedor", "documento"],
        "ejemplo": [
            ["01/08/2026", "CAS-001", 30, 28000, "Distribuidora Nacional EPP", "FC-889"],
            ["20/09/2026", "GUA-010", 200, 6500, "Guantes del Sur", "FC-1023"],
        ],
    },
    "inventario": {
        "requeridas": ["codigo", "cantidad"],
        "opcionales": ["costo_unitario"],
        "ejemplo": [["CAS-001", 25, 28000], ["GUA-010", 120, 6500]],
    },
}

# Orden recomendado de carga para una empresa nueva.
ORDEN_CARGA = ["productos", "clientes", "ventas", "compras", "inventario"]

ALIAS = {
    "cod": "codigo", "codigo_producto": "codigo", "referencia": "codigo", "sku": "codigo",
    "producto": "nombre", "descripcion": "nombre", "razon_social": "nombre", "nombre_cliente": "nombre",
    "costo": "costo_unitario", "costo_unidad": "costo_unitario", "valor_costo": "costo_unitario",
    "precio": "precio_venta", "precio_unitario": "precio_venta", "valor_venta": "precio_venta",
    "linea": "categoria", "grupo": "categoria", "unidad_de_medida": "unidad", "um": "unidad",
    "minimo": "stock_minimo", "stock_min": "stock_minimo", "inventario_minimo": "stock_minimo",
    "nit": "documento", "cedula": "documento", "identificacion": "documento", "numero_documento": "documento",
    "tipo": "tipo_documento", "tipo_doc": "tipo_documento", "correo": "email", "correo_electronico": "email",
    "celular": "telefono", "plazo": "plazo_credito_dias", "dias_credito": "plazo_credito_dias",
    "existencias": "cantidad", "stock": "cantidad", "unidades": "cantidad",
    # Variantes frecuentes en los archivos de las pymes
    "precio_de_venta": "precio_venta", "precio_venta_unitario": "precio_venta", "pvp": "precio_venta",
    "valor_de_venta": "precio_venta", "costo_de_compra": "costo_unitario", "costo_promedio": "costo_unitario",
    "valor_compra": "costo_unitario", "costo_unitario_promedio": "costo_unitario", "existencia_minima": "stock_minimo",
    "linea_de_producto": "categoria", "codigo_de_producto": "codigo", "cod_producto": "codigo", "item": "codigo",
    "dias_de_credito": "plazo_credito_dias", "plazo_de_credito": "plazo_credito_dias", "plazo_credito": "plazo_credito_dias",
    "credito_dias": "plazo_credito_dias", "numero_de_documento": "documento", "nit_cc": "documento",
    "nit_o_cedula": "documento", "documento_de_identidad": "documento", "telefono_celular": "telefono",
    "movil": "telefono", "existencia": "cantidad", "cantidad_disponible": "cantidad", "stock_actual": "cantidad",
    "unidad_medida": "unidad",
}

# Encabezados que significan algo distinto según el tipo de archivo.
ALIAS_TIPO = {
    "ventas": {
        "numero_factura": "factura", "no_factura": "factura", "n_factura": "factura", "factura_no": "factura",
        "numero": "factura", "nro_factura": "factura", "documento": "documento_cliente", "nit_cliente": "documento_cliente",
        "cliente_documento": "documento_cliente", "cliente": "nombre_cliente", "nombre": "nombre_cliente",
        "producto": "nombre_producto", "descripcion": "nombre_producto", "precio": "precio_unitario",
        "precio_venta": "precio_unitario", "valor_unitario": "precio_unitario", "fecha_venta": "fecha",
        "fecha_factura": "fecha", "fecha_de_venta": "fecha", "pago": "forma_pago", "tipo_pago": "forma_pago",
        "condicion_pago": "forma_pago", "forma_de_pago": "forma_pago", "condicion": "forma_pago",
        "saldo": "saldo_pendiente", "saldo_por_cobrar": "saldo_pendiente", "debe": "saldo_pendiente",
        "fecha_de_pago": "fecha_pago", "cant": "cantidad", "fecha_de_factura": "fecha", "no_de_factura": "factura",
        "numero_de_factura": "factura", "factura_n": "factura", "nit_del_cliente": "documento_cliente",
        "documento_del_cliente": "documento_cliente", "cliente_nit": "documento_cliente", "condicion_de_pago": "forma_pago",
        "saldo_por_pagar": "saldo_pendiente", "cartera": "saldo_pendiente", "valor_pendiente": "saldo_pendiente",
    },
    "inventario": {"saldo": "cantidad", "inventario": "cantidad", "saldo_actual": "cantidad", "conteo": "cantidad"},
    "compras": {
        "fecha_compra": "fecha", "fecha_de_compra": "fecha", "valor_unitario": "costo_unitario",
        "precio_compra": "costo_unitario", "precio": "costo_unitario", "precio_venta": "costo_unitario",
        "nit_proveedor": "proveedor", "factura": "documento", "numero_factura": "documento",
        "producto": "nombre_producto", "descripcion": "nombre_producto", "nombre": "nombre_producto", "cant": "cantidad",
    },
}

UNIDADES = {u for u, _ in Producto.UNIDADES}


class ArchivoInvalido(Exception):
    pass


def clave(texto):
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", texto.strip().lower()).strip("_")


def normalizar_encabezado(texto, tipo=None):
    texto = clave(texto)
    propios = ALIAS_TIPO.get(tipo, {})
    if texto in propios:
        return propios[texto]
    general = ALIAS.get(texto, texto)
    return propios.get(general, general)


def limpiar_texto(valor):
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return re.sub(r"\s+", " ", str(valor)).strip()


def normalizar_nombre(texto):
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    texto = re.sub(r"\b(s\.?a\.?s\.?|s\.?a\.?|ltda\.?|e\.?u\.?)\b", "", texto)
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def convertir_numero(valor):
    """Convierte formatos usuales en Colombia: 12.000 · 12.000,50 · $ 1.234 · 1234.5"""
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float, Decimal)):
        return Decimal(str(valor))
    texto = re.sub(r"[\s$]", "", str(valor))
    if not texto:
        return None
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        partes = texto.split(",")
        texto = texto.replace(",", ".") if len(partes) == 2 and len(partes[1]) in (1, 2) else texto.replace(",", "")
    elif texto.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", texto):
        texto = texto.replace(".", "")
    try:
        return Decimal(texto)
    except InvalidOperation:
        raise ValueError(f"'{valor}' no es un número válido")


def leer_filas(archivo):
    nombre = (archivo.name or "").lower()
    if nombre.endswith(".xlsx"):
        try:
            libro = load_workbook(archivo, read_only=True, data_only=True)
        except Exception as exc:  # archivo dañado o con otro formato
            raise ArchivoInvalido("No se pudo leer el archivo de Excel.") from exc
        hoja = libro.worksheets[0]
        return [list(f) for f in hoja.iter_rows(values_only=True)]
    if nombre.endswith(".csv"):
        contenido = archivo.read()
        for codificacion in ("utf-8-sig", "latin-1"):
            try:
                texto = contenido.decode(codificacion)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialecto = csv.Sniffer().sniff(texto[:2048], delimiters=",;\t")
        except csv.Error:
            dialecto = csv.excel
        return [fila for fila in csv.reader(io.StringIO(texto), dialecto)]
    raise ArchivoInvalido("Formato no admitido. Usa un archivo .xlsx o .csv.")


def _quitar_filas_vacias(filas):
    return [f for f in filas if any(limpiar_texto(c) for c in f)]


def analizar_archivo(empresa, tipo, archivo, usuario=None):
    especificacion = COLUMNAS[tipo]
    filas = _quitar_filas_vacias(leer_filas(archivo))
    if len(filas) < 2:
        raise ArchivoInvalido("El archivo no tiene filas de datos debajo de los encabezados.")

    encabezados = [normalizar_encabezado(c, tipo) for c in filas[0]]
    faltantes = [c for c in especificacion["requeridas"] if c not in encabezados]
    if faltantes:
        raise ArchivoInvalido(
            "Faltan columnas obligatorias: " + ", ".join(faltantes)
            + ". Descarga la plantilla para ver el formato esperado."
        )
    conocidas = set(especificacion["requeridas"]) | set(especificacion["opcionales"])
    indices = {c: i for i, c in enumerate(encabezados) if c in conocidas}

    utilidades = {"limpiar": limpiar_texto, "numero": _numero, "clave": clave}
    analizador = {
        "productos": _validar_producto, "clientes": _validar_cliente, "inventario": _validar_inventario,
        "ventas": lambda crudo, ctx: historico.validar_venta(crudo, ctx, utilidades),
        "compras": lambda crudo, ctx: historico.validar_compra(crudo, ctx, utilidades),
    }[tipo]
    contexto = _contexto_validacion(empresa, tipo)

    validas, errores = [], []
    for numero, fila in enumerate(filas[1:], start=2):
        crudo = {c: (fila[i] if i < len(fila) else None) for c, i in indices.items()}
        mensajes, advertencias, datos = analizador(crudo, contexto)
        if mensajes:
            errores.append({"fila": numero, "mensajes": mensajes, "valores": {k: limpiar_texto(v) for k, v in crudo.items()}})
        else:
            validas.append({"fila": numero, "datos": datos, "advertencias": advertencias})
    if tipo == "ventas":
        validas = historico.consolidar_facturas(validas, errores)

    return LoteImportacion.objects.create(
        empresa=empresa,
        usuario=usuario,
        tipo=tipo,
        archivo_nombre=archivo.name[:200],
        total_filas=len(filas) - 1,
        filas_validas=len(validas),
        filas_con_error=len(errores),
        errores=errores,
        datos=validas,
    )


def _contexto_validacion(empresa, tipo):
    if tipo == "ventas":
        return historico.contexto_ventas(empresa)
    if tipo == "compras":
        return historico.contexto_compras(empresa)
    contexto = {"vistos": {}}
    if tipo in ("productos", "inventario"):
        contexto["productos"] = {p.codigo.upper(): p for p in Producto.objects.filter(empresa=empresa)}
    if tipo == "clientes":
        clientes = list(Cliente.objects.filter(empresa=empresa))
        contexto["clientes"] = {c.documento: c for c in clientes}
        contexto["nombres"] = {normalizar_nombre(c.nombre): c for c in clientes}
    return contexto


def _numero(crudo, campo, mensajes, requerido=False, entero=False, minimo=Decimal("0")):
    try:
        valor = convertir_numero(crudo.get(campo))
    except ValueError as exc:
        mensajes.append(f"{campo}: {exc}")
        return None
    if valor is None:
        if requerido:
            mensajes.append(f"{campo}: es obligatorio")
        return None
    if valor < minimo:
        mensajes.append(f"{campo}: no puede ser menor que {minimo}")
        return None
    if entero:
        if valor != valor.to_integral_value():
            mensajes.append(f"{campo}: debe ser un número entero")
            return None
        return int(valor)
    return str(valor.quantize(Decimal("0.01")))


def _duplicado(contexto, clave, fila_actual):
    vistos = contexto["vistos"]
    if clave in vistos:
        return vistos[clave]
    vistos[clave] = fila_actual
    return None


def _validar_producto(crudo, contexto):
    mensajes, advertencias = [], []
    codigo = limpiar_texto(crudo.get("codigo")).upper()
    nombre = limpiar_texto(crudo.get("nombre"))
    if not codigo:
        mensajes.append("codigo: es obligatorio")
    if not nombre:
        mensajes.append("nombre: es obligatorio")
    costo = _numero(crudo, "costo_unitario", mensajes, requerido=True)
    precio = _numero(crudo, "precio_venta", mensajes, requerido=True)
    stock_minimo = _numero(crudo, "stock_minimo", mensajes, entero=True) if crudo.get("stock_minimo") not in (None, "") else 0
    unidad = normalizar_encabezado(crudo.get("unidad")) or "unidad"
    if unidad not in UNIDADES:
        advertencias.append(f"unidad '{limpiar_texto(crudo.get('unidad'))}' no reconocida; se usará 'unidad'")
        unidad = "unidad"
    if codigo:
        previa = _duplicado(contexto, codigo, len(contexto["vistos"]) + 2)
        if previa:
            mensajes.append("codigo: está repetido dentro del archivo")
    if not mensajes and Decimal(precio) < Decimal(costo):
        advertencias.append("el precio de venta es menor que el costo (margen negativo)")
    if not mensajes and Decimal(costo) == 0:
        advertencias.append("costo en cero: el margen de este producto no será confiable")
    accion = "actualizar" if codigo in contexto["productos"] else "crear"
    datos = {
        "codigo": codigo, "nombre": nombre, "categoria": limpiar_texto(crudo.get("categoria")),
        "unidad": unidad, "costo_unitario": costo, "precio_venta": precio,
        "stock_minimo": stock_minimo or 0, "accion": accion,
    }
    return mensajes, advertencias, datos


def _validar_cliente(crudo, contexto):
    mensajes, advertencias = [], []
    documento = re.sub(r"[\s.]", "", limpiar_texto(crudo.get("documento")))
    nombre = limpiar_texto(crudo.get("nombre"))
    if not documento:
        mensajes.append("documento: es obligatorio")
    if not nombre:
        mensajes.append("nombre: es obligatorio")
    tipo_documento = limpiar_texto(crudo.get("tipo_documento")).upper() or "NIT"
    if tipo_documento not in {"NIT", "CC", "CE"}:
        advertencias.append(f"tipo de documento '{tipo_documento}' no reconocido; se usará NIT")
        tipo_documento = "NIT"
    email = limpiar_texto(crudo.get("email")).lower()
    if email:
        try:
            validate_email(email)
        except ValidationError:
            mensajes.append(f"email: '{email}' no es un correo válido")
    plazo = _numero(crudo, "plazo_credito_dias", mensajes, entero=True) if crudo.get("plazo_credito_dias") not in (None, "") else 0
    if documento and _duplicado(contexto, documento, 0):
        mensajes.append("documento: está repetido dentro del archivo")
    if nombre and not mensajes:
        similar = contexto["nombres"].get(normalizar_nombre(nombre))
        if similar and similar.documento != documento:
            advertencias.append(f"posible duplicado: ya existe '{similar.nombre}' con documento {similar.documento}")
    accion = "actualizar" if documento in contexto["clientes"] else "crear"
    datos = {
        "documento": documento, "nombre": nombre, "tipo_documento": tipo_documento,
        "ciudad": limpiar_texto(crudo.get("ciudad")), "telefono": limpiar_texto(crudo.get("telefono")),
        "email": email, "plazo_credito_dias": plazo or 0, "accion": accion,
    }
    return mensajes, advertencias, datos


def _validar_inventario(crudo, contexto):
    mensajes, advertencias = [], []
    codigo = limpiar_texto(crudo.get("codigo")).upper()
    producto = contexto["productos"].get(codigo)
    if not codigo:
        mensajes.append("codigo: es obligatorio")
    elif producto is None:
        mensajes.append(f"codigo: el producto {codigo} no existe; impórtalo primero")
    cantidad = _numero(crudo, "cantidad", mensajes, requerido=True, entero=True, minimo=Decimal("1"))
    costo = _numero(crudo, "costo_unitario", mensajes) if crudo.get("costo_unitario") not in (None, "") else None
    if codigo and _duplicado(contexto, codigo, 0):
        mensajes.append("codigo: está repetido dentro del archivo")
    if producto is not None and costo is None:
        advertencias.append(f"sin costo: se usará el costo registrado ({producto.costo_unitario:.0f})")
    if producto is not None and producto.stock_actual > 0:
        advertencias.append(f"el producto ya tiene {producto.stock_actual} unidades; esta carga se suma a ellas")
    datos = {"codigo": codigo, "cantidad": cantidad, "costo_unitario": costo, "accion": "entrada"}
    return mensajes, advertencias, datos


@transaction.atomic
def aplicar_lote(lote, usuario=None):
    if lote.estado != "pendiente":
        raise ValueError("Este lote ya fue procesado.")
    empresa = lote.empresa
    creados = actualizados = 0
    notas = []

    if lote.tipo == "productos":
        categorias = {c.nombre.lower(): c for c in Categoria.objects.filter(empresa=empresa)}
        for fila in lote.datos:
            d = fila["datos"]
            categoria = None
            if d["categoria"]:
                categoria = categorias.get(d["categoria"].lower())
                if categoria is None:
                    categoria = Categoria.objects.create(empresa=empresa, nombre=d["categoria"])
                    categorias[d["categoria"].lower()] = categoria
            valores = {
                "nombre": d["nombre"], "categoria": categoria, "unidad": d["unidad"],
                "costo_unitario": Decimal(d["costo_unitario"]), "precio_venta": Decimal(d["precio_venta"]),
                "stock_minimo": d["stock_minimo"],
            }
            _, nuevo = Producto.objects.update_or_create(empresa=empresa, codigo=d["codigo"], defaults=valores)
            creados += nuevo
            actualizados += not nuevo

    elif lote.tipo == "clientes":
        for fila in lote.datos:
            d = dict(fila["datos"])
            documento = d.pop("documento")
            d.pop("accion", None)
            _, nuevo = Cliente.objects.update_or_create(empresa=empresa, documento=documento, defaults=d)
            creados += nuevo
            actualizados += not nuevo

    elif lote.tipo == "inventario":
        productos = {p.codigo.upper(): p for p in Producto.objects.filter(empresa=empresa)}
        for fila in lote.datos:
            d = fila["datos"]
            producto = productos[d["codigo"]]
            costo = Decimal(d["costo_unitario"]) if d["costo_unitario"] else producto.costo_unitario
            registrar_movimiento(
                empresa, producto, MovimientoInventario.ENTRADA, d["cantidad"], costo_unitario=costo,
                usuario=usuario, observacion=f"Inventario actual (lote de importación {lote.pk})",
                origen=MovimientoInventario.CONTEO,
            )
            creados += 1
        if MovimientoInventario.objects.filter(empresa=empresa, origen=MovimientoInventario.HISTORICO).exists():
            notas = historico.reconstruir_inventario(empresa)

    elif lote.tipo == "ventas":
        creados, notas = historico.aplicar_ventas(lote, usuario, _siguiente_numero_venta(empresa))

    elif lote.tipo == "compras":
        creados, notas = historico.aplicar_compras(lote, usuario)

    lote.estado = "aplicado"
    lote.creados = creados
    lote.actualizados = actualizados
    lote.notas = notas
    lote.save(update_fields=["estado", "creados", "actualizados", "notas"])
    return lote


def _siguiente_numero_venta(empresa):
    from comercial.models import Venta
    ultimo = Venta.objects.select_for_update().filter(empresa=empresa).aggregate(m=Max("numero"))["m"]
    return (ultimo or 0) + 1


DESCRIPCIONES = {
    "codigo": "Código o referencia del producto (el mismo del archivo de productos).",
    "nombre": "Nombre del producto o razón social del cliente.",
    "categoria": "Línea de producto (por ejemplo: Protección de manos).",
    "unidad": "unidad, par, caja, rollo, metro, kit o galón.",
    "costo_unitario": "Costo por unidad en pesos, sin IVA.",
    "precio_venta": "Precio de venta por unidad en pesos, sin IVA.",
    "stock_minimo": "Existencia mínima deseada; por debajo se genera alerta.",
    "documento": "NIT o cédula (clientes) o número de factura del proveedor (compras).",
    "tipo_documento": "NIT, CC o CE.",
    "ciudad": "Ciudad del cliente.",
    "telefono": "Teléfono o celular.",
    "email": "Correo electrónico.",
    "plazo_credito_dias": "Días de crédito que se le dan al cliente (0 si solo compra de contado).",
    "cantidad": "Unidades (número entero).",
    "fecha": "Fecha en formato dd/mm/aaaa. Se admiten datos desde el 1 de enero de 2024.",
    "factura": "Número de la factura. Una factura con varios productos ocupa varias filas con el mismo número.",
    "documento_cliente": "NIT o cédula del cliente que compró.",
    "nombre_cliente": "Nombre del cliente; si no existe en el maestro, se crea con este nombre.",
    "precio_unitario": "Precio por unidad facturado, sin IVA.",
    "forma_pago": "contado o credito. Si se deja vacío se toma como contado.",
    "saldo_pendiente": "Lo que el cliente aún debe de esa factura hoy (0 o vacío si ya la pagó).",
    "fecha_pago": "Fecha en que se terminó de pagar una factura a crédito (opcional).",
    "proveedor": "Nombre del proveedor (opcional).",
}


def generar_plantilla(tipo):
    from openpyxl.styles import Font
    especificacion = COLUMNAS[tipo]
    libro = Workbook()
    hoja = libro.active
    hoja.title = dict(LoteImportacion.TIPOS).get(tipo, tipo.capitalize())[:31]
    orden = _orden_natural(tipo)
    hoja.append(orden)
    for celda in hoja[1]:
        celda.font = Font(bold=celda.value in especificacion["requeridas"])
    natural = _orden_natural(tipo)
    for ejemplo in especificacion["ejemplo"]:
        fila = dict(zip(natural, ejemplo))
        hoja.append([fila.get(c, "") for c in orden])
    for columna in hoja.columns:
        hoja.column_dimensions[columna[0].column_letter].width = 22
    ayuda = libro.create_sheet("Instrucciones")
    ayuda.append(["Columna", "¿Obligatoria?", "Descripción"])
    for celda in ayuda[1]:
        celda.font = Font(bold=True)
    for c in orden:
        ayuda.append([c, "Sí" if c in especificacion["requeridas"] else "No", DESCRIPCIONES.get(c, "")])
    ayuda.append([])
    ayuda.append(["Las filas de ejemplo de la primera hoja deben reemplazarse por los datos de la empresa."])
    ayuda.column_dimensions["A"].width = 20
    ayuda.column_dimensions["B"].width = 14
    ayuda.column_dimensions["C"].width = 90
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()


def _orden_natural(tipo):
    return {
        "productos": ["codigo", "nombre", "categoria", "unidad", "costo_unitario", "precio_venta", "stock_minimo"],
        "clientes": ["documento", "nombre", "tipo_documento", "ciudad", "telefono", "email", "plazo_credito_dias"],
        "inventario": ["codigo", "cantidad", "costo_unitario"],
        "ventas": ["fecha", "factura", "documento_cliente", "nombre_cliente", "codigo", "cantidad", "precio_unitario",
                   "forma_pago", "saldo_pendiente", "fecha_pago"],
        "compras": ["fecha", "codigo", "cantidad", "costo_unitario", "proveedor", "documento"],
    }[tipo]
