import io
from collections import defaultdict
from datetime import date, timedelta

from django.db.models import Count, F, Max, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font

from comercial.models import ItemVenta, Venta
from core.actividad import registrar
from core.models import RegistroActividad, RespuestaSUS
from core.permisos import requiere
from inventario.models import MovimientoInventario
from maestros.importacion import normalizar_nombre
from maestros.models import Cliente, Producto

from . import indicadores as ind
from .models import FactMovimiento, FactRecaudo, FactVenta

PERIODOS = [
    ("30", "Últimos 30 días"),
    ("90", "Últimos 90 días"),
    ("mes", "Mes en curso"),
    ("mes_anterior", "Mes anterior"),
    ("anio", "Año a la fecha"),
    ("personalizado", "Personalizado"),
]


def fecha_referencia(empresa):
    """Para la empresa de demostración el periodo termina en su último registro."""
    hoy = timezone.localdate()
    if empresa.es_demo:
        ultima = Venta.objects.filter(empresa=empresa).aggregate(m=Max("fecha"))["m"]
        if ultima:
            return min(ultima, hoy)
    return hoy


def resolver_periodo(request, empresa):
    referencia = fecha_referencia(empresa)
    clave = request.GET.get("periodo", "30")
    inicio, fin = referencia - timedelta(days=29), referencia
    if clave == "90":
        inicio = referencia - timedelta(days=89)
    elif clave == "mes":
        inicio = referencia.replace(day=1)
    elif clave == "mes_anterior":
        fin = referencia.replace(day=1) - timedelta(days=1)
        inicio = fin.replace(day=1)
    elif clave == "anio":
        inicio = date(referencia.year, 1, 1)
    elif clave == "personalizado":
        try:
            inicio = date.fromisoformat(request.GET.get("desde", ""))
            fin = date.fromisoformat(request.GET.get("hasta", ""))
            if inicio > fin:
                inicio, fin = fin, inicio
        except ValueError:
            clave = "30"
            inicio, fin = referencia - timedelta(days=29), referencia
    else:
        clave = "30"
    return clave, inicio, fin


@requiere("tablero")
def tablero(request):
    empresa = request.empresa
    clave, inicio, fin = resolver_periodo(request, empresa)
    indicadores, base = ind.calcular_indicadores(empresa, inicio, fin)

    hay_productos = Producto.objects.filter(empresa=empresa).exists()
    hay_ventas_historicas = Venta.objects.filter(empresa=empresa, estado=Venta.EMITIDA).exists()
    ultima_venta = Venta.objects.filter(empresa=empresa, estado=Venta.EMITIDA).aggregate(m=Max("fecha"))["m"]

    primeros_pasos = [
        {"texto": "Registrar o importar los productos con su costo y precio", "hecho": hay_productos,
         "enlace": "maestros:importar"},
        {"texto": "Cargar el inventario inicial", "hecho": MovimientoInventario.objects.filter(empresa=empresa).exists(),
         "enlace": "inventario:movimiento_nuevo"},
        {"texto": "Registrar o importar los clientes", "hecho": Cliente.objects.filter(empresa=empresa).exists(),
         "enlace": "maestros:clientes"},
        {"texto": "Emitir la primera venta", "hecho": hay_ventas_historicas, "enlace": "comercial:venta_nueva"},
    ]

    if request.GET:
        registrar(request, "tablero", "Consulta del tablero", f"{inicio:%Y-%m-%d} a {fin:%Y-%m-%d}")

    contexto = {
        "periodos": PERIODOS,
        "periodo": clave,
        "inicio": inicio,
        "fin": fin,
        "dias": (fin - inicio).days + 1,
        "anterior": ind.periodo_anterior(inicio, fin),
        "indicadores": indicadores,
        "base": base,
        "acciones": ind.acciones_sugeridas(empresa, indicadores, inicio, fin),
        "serie_semanal": ind.serie_semanal(empresa, inicio, fin),
        "cartera": ind.cartera_por_antiguedad(empresa, fin),
        "top_productos": ind.top_productos_contribucion(empresa, inicio, fin),
        "margen_categorias": ind.margen_por_categoria(empresa, inicio, fin),
        "alertas": list(ind.alertas_stock(empresa)[:8]),
        "morosos": ind.clientes_con_cartera_vencida(empresa, fin),
        "meta_margen": float(empresa.meta_margen),
        "primeros_pasos": primeros_pasos,
        "pasos_pendientes": not all(p["hecho"] for p in primeros_pasos),
        "sin_ventas_periodo": base["n_ventas"] == 0,
        "ultima_venta": ultima_venta,
    }
    return render(request, "analitica/tablero.html", contexto)


