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
from openpyxl import Workbook, load_workbook

from inventario.models import MovimientoInventario
from inventario.servicios import registrar_movimiento

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
    "inventario": {
        "requeridas": ["codigo", "cantidad"],
        "opcionales": ["costo_unitario"],
        "ejemplo": [["CAS-001", 25, 28000], ["GUA-010", 120, 6500]],
    },
}

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
}

UNIDADES = {u for u, _ in Producto.UNIDADES}


class ArchivoInvalido(Exception):
    pass


def normalizar_encabezado(texto):
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    texto = re.sub(r"[^a-z0-9]+", "_", texto.strip().lower()).strip("_")
    return ALIAS.get(texto, texto)


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

    encabezados = [normalizar_encabezado(c) for c in filas[0]]
    faltantes = [c for c in especificacion["requeridas"] if c not in encabezados]
    if faltantes:
        raise ArchivoInvalido(
            "Faltan columnas obligatorias: " + ", ".join(faltantes)
            + ". Descarga la plantilla para ver el formato esperado."
        )
    conocidas = set(especificacion["requeridas"]) | set(especificacion["opcionales"])
    indices = {c: i for i, c in enumerate(encabezados) if c in conocidas}

    analizador = {"productos": _validar_producto, "clientes": _validar_cliente, "inventario": _validar_inventario}[tipo]
    contexto = _contexto_validacion(empresa, tipo)

    validas, errores = [], []
    for numero, fila in enumerate(filas[1:], start=2):
        crudo = {c: (fila[i] if i < len(fila) else None) for c, i in indices.items()}
        mensajes, advertencias, datos = analizador(crudo, contexto)
        if mensajes:
            errores.append({"fila": numero, "mensajes": mensajes, "valores": {k: limpiar_texto(v) for k, v in crudo.items()}})
        else:
            validas.append({"fila": numero, "datos": datos, "advertencias": advertencias})

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
    datos = {"codigo": codigo, "cantidad": cantidad, "costo_unitario": costo, "accion": "entrada"}
    return mensajes, advertencias, datos


@transaction.atomic
def aplicar_lote(lote, usuario=None):
    if lote.estado != "pendiente":
        raise ValueError("Este lote ya fue procesado.")
    empresa = lote.empresa
    creados = actualizados = 0

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
                usuario=usuario, observacion=f"Inventario inicial (lote de importación {lote.pk})",
            )
            creados += 1

    lote.estado = "aplicado"
    lote.creados = creados
    lote.actualizados = actualizados
    lote.save(update_fields=["estado", "creados", "actualizados"])
    return lote


def generar_plantilla(tipo):
    especificacion = COLUMNAS[tipo]
    libro = Workbook()
    hoja = libro.active
    hoja.title = tipo.capitalize()
    hoja.append(especificacion["requeridas"] + especificacion["opcionales"])
    orden = especificacion["requeridas"] + especificacion["opcionales"]
    for ejemplo in especificacion["ejemplo"]:
        # Los ejemplos están escritos en el orden natural de lectura; se reordenan a las columnas.
        natural = _orden_natural(tipo)
        fila = dict(zip(natural, ejemplo))
        hoja.append([fila.get(c, "") for c in orden])
    for columna in hoja.columns:
        hoja.column_dimensions[columna[0].column_letter].width = 24
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()


def _orden_natural(tipo):
    return {
        "productos": ["codigo", "nombre", "categoria", "unidad", "costo_unitario", "precio_venta", "stock_minimo"],
        "clientes": ["documento", "nombre", "tipo_documento", "ciudad", "telefono", "email", "plazo_credito_dias"],
        "inventario": ["codigo", "cantidad", "costo_unitario"],
    }[tipo]
