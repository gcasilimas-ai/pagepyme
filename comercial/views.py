from datetime import date

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from analitica.indicadores import TRAMOS_CARTERA, cartera_por_antiguedad, ventas_credito_con_saldo
from core.actividad import registrar
from core.permisos import requiere
from core.utilidades import paginar, parametros_sin_pagina
from inventario.servicios import StockInsuficiente
from maestros.models import Producto

from . import servicios
from .forms import (AbonoForm, ConvertirForm, CotizacionForm, EstadoCotizacionForm, ItemFormSet,
                    VentaForm, items_desde_formset)
from .models import Cotizacion, Venta


def _catalogo_json(empresa):
    productos = Producto.objects.filter(empresa=empresa, activo=True).values(
        "id", "precio_venta", "stock_actual", "unidad"
    )
    return {
        str(p["id"]): {"precio": str(p["precio_venta"]), "stock": p["stock_actual"], "unidad": p["unidad"]}
        for p in productos
    }


def _fecha(valor):
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- ventas

@requiere("comercial")
def ventas(request):
    qs = Venta.objects.filter(empresa=request.empresa).select_related("cliente")
    q = request.GET.get("q", "").strip()
    forma = request.GET.get("forma", "")
    desde, hasta = _fecha(request.GET.get("desde")), _fecha(request.GET.get("hasta"))
    if q:
        numero = q.upper().replace("VTA-", "").strip()
        filtro = Q(cliente__nombre__icontains=q)
        if numero.isdigit():
            filtro |= Q(numero=int(numero))
        qs = qs.filter(filtro)
    if forma in {Venta.CONTADO, Venta.CREDITO}:
        qs = qs.filter(forma_pago=forma)
    if desde:
        qs = qs.filter(fecha__gte=desde)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)
    total = qs.filter(estado=Venta.EMITIDA).aggregate(t=Sum("total"))["t"] or 0
    return render(request, "comercial/ventas.html", {
        "pagina": paginar(request, qs), "parametros": parametros_sin_pagina(request),
        "q": q, "forma": forma, "desde": request.GET.get("desde", ""), "hasta": request.GET.get("hasta", ""),
        "total": total,
    })


@requiere("comercial")
def venta_nueva(request):
    empresa = request.empresa
    form = VentaForm(request.POST or None, empresa=empresa)
    formset = ItemFormSet(request.POST or None, prefix="items", form_kwargs={"empresa": empresa})
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        items = items_desde_formset(formset)
        try:
            venta = servicios.crear_venta(
                empresa, form.cleaned_data["cliente"], items,
                forma_pago=form.cleaned_data["forma_pago"], fecha=form.cleaned_data["fecha"],
                usuario=request.user, observaciones=form.cleaned_data["observaciones"],
                medio_pago=form.cleaned_data["medio_pago"] or "efectivo",
            )
        except (StockInsuficiente, ValidationError) as exc:
            form.add_error(None, exc.messages[0] if hasattr(exc, "messages") else str(exc))
        else:
            registrar(request, "comercial", "Venta emitida", f"{venta.codigo} por {venta.total:.0f}")
            messages.success(request, f"Venta {venta.codigo} registrada. El inventario ya se actualizó.")
            return redirect("comercial:venta", pk=venta.pk)
    return render(request, "comercial/documento_form.html", {
        "form": form, "formset": formset, "titulo": "Nueva venta", "catalogo": _catalogo_json(empresa),
        "volver": "comercial:ventas", "boton": "Registrar venta", "es_venta": True,
    })


@requiere("comercial")
def venta_detalle(request, pk):
    venta = get_object_or_404(Venta.objects.select_related("cliente", "creado_por", "cotizacion"), pk=pk, empresa=request.empresa)
    return render(request, "comercial/venta_detalle.html", {
        "venta": venta,
        "items": venta.items.select_related("producto"),
        "pagos": venta.pagos.select_related("registrado_por"),
        "abono_form": AbonoForm(initial={"valor": venta.saldo}),
        "hoy": timezone.localdate(),
    })


