"""Libro de Excel con la evidencia del piloto de todas las empresas vinculadas (solo equipo consultor).

Reúne en un solo archivo lo que el documento de grado necesita para reportar resultados:
caracterización, cargas desde Excel, calidad de datos, indicadores, ventas por mes, productos,
clientes (solo conteos, sin nombres), cartera, encuesta SUS y uso. No incluye datos personales
de los clientes de las empresas.
"""
import io
import re
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Max, Min, Q, Sum
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from analitica import comercial as com
from analitica import indicadores as ind
from analitica.models import FactVenta
from analitica.views import revisiones_calidad
from comercial.models import MetaComercial, Venta
from inventario.models import MovimientoInventario
from maestros.models import Categoria, Cliente, LoteImportacion, Producto

from .models import Perfil, RegistroActividad, RespuestaSUS

ENCABEZADO = PatternFill("solid", fgColor="0B1F3A")


def _num(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(round(v, 4))
    return v


def _hoja(libro, titulo, columnas, filas, anchos=None):
    hoja = libro.create_sheet(titulo)
    hoja.append(columnas)
    for celda in hoja[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = ENCABEZADO
        celda.alignment = Alignment(wrap_text=True, vertical="center")
    for fila in filas:
        hoja.append([_num(v) for v in fila])
    for i, _ in enumerate(columnas, start=1):
        hoja.column_dimensions[get_column_letter(i)].width = (anchos or {}).get(i, 18)
    hoja.freeze_panes = "A2"
    return hoja


def _referencia(empresa):
    """Fecha de corte: la última venta registrada (la historia cargada puede terminar antes de hoy)."""
    ultima = FactVenta.objects.filter(empresa=empresa).aggregate(m=Max("fecha_id"))["m"]
    return ultima or timezone.localdate()


def _mensaje_generico(texto):
    texto = re.sub(r"'[^']*'|«[^»]*»", "«…»", texto)
    texto = re.sub(r"\b[A-Z0-9][A-Z0-9\-_.]{2,}\b", "X", texto)
    return re.sub(r"\d+([.,]\d+)?", "N", texto)


def generar(empresas):
    libro = Workbook()
    portada = libro.active
    portada.title = "Léame"
    ahora = timezone.localtime()
    for linea in (
        ["Evidencia del piloto PAGE-PYME"],
        [f"Generado el {ahora:%d/%m/%Y %H:%M}"],
        ["Fecha de corte de cada empresa: su última venta registrada (ver hoja Empresas)."],
        ["Los indicadores se calculan igual que en los tableros de la plataforma."],
        ["No contiene nombres ni documentos de los clientes de las empresas."],
    ):
        portada.append(linea)
    portada["A1"].font = Font(bold=True, size=14)
    portada.column_dimensions["A"].width = 100

    f_emp, f_cargas, f_err, f_cal, f_ind, f_base, f_mes, f_top, f_lin, f_cli, f_cart, f_sus, f_uso = ([] for _ in range(13))
    for e in empresas:
        corte = _referencia(e)
        rango = FactVenta.objects.filter(empresa=e).aggregate(a=Min("fecha_id"), b=Max("fecha_id"))
        historicas = Venta.objects.filter(empresa=e, estado=Venta.EMITIDA).exclude(documento_externo="").count()
        en_plataforma = Venta.objects.filter(empresa=e, estado=Venta.EMITIDA, documento_externo="").count()
        movs = MovimientoInventario.objects.filter(empresa=e)
        meses = None
        if rango["a"] and rango["b"]:
            meses = round((rango["b"] - rango["a"]).days / 30.44, 1)
        f_emp.append([
            e.nombre, e.nit, e.ciudad, e.departamento, e.sector, e.numero_empleados, e.anios_operacion,
            e.herramienta_previa, e.fecha_vinculacion,
            Perfil.objects.filter(empresa=e, usuario__is_active=True).count(),
            Producto.objects.filter(empresa=e).count(), Categoria.objects.filter(empresa=e).count(),
            Cliente.objects.filter(empresa=e).count(), historicas, en_plataforma, rango["a"], rango["b"], meses,
            movs.filter(origen=MovimientoInventario.HISTORICO, tipo=MovimientoInventario.ENTRADA).count(),
            movs.filter(origen=MovimientoInventario.SALDO_INICIAL).count(), movs.count(), corte,
        ])

        for lote in LoteImportacion.objects.filter(empresa=e).order_by("fecha"):
            f_cargas.append([
                e.nombre, lote.pk, lote.get_tipo_display(), lote.archivo_nombre,
                timezone.localtime(lote.fecha).replace(tzinfo=None), lote.get_estado_display(), lote.total_filas,
                lote.filas_validas, lote.filas_con_error,
                round(lote.filas_validas / lote.total_filas * 100, 1) if lote.total_filas else None,
                lote.creados, lote.actualizados, " | ".join(lote.notas or []),
            ])
            conteo = Counter()
            for err in lote.errores or []:
                for m in err.get("mensajes", []):
                    conteo[_mensaje_generico(m)] += 1
            for mensaje, n in conteo.most_common():
                f_err.append([e.nombre, lote.pk, lote.get_tipo_display(), mensaje, n])

        revisiones, _ = revisiones_calidad(e)
        for r in revisiones:
            f_cal.append([e.nombre, r["nombre"], r["problemas"], r["total"], "Sí" if r["ok"] else "No",
                          "Sí" if r["informativa"] else "No", r["impacto"]])

        periodos = [("Últimos 30 días", corte - timedelta(days=29), corte),
                    ("Últimos 90 días", corte - timedelta(days=89), corte)]
        if rango["a"]:
            periodos.append(("Historia completa", rango["a"], corte))
        for nombre, inicio, fin in periodos:
            lista, _ = ind.calcular_indicadores(e, inicio, fin)
            for i in lista:
                f_ind.append([e.nombre, nombre, inicio, fin, i.nombre, _num(i.valor), i.unidad, _num(i.meta),
                              "≥" if i.direccion == "mayor" else "≤", _num(i.cumplimiento), i.estado_texto,
                              _num(i.anterior), i.calculo])
            b = ind.valores_base(e, inicio, fin)
            f_base.append([e.nombre, nombre, inicio, fin, b["ingresos"], b["costo"], b["contribucion"], b["n_ventas"],
                           b["ventas_credito"], b["inv_inicial"], b["inv_final"], b["inv_promedio"], b["saldo_cartera"],
                           b["productos_vigentes"], b["productos_quiebre"], b["clientes_compradores"],
                           b["clientes_recurrentes"]])

        if rango["a"]:
            ventas_mes = (
                FactVenta.objects.filter(empresa=e).values("fecha__anio", "fecha__mes")
                .annotate(v_ingreso=Sum("ingreso"), v_costo=Sum("costo"), facturas=Count("venta_id", distinct=True),
                          clientes=Count("cliente_id", distinct=True),
                          credito=Sum("ingreso", filter=Q(forma_pago=Venta.CREDITO)))
                .order_by("fecha__anio", "fecha__mes")
            )
            metas = {(m.anio, m.mes): m.valor for m in MetaComercial.objects.filter(empresa=e)}
            for v in ventas_mes:
                ingreso = v["v_ingreso"] or Decimal(0)
                meta = metas.get((v["fecha__anio"], v["fecha__mes"]))
                f_mes.append([
                    e.nombre, f"{v['fecha__anio']}-{v['fecha__mes']:02d}", ingreso, v["v_costo"],
                    round((ingreso - v["v_costo"]) / ingreso * 100, 1) if ingreso else None, v["facturas"],
                    v["clientes"], round(ingreso / v["facturas"]) if v["facturas"] else None,
                    round((v["credito"] or 0) / ingreso * 100, 1) if ingreso else None, meta,
                    round(ingreso / meta * 100, 1) if meta else None,
                ])
            for p in com.top_productos(e, rango["a"], corte, limite=15):
                f_top.append([e.nombre, p["codigo"], p["producto"], p["unidades"], p["ingreso"], p["participacion"],
                              p["margen"], p["stock"], p["minimo"], p["estado_stock"]])
            for l in com.ventas_por_linea(e, rango["a"], corte):
                f_lin.append([e.nombre] + [l.get(k) for k in ("linea", "ventas", "participacion")])

        grupos = com.clasificar_clientes(e, corte)
        compras = defaultdict(int)
        for n in (FactVenta.objects.filter(empresa=e).values("cliente_id")
                  .annotate(n=Count("venta_id", distinct=True)).values_list("n", flat=True)):
            compras["Una compra" if n == 1 else ("2 a 5 compras" if n <= 5 else "Más de 5 compras")] += 1
        f_cli.append([e.nombre, Cliente.objects.filter(empresa=e).count(), sum(compras.values()),
                      compras["Una compra"], compras["2 a 5 compras"], compras["Más de 5 compras"],
                      len(grupos["dejo"]), sum(c["total"] for c in grupos["dejo"]),
                      len(grupos["riesgo"]), sum(c["total"] for c in grupos["riesgo"]),
                      len(grupos["no_volvio"]), sum(c["total"] for c in grupos["no_volvio"])])
        for t in ind.cartera_por_antiguedad(e, corte):
            f_cart.append([e.nombre, corte, t["tramo"], t["saldo"]])

        for r in RespuestaSUS.objects.filter(empresa=e).select_related("usuario"):
            rol = Perfil.objects.filter(usuario=r.usuario, empresa=e).values_list("rol", flat=True).first()
            f_sus.append([e.nombre, f"U{r.usuario_id}", rol, timezone.localtime(r.fecha).replace(tzinfo=None), r.puntaje])

        uso = (RegistroActividad.objects.filter(empresa=e, usuario__is_staff=False)
               .values("modulo").annotate(n=Count("id"), usuarios=Count("usuario", distinct=True)).order_by("-n"))
        for u in uso:
            f_uso.append([e.nombre, u["modulo"], u["n"], u["usuarios"]])

    _hoja(libro, "Empresas", ["Empresa", "NIT", "Ciudad", "Departamento", "Sector", "Empleados", "Años de operación",
                              "Herramienta previa", "Vinculada", "Usuarios", "Productos", "Categorías", "Clientes",
                              "Facturas históricas", "Ventas registradas en la plataforma", "Primera venta",
                              "Última venta", "Meses de historia", "Compras históricas (líneas)",
                              "Saldos iniciales o ajustes de conciliación", "Movimientos de inventario", "Fecha de corte"], f_emp)
    _hoja(libro, "Cargas", ["Empresa", "Lote", "Tipo", "Archivo", "Fecha", "Estado", "Filas leídas", "Filas válidas",
                            "Filas con error", "% válidas", "Creados", "Actualizados", "Notas de la carga"], f_cargas,
          {13: 90})
    _hoja(libro, "Errores de carga", ["Empresa", "Lote", "Tipo", "Error (generalizado)", "Veces"], f_err, {4: 90})
    _hoja(libro, "Calidad", ["Empresa", "Revisión", "Casos con problema", "Total revisado", "Aprobada", "Informativa",
                             "Por qué importa"], f_cal, {2: 45, 7: 70})
    _hoja(libro, "Indicadores", ["Empresa", "Periodo", "Desde", "Hasta", "Indicador", "Valor", "Unidad", "Meta",
                                 "Sentido", "Cumplimiento (%)", "Estado", "Periodo anterior", "Cálculo"], f_ind, {13: 80})
    _hoja(libro, "Cifras base", ["Empresa", "Periodo", "Desde", "Hasta", "Ventas", "Costo de lo vendido", "Contribución",
                                 "Número de ventas", "Ventas a crédito", "Inventario inicial", "Inventario final",
                                 "Inventario promedio", "Saldo de cartera", "Productos vigentes", "Productos agotados",
                                 "Clientes compradores", "Clientes recurrentes"], f_base)
    _hoja(libro, "Ventas por mes", ["Empresa", "Mes", "Ventas", "Costo", "Margen (%)", "Facturas", "Clientes",
                                    "Ticket promedio", "% a crédito", "Meta", "Cumplimiento (%)"], f_mes)
    _hoja(libro, "Productos más vendidos", ["Empresa", "Código", "Producto", "Unidades", "Ventas", "Participación (%)",
                                            "Margen (%)", "Existencias", "Mínimo", "Estado"], f_top, {3: 40})
    _hoja(libro, "Ventas por línea", ["Empresa", "Línea", "Ventas", "Participación (%)"], f_lin, {2: 35})
    _hoja(libro, "Clientes", ["Empresa", "Clientes en el maestro", "Clientes con compras", "Con una compra",
                              "Con 2 a 5 compras", "Con más de 5 compras", "Dejaron de comprar", "Valor histórico (dejaron)",
                              "En riesgo", "Valor histórico (en riesgo)", "No volvieron", "Valor histórico (no volvieron)"],
          f_cli)
    _hoja(libro, "Cartera", ["Empresa", "Fecha de corte", "Tramo", "Saldo"], f_cart)
    _hoja(libro, "SUS", ["Empresa", "Usuario", "Rol", "Fecha", "Puntaje"], f_sus)
    _hoja(libro, "Uso", ["Empresa", "Módulo", "Acciones", "Usuarios"], f_uso)
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()
