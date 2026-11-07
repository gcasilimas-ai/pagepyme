from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from comercial.models import Venta
from core.actividad import registrar
from core.permisos import puede, requiere
from core.utilidades import paginar, parametros_sin_pagina

from .forms import CategoriaForm, ClienteForm, ImportarForm, ProductoForm
from .importacion import COLUMNAS, ArchivoInvalido, analizar_archivo, aplicar_lote, generar_plantilla
from .models import Categoria, Cliente, LoteImportacion, Producto


# --------------------------------------------------------------------------- productos

@requiere("productos_ver")
def productos(request):
    qs = Producto.objects.filter(empresa=request.empresa).select_related("categoria")
    q = request.GET.get("q", "").strip()
    categoria = request.GET.get("categoria", "")
    estado = request.GET.get("estado", "")
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(codigo__icontains=q))
    if categoria:
        qs = qs.filter(categoria_id=categoria)
    if estado == "agotados":
        qs = qs.filter(activo=True, stock_actual__lte=0)
    elif estado == "inactivos":
        qs = qs.filter(activo=False)
    else:
        qs = qs.filter(activo=True)
    return render(request, "maestros/productos.html", {
        "pagina": paginar(request, qs),
        "parametros": parametros_sin_pagina(request),
        "categorias": Categoria.objects.filter(empresa=request.empresa),
        "q": q, "categoria_sel": categoria, "estado": estado,
        "puede_editar": puede(request, "productos_editar"),
    })


@requiere("productos_editar")
def producto_form(request, pk=None):
    instancia = get_object_or_404(Producto, pk=pk, empresa=request.empresa) if pk else None
    form = ProductoForm(request.POST or None, instance=instancia, empresa=request.empresa)
    if request.method == "POST" and form.is_valid():
        producto = form.save()
        registrar(request, "maestros", "Producto editado" if pk else "Producto creado", producto.codigo)
        messages.success(request, f"Producto {producto.codigo} guardado.")
        return redirect("maestros:producto", pk=producto.pk)
    return render(request, "core/formulario.html", {
        "form": form,
        "titulo": "Editar producto" if pk else "Nuevo producto",
        "volver": "maestros:productos",
        "ayuda": "El stock no se edita aquí: cambia solo con entradas, ventas y ajustes de inventario.",
    })


@requiere("productos_ver")
def producto_detalle(request, pk):
    producto = get_object_or_404(Producto.objects.select_related("categoria"), pk=pk, empresa=request.empresa)
    movimientos = producto.movimientos.select_related("usuario", "venta")[:15]
    ventas = producto.items_venta.filter(venta__estado=Venta.EMITIDA).aggregate(
        unidades=Sum("cantidad"), n=Count("venta", distinct=True)
    )
    return render(request, "maestros/producto_detalle.html", {
        "producto": producto, "movimientos": movimientos, "ventas": ventas,
        "puede_editar": puede(request, "productos_editar"),
        "puede_inventario": puede(request, "inventario"),
    })


# --------------------------------------------------------------------------- categorías

@requiere("productos_ver")
def categorias(request):
    qs = Categoria.objects.filter(empresa=request.empresa).annotate(n_productos=Count("productos"))
    return render(request, "maestros/categorias.html", {
        "categorias": qs, "puede_editar": puede(request, "productos_editar"),
    })


@requiere("productos_editar")
def categoria_form(request, pk=None):
    instancia = get_object_or_404(Categoria, pk=pk, empresa=request.empresa) if pk else None
    form = CategoriaForm(request.POST or None, instance=instancia, empresa=request.empresa)
    if request.method == "POST" and form.is_valid():
        categoria = form.save()
        registrar(request, "maestros", "Categoría guardada", categoria.nombre)
        messages.success(request, f"Categoría «{categoria.nombre}» guardada.")
        return redirect("maestros:categorias")
    return render(request, "core/formulario.html", {
        "form": form,
        "titulo": "Editar categoría" if pk else "Nueva categoría",
        "volver": "maestros:categorias",
        "ayuda": "La meta de margen de la línea permite comparar cada categoría con un objetivo propio en el tablero.",
    })


# --------------------------------------------------------------------------- clientes

