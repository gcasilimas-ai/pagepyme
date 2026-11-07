from datetime import date

from django.contrib import messages
from django.db.models import F, Q
from django.shortcuts import redirect, render

from analitica.indicadores import alertas_stock
from core.actividad import registrar
from core.permisos import requiere
from core.utilidades import paginar, parametros_sin_pagina

from .forms import MovimientoForm
from .models import MovimientoInventario
from .servicios import StockInsuficiente, registrar_movimiento


@requiere("inventario")
def movimientos(request):
    qs = MovimientoInventario.objects.filter(empresa=request.empresa).select_related("producto", "usuario", "venta")
    q = request.GET.get("q", "").strip()
    tipo = request.GET.get("tipo", "")
    if q:
        qs = qs.filter(Q(producto__nombre__icontains=q) | Q(producto__codigo__icontains=q))
    if tipo:
        qs = qs.filter(tipo=tipo)
    for campo, lookup in (("desde", "fecha__gte"), ("hasta", "fecha__lte")):
        try:
            if request.GET.get(campo):
                qs = qs.filter(**{lookup: date.fromisoformat(request.GET[campo])})
        except ValueError:
            pass
    return render(request, "inventario/movimientos.html", {
        "pagina": paginar(request, qs), "parametros": parametros_sin_pagina(request),
        "tipos": MovimientoInventario.TIPOS, "tipo": tipo, "q": q,
        "desde": request.GET.get("desde", ""), "hasta": request.GET.get("hasta", ""),
    })


@requiere("inventario")
def movimiento_nuevo(request):
    inicial = {}
    if request.GET.get("producto"):
        inicial["producto"] = request.GET["producto"]
        inicial["tipo"] = request.GET.get("tipo", MovimientoInventario.ENTRADA)
    form = MovimientoForm(request.POST or None, empresa=request.empresa, initial=inicial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            mov = registrar_movimiento(
                request.empresa, d["producto"], d["tipo"], d["cantidad"],
                costo_unitario=d.get("costo_unitario"), fecha=d["fecha"],
                usuario=request.user, observacion=d.get("observacion", ""),
            )
        except StockInsuficiente as exc:
            form.add_error("cantidad", str(exc))
        else:
            registrar(request, "inventario", mov.get_tipo_display(), f"{mov.producto.codigo} × {mov.cantidad}")
            messages.success(request, f"{mov.get_tipo_display()} registrada. Stock de {mov.producto.nombre}: {mov.producto.stock_actual}.")
            return redirect("inventario:movimientos")
    return render(request, "core/formulario.html", {
        "form": form, "titulo": "Registrar movimiento de inventario", "volver": "inventario:movimientos",
        "ayuda": "Las salidas por venta se registran solas al emitir una venta. Aquí se registran compras y ajustes.",
    })


@requiere("inventario")
def alertas(request):
    productos = alertas_stock(request.empresa)
    return render(request, "inventario/alertas.html", {
        "agotados": [p for p in productos if p.stock_actual <= 0],
        "bajos": [p for p in productos if p.stock_actual > 0],
    })
