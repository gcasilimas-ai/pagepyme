from collections import Counter, OrderedDict, defaultdict
from decimal import Decimal
from datetime import timedelta
from statistics import mean

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from analitica import indicadores as ind
from analitica.models import FactVenta
from comercial.models import MetaComercial, Venta
from maestros.models import Cliente, Producto

from .actividad import registrar
from .forms import MESES, EmpresaForm, MetasComercialesForm, MetasForm, NuevoUsuarioForm, SUSForm
from .models import Empresa, Perfil, RegistroActividad, RespuestaSUS
from .permisos import puede, requiere


class Ingreso(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True


@login_required
def inicio(request):
    if request.empresa is None:
        return redirect("sin_empresa")
    if request.rol == Perfil.COMERCIAL and not request.user.is_staff:
        return redirect("analitica:comercial")
    if puede(request, "tablero"):
        return redirect("analitica:tablero")
    return redirect("inventario:alertas")


@login_required
def sin_empresa(request):
    if request.empresa is not None:
        return redirect("inicio")
    return render(request, "core/sin_empresa.html")


@login_required
@require_POST
def cambiar_empresa(request):
    if not request.user.is_staff:
        raise PermissionDenied
    empresa = get_object_or_404(Empresa, pk=request.POST.get("empresa"))
    request.session["empresa_id"] = empresa.pk
    messages.info(request, f"Ahora trabajas sobre «{empresa.nombre}».")
    return redirect(request.POST.get("siguiente") or "inicio")


@requiere("configuracion")
def metas(request):
    empresa = request.empresa
    hoy = timezone.localdate()
    try:
        anio = int(request.POST.get("anio") or request.GET.get("anio") or hoy.year)
    except ValueError:
        anio = hoy.year
    seccion = request.POST.get("seccion") if request.method == "POST" else None

    existentes = {m.mes: m.valor for m in MetaComercial.objects.filter(empresa=empresa, anio=anio)}
    form_comercial = MetasComercialesForm(
        request.POST if seccion == "comercial" else None,
        initial={f"mes_{mes}": valor for mes, valor in existentes.items()},
    )
    form_indicadores = MetasForm(request.POST if seccion == "indicadores" else None, instance=empresa)

    if seccion == "comercial" and form_comercial.is_valid():
        for mes, valor in form_comercial.valores().items():
            if valor:
                MetaComercial.objects.update_or_create(empresa=empresa, anio=anio, mes=mes, defaults={"valor": valor})
            else:
                MetaComercial.objects.filter(empresa=empresa, anio=anio, mes=mes).delete()
        registrar(request, "configuracion", "Metas comerciales actualizadas", str(anio))
        messages.success(request, f"Metas de ventas de {anio} guardadas. El tablero comercial ya las usa.")
        return redirect(f"{reverse('metas')}?anio={anio}")
    if seccion == "indicadores" and form_indicadores.is_valid():
        form_indicadores.save()
        registrar(request, "configuracion", "Metas de indicadores actualizadas")
        messages.success(request, "Metas de los indicadores actualizadas.")
        return redirect(f"{reverse('metas')}?anio={anio}")

    ventas = defaultdict(Decimal)
    for fila in FactVenta.objects.filter(empresa=empresa, fecha__anio__in=[anio - 1, anio]) \
            .values("fecha__anio", "fecha__mes").annotate(t=Sum("ingreso")):
        ventas[(fila["fecha__anio"], fila["fecha__mes"])] = fila["t"] or Decimal("0")
    meses = []
    for i, nombre in enumerate(MESES, start=1):
        meses.append({
            "campo": form_comercial[f"mes_{i}"],
            "anterior": ventas.get((anio - 1, i)),
            "real": ventas.get((anio, i)),
            "meta": existentes.get(i),
        })
    return render(request, "core/metas.html", {
        "anio": anio,
        "anios": [hoy.year - 1, hoy.year, hoy.year + 1],
        "form_comercial": form_comercial,
        "form_indicadores": form_indicadores,
        "meses": meses,
        "total_anual": sum(existentes.values(), Decimal("0")),
        "meses_con_meta": len(existentes),
    })


@requiere("configuracion")
def empresa_datos(request):
    form = EmpresaForm(request.POST or None, instance=request.empresa)
    if request.method == "POST" and form.is_valid():
        form.save()
        registrar(request, "configuracion", "Datos de la empresa actualizados")
        messages.success(request, "Datos de la empresa guardados.")
        return redirect("empresa")
    return render(request, "core/formulario.html", {
        "form": form, "titulo": "Datos de la empresa", "volver": "analitica:tablero",
        "ayuda": "Esta información caracteriza a la empresa dentro del piloto.",
    })


@requiere("configuracion")
def equipo(request):
    form = NuevoUsuarioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        usuario = get_user_model().objects.create_user(
            username=d["username"], password=d["password1"], first_name=d["first_name"],
            last_name=d["last_name"], email=d["email"],
        )
        Perfil.objects.create(usuario=usuario, empresa=request.empresa, rol=d["rol"])
        registrar(request, "configuracion", "Usuario creado", f"{usuario.username} ({d['rol']})")
        messages.success(request, f"Usuario {usuario.username} creado. Ya puede ingresar.")
        return redirect("equipo")
    perfiles = Perfil.objects.filter(empresa=request.empresa).select_related("usuario").order_by("usuario__username")
    return render(request, "core/equipo.html", {"form": form, "perfiles": perfiles})


@requiere("configuracion")
@require_POST
def equipo_estado(request, pk):
    perfil = get_object_or_404(Perfil, pk=pk, empresa=request.empresa)
    if perfil.usuario == request.user:
        messages.error(request, "No puedes desactivar tu propio usuario.")
    else:
        perfil.usuario.is_active = not perfil.usuario.is_active
        perfil.usuario.save(update_fields=["is_active"])
        estado = "activado" if perfil.usuario.is_active else "desactivado"
        registrar(request, "configuracion", f"Usuario {estado}", perfil.usuario.username)
        messages.success(request, f"Usuario {perfil.usuario.username} {estado}.")
    return redirect("equipo")


def _semanas(n, hasta=None):
    hasta = hasta or timezone.localdate()
    lunes = hasta - timedelta(days=hasta.weekday())
    return [lunes - timedelta(weeks=i) for i in range(n - 1, -1, -1)]


def resumen_uso(empresa, semanas=8):
    inicios = _semanas(semanas)
    registros = RegistroActividad.objects.filter(empresa=empresa, fecha__date__gte=inicios[0])
    por_semana = OrderedDict((s, {"acciones": 0, "usuarios": set(), "dias": set()}) for s in inicios)
    modulos = Counter()
    for r in registros.only("fecha", "usuario_id", "modulo"):
        dia = timezone.localtime(r.fecha).date()
        semana = dia - timedelta(days=dia.weekday())
        if semana in por_semana:
            por_semana[semana]["acciones"] += 1
            por_semana[semana]["usuarios"].add(r.usuario_id)
            por_semana[semana]["dias"].add(dia)
        modulos[r.modulo] += 1
    filas = [
        {"semana": s, "acciones": d["acciones"], "usuarios": len(d["usuarios"]), "dias": len(d["dias"])}
        for s, d in por_semana.items()
    ]
    return filas, modulos.most_common()


@requiere("evidencia")
def uso(request):
    filas, modulos = resumen_uso(request.empresa)
    usuarios = Perfil.objects.filter(empresa=request.empresa).select_related("usuario")
    ultimas = {
        r["usuario_id"]: r["ultima"]
        for r in RegistroActividad.objects.filter(empresa=request.empresa).values("usuario_id").annotate(
            ultima=Max("fecha"), n=Count("id"))
    }
    return render(request, "core/uso.html", {
        "filas": filas, "modulos": modulos,
        "usuarios": [(p, ultimas.get(p.usuario_id)) for p in usuarios],
        "recientes": RegistroActividad.objects.filter(empresa=request.empresa).select_related("usuario")[:25],
        "max_acciones": max([f["acciones"] for f in filas] + [1]),
    })


def interpretar_sus(puntaje):
    """Escala adjetiva de Bangor, Kortum y Miller (2009)."""
    if puntaje is None:
        return ""
    if puntaje >= 85:
        return "Excelente"
    if puntaje >= 73:
        return "Buena"
    if puntaje >= 52:
        return "Aceptable"
    if puntaje >= 39:
        return "Pobre"
    return "Inaceptable"


@requiere("sus")
def encuesta(request):
    previa = RespuestaSUS.objects.filter(empresa=request.empresa, usuario=request.user).first()
    form = SUSForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        respuesta = form.save(commit=False)
        respuesta.empresa = request.empresa
        respuesta.usuario = request.user
        respuesta.save()
        registrar(request, "evidencia", "Encuesta SUS respondida", f"{respuesta.puntaje:.1f}")
        messages.success(request, "Gracias. Tu respuesta quedó registrada.")
        return redirect("inicio")
    return render(request, "core/encuesta.html", {"form": form, "previa": previa})


@requiere("evidencia")
def sus_resultados(request):
    respuestas = list(RespuestaSUS.objects.filter(empresa=request.empresa).select_related("usuario"))
    promedio = mean(r.puntaje for r in respuestas) if respuestas else None
    por_item = [
        {"n": i, "pregunta": p, "promedio": mean(r.respuestas[i - 1] for r in respuestas) if respuestas else None,
         "positiva": i % 2 == 1}
        for i, p in enumerate(RespuestaSUS.PREGUNTAS, start=1)
    ]
    return render(request, "core/sus_resultados.html", {
        "respuestas": respuestas, "promedio": promedio, "interpretacion": interpretar_sus(promedio),
        "por_item": por_item,
    })


@login_required
def piloto(request):
    """Resumen consolidado del piloto (solo para el equipo consultor)."""
    if not request.user.is_staff:
        raise PermissionDenied
    hoy = timezone.localdate()
    inicio = hoy - timedelta(days=29)
    empresas = Empresa.objects.filter(es_demo=False).order_by("nombre")
    semanas = _semanas(8)
    filas = []
    for e in empresas:
        uso_semanal, _ = resumen_uso(e)
        respuestas = list(RespuestaSUS.objects.filter(empresa=e))
        lista, _ = ind.calcular_indicadores(e, inicio, hoy)
        filas.append({
            "empresa": e,
            "usuarios": Perfil.objects.filter(empresa=e, usuario__is_active=True).count(),
            "productos": Producto.objects.filter(empresa=e).count(),
            "clientes": Cliente.objects.filter(empresa=e).count(),
            "ventas": Venta.objects.filter(empresa=e, estado=Venta.EMITIDA).count(),
            "acciones": RegistroActividad.objects.filter(empresa=e).count(),
            "semanas_activas": sum(1 for s in uso_semanal if s["acciones"] > 0),
            "uso_semanal": uso_semanal,
            "sus": mean(r.puntaje for r in respuestas) if respuestas else None,
            "n_sus": len(respuestas),
            "indicadores": lista,
        })
    adopcion = []
    for i, s in enumerate(semanas):
        por_empresa = [f["uso_semanal"][i]["acciones"] for f in filas]
        activas = sum(1 for n in por_empresa if n > 0)
        adopcion.append({"semana": s, "activas": activas, "por_empresa": por_empresa,
                         "pct": round(activas / len(filas) * 100) if filas else None})
    todas_sus = [r.puntaje for r in RespuestaSUS.objects.filter(empresa__es_demo=False)]
    return render(request, "core/piloto.html", {
        "filas": filas, "adopcion": adopcion, "inicio": inicio, "fin": hoy,
        "total_usuarios": sum(f["usuarios"] for f in filas),
        "sus_global": mean(todas_sus) if todas_sus else None, "n_sus_global": len(todas_sus),
        "interpretacion": interpretar_sus(mean(todas_sus)) if todas_sus else "",
    })


@login_required
def ayuda(request):
    if request.empresa is None:
        return redirect("sin_empresa")
    hoy = timezone.localdate()
    lista, _ = ind.calcular_indicadores(request.empresa, hoy - timedelta(days=29), hoy)
    return render(request, "core/ayuda.html", {"indicadores": lista})