# --------------------------------------------------------------------------- calidad de datos

def revisiones_calidad(empresa):
    hoy = timezone.localdate()
    productos = Producto.objects.filter(empresa=empresa, activo=True)
    n_productos = productos.count()
    clientes = Cliente.objects.filter(empresa=empresa, activo=True)
    n_clientes = clientes.count()

    grupos = defaultdict(list)
    for c in clientes:
        grupos[normalizar_nombre(c.nombre)].append(c)
    duplicados = [g for g in grupos.values() if len(g) > 1]

    stock_movs = {
        f["producto_id"]: f["s"] or 0
        for f in FactMovimiento.objects.filter(empresa=empresa).values("producto_id").annotate(s=Sum("cantidad_neta"))
    }
    descuadres = [p for p in Producto.objects.filter(empresa=empresa) if p.stock_actual != stock_movs.get(p.id, 0)]

    sumas = {
        v["venta_id"]: v["s"]
        for v in ItemVenta.objects.filter(venta__empresa=empresa).values("venta_id").annotate(s=Sum(F("cantidad") * F("precio_unitario")))
    }
    ventas_inconsistentes = [
        v for v in Venta.objects.filter(empresa=empresa, estado=Venta.EMITIDA).only("id", "total")
        if abs((sumas.get(v.id) or 0) - v.total) > 1
    ]

    hace_30 = hoy - timedelta(days=30)
    movs_30 = MovimientoInventario.objects.filter(empresa=empresa, fecha__gte=hace_30)
    ajustes_30 = movs_30.filter(tipo__in=[MovimientoInventario.AJUSTE_POSITIVO, MovimientoInventario.AJUSTE_NEGATIVO]).count()
    total_movs_30 = movs_30.count()

    ultima_venta = Venta.objects.filter(empresa=empresa).aggregate(m=Max("fecha"))["m"]
    ultima_entrada = MovimientoInventario.objects.filter(empresa=empresa, tipo=MovimientoInventario.ENTRADA).aggregate(m=Max("fecha"))["m"]

    credito_sin_contacto = clientes.filter(plazo_credito_dias__gt=0, telefono="", email="").count()
    vencidas_90 = sum(1 for v in ind.ventas_credito_con_saldo(empresa, hoy) if v.dias_vencida_corte > 90)

    def revision(clave, nombre, problema, total, impacto, enlace=None, umbral=0, informativa=False):
        return {
            "clave": clave, "nombre": nombre, "problemas": problema, "total": total,
            "ok": problema <= umbral, "impacto": impacto, "enlace": enlace, "informativa": informativa,
        }

    return [
        revision("costo", "Productos sin costo registrado", productos.filter(costo_unitario=0).count(), n_productos,
                 "Sin costo, el margen y la rotación de ese producto salen inflados.", "maestros:productos"),
        revision("precio", "Productos con precio menor o igual al costo",
                 productos.filter(precio_venta__lte=F("costo_unitario"), precio_venta__gt=0).count(), n_productos,
                 "Cada venta de estos productos reduce el margen de contribución.", "maestros:productos"),
        revision("categoria", "Productos sin categoría", productos.filter(categoria__isnull=True).count(), n_productos,
                 "Quedan por fuera del análisis de margen por línea de producto.", "maestros:productos"),
        revision("minimo", "Productos sin stock mínimo definido", productos.filter(stock_minimo=0).count(), n_productos,
                 "Sin stock mínimo no se generan alertas de reposición.", "maestros:productos"),
        revision("descuadre", "Stock que no coincide con la suma de movimientos", len(descuadres),
                 Producto.objects.filter(empresa=empresa).count(),
                 "Prueba de integridad: el stock de cada producto debe ser igual a la suma de sus movimientos."),
        revision("ventas", "Ventas cuyo total no coincide con sus líneas", len(ventas_inconsistentes),
                 Venta.objects.filter(empresa=empresa, estado=Venta.EMITIDA).count(),
                 "Prueba de integridad entre el encabezado y el detalle de cada venta."),
        revision("duplicados", "Grupos de clientes con nombre posiblemente duplicado", len(duplicados), n_clientes,
                 "Un cliente duplicado divide su historial y altera el indicador de recurrencia.", "maestros:clientes"),
        revision("contacto", "Clientes con crédito sin teléfono ni correo", credito_sin_contacto,
                 clientes.filter(plazo_credito_dias__gt=0).count(),
                 "No se les puede gestionar el cobro cuando la cartera se vence.", "maestros:clientes"),
        revision("ajustes", "Ajustes de inventario en los últimos 30 días", ajustes_30, total_movs_30,
                 "Una proporción alta de ajustes indica movimientos que no se están registrando a tiempo.",
                 "inventario:movimientos", umbral=max(1, total_movs_30 // 10)),
        revision("frescura_ventas", "Días desde la última venta registrada",
                 (hoy - ultima_venta).days if ultima_venta else 0, None,
                 "Si se deja de registrar, el tablero muestra una situación desactualizada.",
                 umbral=7, informativa=True),
        revision("frescura_compras", "Días desde la última entrada de inventario",
                 (hoy - ultima_entrada).days if ultima_entrada else 0, None,
                 "Las compras no registradas hacen que el sistema reporte agotados que no existen.",
                 umbral=15, informativa=True),
        revision("vencidas", "Ventas a crédito con más de 90 días de mora", vencidas_90, None,
                 "Saldos con alto riesgo de no recuperarse.", "comercial:cartera", informativa=True),
    ], {"duplicados": duplicados[:10], "descuadres": descuadres[:10], "ventas": ventas_inconsistentes[:10]}


@requiere("evidencia")
def calidad(request):
    revisiones, detalles = revisiones_calidad(request.empresa)
    aprobadas = sum(1 for r in revisiones if r["ok"])
    return render(request, "analitica/calidad.html", {
        "revisiones": revisiones, "detalles": detalles,
        "aprobadas": aprobadas, "total": len(revisiones),
        "indice": round(aprobadas / len(revisiones) * 100),
    })


# --------------------------------------------------------------------------- exportación

def _libro(nombre_hoja, encabezados, filas):
    libro = Workbook()
    hoja = libro.active
    hoja.title = nombre_hoja[:31]
    hoja.append(encabezados)
    for celda in hoja[1]:
        celda.font = Font(bold=True)
    for fila in filas:
        hoja.append(list(fila))
    for i, _ in enumerate(encabezados, start=1):
        hoja.column_dimensions[hoja.cell(row=1, column=i).column_letter].width = 20
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()


CONJUNTOS = {
    "ventas": "Hechos de venta (una fila por línea de venta)",
    "movimientos": "Hechos de inventario (una fila por movimiento)",
    "recaudos": "Hechos de recaudo (una fila por abono)",
    "indicadores": "Indicadores del periodo con su fórmula y cálculo",
    "actividad": "Bitácora de uso de la plataforma",
    "sus": "Respuestas de la encuesta de usabilidad (SUS)",
}


@requiere("evidencia")
def exportar(request):
    clave, inicio, fin = resolver_periodo(request, request.empresa)
    return render(request, "analitica/exportar.html", {
        "conjuntos": CONJUNTOS, "periodos": PERIODOS, "periodo": clave, "inicio": inicio, "fin": fin,
    })


@requiere("evidencia")
def exportar_archivo(request, conjunto):
    if conjunto not in CONJUNTOS:
        raise Http404
    empresa = request.empresa
    _, inicio, fin = resolver_periodo(request, empresa)

    if conjunto == "ventas":
        qs = FactVenta.objects.filter(empresa=empresa, fecha_id__gte=inicio, fecha_id__lte=fin).values_list(
            "fecha_id", "venta__numero", "cliente__nombre", "producto__codigo", "producto__nombre",
            "categoria__nombre", "forma_pago", "cantidad", "ingreso", "costo",
        ).order_by("fecha_id", "venta__numero")
        contenido = _libro("Ventas", ["Fecha", "Venta", "Cliente", "Código", "Producto", "Categoría",
                                      "Forma de pago", "Cantidad", "Ingreso", "Costo"], qs)
    elif conjunto == "movimientos":
        qs = FactMovimiento.objects.filter(empresa=empresa, fecha_id__gte=inicio, fecha_id__lte=fin).values_list(
            "fecha_id", "producto__codigo", "producto__nombre", "tipo", "cantidad_neta", "valor_neto",
        ).order_by("fecha_id", "id")
        contenido = _libro("Movimientos", ["Fecha", "Código", "Producto", "Tipo", "Cantidad neta", "Valor neto"], qs)
    elif conjunto == "recaudos":
        qs = FactRecaudo.objects.filter(empresa=empresa, fecha_id__gte=inicio, fecha_id__lte=fin).values_list(
            "fecha_id", "venta__numero", "cliente__nombre", "forma_pago", "valor",
        ).order_by("fecha_id")
        contenido = _libro("Recaudos", ["Fecha", "Venta", "Cliente", "Forma de pago", "Valor"], qs)
    elif conjunto == "indicadores":
        lista, _ = ind.calcular_indicadores(empresa, inicio, fin)
        filas = [
            (i.nombre, i.valor, i.unidad, i.meta, "Más es mejor" if i.direccion == "mayor" else "Menos es mejor",
             i.cumplimiento, i.estado_texto, i.formula, i.calculo, i.anterior)
            for i in lista
        ]
        contenido = _libro("Indicadores", ["Indicador", "Valor", "Unidad", "Meta", "Sentido", "Cumplimiento (%)",
                                           "Estado", "Fórmula", "Cálculo", "Periodo anterior"], filas)
    elif conjunto == "actividad":
        qs = RegistroActividad.objects.filter(empresa=empresa, fecha__date__gte=inicio, fecha__date__lte=fin) \
            .select_related("usuario").order_by("fecha")
        filas = [(timezone.localtime(r.fecha).replace(tzinfo=None), r.usuario.get_username() if r.usuario else "",
                  r.modulo, r.accion, r.detalle) for r in qs]
        contenido = _libro("Actividad", ["Fecha y hora", "Usuario", "Módulo", "Acción", "Detalle"], filas)
    else:
        qs = RespuestaSUS.objects.filter(empresa=empresa).select_related("usuario").order_by("fecha")
        filas = [(timezone.localtime(r.fecha).replace(tzinfo=None), r.usuario.get_username() if r.usuario else "",
                  *r.respuestas, r.puntaje, r.comentario) for r in qs]
        contenido = _libro("SUS", ["Fecha", "Usuario", *[f"P{i}" for i in range(1, 11)], "Puntaje SUS", "Comentario"], filas)

    registrar(request, "evidencia", "Exportación", f"{conjunto} {inicio:%Y-%m-%d} a {fin:%Y-%m-%d}")
    respuesta = HttpResponse(contenido, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    respuesta["Content-Disposition"] = f'attachment; filename="pagepyme_{conjunto}_{inicio:%Y%m%d}_{fin:%Y%m%d}.xlsx"'
    return respuesta
