"""Vinculación de empresas al piloto (solo para el equipo consultor)."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from comercial.models import Cotizacion, MetaComercial, Seguimiento, Venta
from inventario.models import MovimientoInventario
from maestros.models import Categoria, Cliente, LoteImportacion, Producto

from .actividad import registrar
from .forms import EmpresaPilotoForm, GerentePilotoForm, ReiniciarEmpresaForm
from .models import Empresa, Perfil


def _solo_consultor(request):
    if not request.user.is_staff:
        raise PermissionDenied


@login_required
def empresas_piloto(request):
    _solo_consultor(request)
    inicial = {"fecha_vinculacion": timezone.localdate()}
    form_empresa = EmpresaPilotoForm(request.POST or None, prefix="empresa", initial=inicial)
    form_gerente = GerentePilotoForm(request.POST or None, prefix="gerente")
    if request.method == "POST" and form_empresa.is_valid() and form_gerente.is_valid():
        with transaction.atomic():
            empresa = form_empresa.save()
            d = form_gerente.cleaned_data
            usuario = get_user_model().objects.create_user(
                username=d["username"], password=d["password1"], first_name=d["first_name"],
                last_name=d["last_name"], email=d["email"],
            )
            Perfil.objects.create(usuario=usuario, empresa=empresa, rol=Perfil.GERENTE)
        request.session["empresa_id"] = empresa.pk
        request.empresa = empresa
        registrar(request, "configuracion", "Empresa vinculada al piloto", f"{empresa.nombre} · gerente {usuario.username}")
        messages.success(
            request,
            f"«{empresa.nombre}» quedó vinculada y ahora trabajas sobre ella. El gerente ingresa con el usuario "
            f"{usuario.username}. Siguiente paso: cargar sus datos.",
        )
        return redirect("maestros:importar")

    empresas = (
        Empresa.objects.filter(es_demo=False)
        .annotate(
            n_usuarios=Count("perfiles", distinct=True),
            n_productos=Count("productos", distinct=True),
            n_clientes=Count("clientes", distinct=True),
            n_ventas=Count("ventas", filter=Q(ventas__estado=Venta.EMITIDA), distinct=True),
            n_lotes=Count("lotes_importacion", filter=Q(lotes_importacion__estado="aplicado"), distinct=True),
        )
        .order_by("fecha_vinculacion", "nombre")
    )
    return render(request, "core/empresas_piloto.html", {
        "form_empresa": form_empresa, "form_gerente": form_gerente, "empresas": empresas,
    })


@login_required
def empresa_reiniciar(request, pk):
    _solo_consultor(request)
    empresa = get_object_or_404(Empresa, pk=pk, es_demo=False)
    form = ReiniciarEmpresaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data["confirmacion"].strip() != empresa.nombre:
            form.add_error("confirmacion", "El nombre no coincide.")
        else:
            maestros = form.cleaned_data["incluir_maestros"]
            borrar_datos(empresa, maestros)
            registrar(request, "configuracion", "Datos de la empresa reiniciados",
                      f"{empresa.nombre}{' (incluye maestros)' if maestros else ''}")
            messages.success(request, f"Se borraron los datos cargados de «{empresa.nombre}». Puedes volver a cargarlos.")
            return redirect("empresas_piloto")
    resumen = {
        "ventas": Venta.objects.filter(empresa=empresa).count(),
        "movimientos": MovimientoInventario.objects.filter(empresa=empresa).count(),
        "cotizaciones": Cotizacion.objects.filter(empresa=empresa).count(),
        "lotes": LoteImportacion.objects.filter(empresa=empresa).count(),
        "productos": Producto.objects.filter(empresa=empresa).count(),
        "clientes": Cliente.objects.filter(empresa=empresa).count(),
    }
    return render(request, "core/empresa_reiniciar.html", {"empresa": empresa, "form": form, "resumen": resumen})


@transaction.atomic
def borrar_datos(empresa, incluir_maestros=False):
    """Borra las operaciones y cargas de una empresa del piloto (no toca usuarios ni la bitácora de uso)."""
    Venta.objects.filter(empresa=empresa).delete()          # incluye líneas, abonos y movimientos asociados
    Cotizacion.objects.filter(empresa=empresa).delete()
    MovimientoInventario.objects.filter(empresa=empresa).delete()
    Seguimiento.objects.filter(empresa=empresa).delete()
    MetaComercial.objects.filter(empresa=empresa).delete()
    LoteImportacion.objects.filter(empresa=empresa).delete()
    if incluir_maestros:
        Producto.objects.filter(empresa=empresa).delete()
        Categoria.objects.filter(empresa=empresa).delete()
        Cliente.objects.filter(empresa=empresa).delete()
    else:
        Producto.objects.filter(empresa=empresa).update(stock_actual=0)


@login_required
def evidencia_piloto(request):
    """Descarga en Excel la evidencia del piloto de todas las empresas vinculadas."""
    _solo_consultor(request)
    from .evidencia_piloto import generar
    empresas = Empresa.objects.filter(es_demo=False).order_by("nombre")
    respuesta = HttpResponse(
        generar(empresas), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    respuesta["Content-Disposition"] = f'attachment; filename="evidencia_piloto_{timezone.localdate():%Y%m%d}.xlsx"'
    registrar(request, "evidencia", "Evidencia del piloto descargada", f"{empresas.count()} empresas")
    return respuesta