@requiere("comercial")
@require_POST
def venta_abono(request, pk):
    venta = get_object_or_404(Venta, pk=pk, empresa=request.empresa)
    form = AbonoForm(request.POST)
    if form.is_valid():
        try:
            pago = servicios.registrar_abono(venta, usuario=request.user, **form.cleaned_data)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            registrar(request, "comercial", "Abono registrado", f"{venta.codigo}: {pago.valor:.0f}")
            messages.success(request, "Abono registrado.")
    else:
        messages.error(request, "Revisa los datos del abono: " + "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("comercial:venta", pk=venta.pk)


@requiere("comercial")
@require_POST
def venta_anular(request, pk):
    venta = get_object_or_404(Venta, pk=pk, empresa=request.empresa)
    try:
        servicios.anular_venta(venta, usuario=request.user, motivo=request.POST.get("motivo", ""))
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        registrar(request, "comercial", "Venta anulada", venta.codigo)
        messages.success(request, f"{venta.codigo} anulada; las unidades regresaron al inventario.")
    return redirect("comercial:venta", pk=venta.pk)


@requiere("comercial")
def cartera(request):
    hoy = timezone.localdate()
    filas = sorted(ventas_credito_con_saldo(request.empresa, hoy), key=lambda v: v.dias_vencida_corte, reverse=True)
    solo_vencidas = request.GET.get("vencidas") == "1"
    if solo_vencidas:
        filas = [v for v in filas if v.dias_vencida_corte > 0]
    tramos = cartera_por_antiguedad(request.empresa, hoy)
    return render(request, "comercial/cartera.html", {
        "filas": filas, "tramos": tramos, "total": sum(t["saldo"] for t in tramos),
        "vencido": sum(t["saldo"] for t in tramos[1:]), "solo_vencidas": solo_vencidas, "hoy": hoy,
        "n_tramos": len(TRAMOS_CARTERA),
    })


# --------------------------------------------------------------------------- cotizaciones

@requiere("comercial")
def cotizaciones(request):
    qs = Cotizacion.objects.filter(empresa=request.empresa).select_related("cliente")
    estado = request.GET.get("estado", "")
    q = request.GET.get("q", "").strip()
    if estado:
        qs = qs.filter(estado=estado)
    if q:
        qs = qs.filter(cliente__nombre__icontains=q)
    return render(request, "comercial/cotizaciones.html", {
        "pagina": paginar(request, qs), "parametros": parametros_sin_pagina(request),
        "estados": Cotizacion.ESTADOS, "estado": estado, "q": q,
    })


@requiere("comercial")
def cotizacion_nueva(request):
    empresa = request.empresa
    form = CotizacionForm(request.POST or None, empresa=empresa)
    formset = ItemFormSet(request.POST or None, prefix="items", form_kwargs={"empresa": empresa})
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        try:
            cotizacion = servicios.crear_cotizacion(
                empresa, form.cleaned_data["cliente"], items_desde_formset(formset),
                fecha=form.cleaned_data["fecha"], vigencia_dias=form.cleaned_data["vigencia_dias"],
                observaciones=form.cleaned_data["observaciones"], usuario=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            registrar(request, "comercial", "Cotización creada", cotizacion.codigo)
            messages.success(request, f"Cotización {cotizacion.codigo} creada.")
            return redirect("comercial:cotizacion", pk=cotizacion.pk)
    return render(request, "comercial/documento_form.html", {
        "form": form, "formset": formset, "titulo": "Nueva cotización", "catalogo": _catalogo_json(empresa),
        "volver": "comercial:cotizaciones", "boton": "Guardar cotización", "es_venta": False,
    })


@requiere("comercial")
def cotizacion_detalle(request, pk):
    cotizacion = get_object_or_404(Cotizacion.objects.select_related("cliente", "creado_por"), pk=pk, empresa=request.empresa)
    items = cotizacion.items.select_related("producto")
    faltantes = [i for i in items if i.producto.stock_actual < i.cantidad]
    return render(request, "comercial/cotizacion_detalle.html", {
        "cotizacion": cotizacion, "items": items, "faltantes": faltantes,
        "estado_form": EstadoCotizacionForm(initial={"estado": cotizacion.estado}),
        "convertir_form": ConvertirForm(),
        "venta": getattr(cotizacion, "venta", None),
    })


@requiere("comercial")
@require_POST
def cotizacion_estado(request, pk):
    cotizacion = get_object_or_404(Cotizacion, pk=pk, empresa=request.empresa)
    form = EstadoCotizacionForm(request.POST)
    if form.is_valid() and cotizacion.estado != Cotizacion.CONVERTIDA:
        cotizacion.estado = form.cleaned_data["estado"]
        cotizacion.save(update_fields=["estado"])
        registrar(request, "comercial", "Cotización actualizada", f"{cotizacion.codigo}: {cotizacion.get_estado_display()}")
        messages.success(request, f"Estado actualizado a «{cotizacion.get_estado_display()}».")
    return redirect("comercial:cotizacion", pk=pk)


@requiere("comercial")
@require_POST
def cotizacion_convertir(request, pk):
    cotizacion = get_object_or_404(Cotizacion, pk=pk, empresa=request.empresa)
    form = ConvertirForm(request.POST)
    if form.is_valid():
        try:
            venta = servicios.convertir_cotizacion(cotizacion, form.cleaned_data["forma_pago"], usuario=request.user)
        except (StockInsuficiente, ValidationError) as exc:
            messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
        else:
            registrar(request, "comercial", "Cotización convertida", f"{cotizacion.codigo} → {venta.codigo}")
            messages.success(request, f"Se emitió la venta {venta.codigo} a partir de {cotizacion.codigo}.")
            return redirect("comercial:venta", pk=venta.pk)
    return redirect("comercial:cotizacion", pk=pk)


@requiere("comercial")
def cotizacion_imprimir(request, pk):
    cotizacion = get_object_or_404(Cotizacion.objects.select_related("cliente", "empresa"), pk=pk, empresa=request.empresa)
    return render(request, "comercial/cotizacion_imprimir.html", {
        "cotizacion": cotizacion, "items": cotizacion.items.select_related("producto"),
    })