@requiere("clientes")
def clientes(request):
    qs = Cliente.objects.filter(empresa=request.empresa)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(documento__icontains=q) | Q(ciudad__icontains=q))
    qs = qs.annotate(
        n_ventas=Count("ventas", filter=Q(ventas__estado=Venta.EMITIDA)),
        total_comprado=Sum("ventas__total", filter=Q(ventas__estado=Venta.EMITIDA)),
    ).order_by("nombre")
    return render(request, "maestros/clientes.html", {
        "pagina": paginar(request, qs), "parametros": parametros_sin_pagina(request), "q": q,
    })


@requiere("clientes")
def cliente_form(request, pk=None):
    instancia = get_object_or_404(Cliente, pk=pk, empresa=request.empresa) if pk else None
    form = ClienteForm(request.POST or None, instance=instancia, empresa=request.empresa)
    if request.method == "POST" and form.is_valid():
        cliente = form.save()
        registrar(request, "maestros", "Cliente editado" if pk else "Cliente creado", cliente.nombre)
        messages.success(request, f"Cliente «{cliente.nombre}» guardado.")
        return redirect("maestros:cliente", pk=cliente.pk)
    return render(request, "core/formulario.html", {
        "form": form,
        "titulo": "Editar cliente" if pk else "Nuevo cliente",
        "volver": "maestros:clientes",
        "ayuda": "El plazo de crédito define la fecha de vencimiento de las ventas a crédito (30 días si queda en 0).",
    })


@requiere("clientes")
def cliente_detalle(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk, empresa=request.empresa)
    ventas = cliente.ventas.order_by("-fecha", "-numero")[:20]
    con_saldo = [v for v in cliente.ventas.filter(estado=Venta.EMITIDA, forma_pago=Venta.CREDITO) if v.saldo > 0]
    return render(request, "maestros/cliente_detalle.html", {
        "cliente": cliente, "ventas": ventas,
        "saldo_total": sum((v.saldo for v in con_saldo), 0), "ventas_con_saldo": con_saldo,
    })


# --------------------------------------------------------------------------- importación (ETL)

@requiere("importar")
def importar(request):
    form = ImportarForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            lote = analizar_archivo(request.empresa, form.cleaned_data["tipo"], form.cleaned_data["archivo"], request.user)
        except ArchivoInvalido as exc:
            form.add_error("archivo", str(exc))
        else:
            registrar(request, "importacion", "Archivo analizado", f"{lote.get_tipo_display()}: {lote.archivo_nombre}")
            return redirect("maestros:lote", pk=lote.pk)
    lotes = LoteImportacion.objects.filter(empresa=request.empresa).select_related("usuario")[:10]
    return render(request, "maestros/importar.html", {"form": form, "lotes": lotes, "columnas": COLUMNAS})


@requiere("importar")
def lote(request, pk):
    lote = get_object_or_404(LoteImportacion, pk=pk, empresa=request.empresa)
    if request.method == "POST" and lote.estado == "pendiente":
        if request.POST.get("accion") == "aplicar":
            if not lote.filas_validas:
                messages.error(request, "No hay filas válidas para cargar.")
            else:
                aplicar_lote(lote, request.user)
                registrar(request, "importacion", "Lote aplicado",
                          f"{lote.get_tipo_display()}: {lote.creados} creados, {lote.actualizados} actualizados")
                messages.success(request, f"Carga aplicada: {lote.creados} creados y {lote.actualizados} actualizados.")
        else:
            lote.estado = "descartado"
            lote.save(update_fields=["estado"])
            registrar(request, "importacion", "Lote descartado", lote.archivo_nombre)
            messages.info(request, "La carga se descartó; no se modificó ningún dato.")
        return redirect("maestros:lote", pk=lote.pk)
    advertencias = sum(len(f.get("advertencias", [])) for f in lote.datos)
    return render(request, "maestros/lote.html", {"lote": lote, "advertencias": advertencias})


@requiere("importar")
def plantilla(request, tipo):
    if tipo not in COLUMNAS:
        raise Http404
    respuesta = HttpResponse(
        generar_plantilla(tipo),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    respuesta["Content-Disposition"] = f'attachment; filename="plantilla_{tipo}.xlsx"'
    return respuesta
