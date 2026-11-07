from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .models import Perfil

G, C, B = Perfil.GERENTE, Perfil.COMERCIAL, Perfil.BODEGA

# Qué roles entran a cada área de la aplicación.
AREAS = {
    "tablero": {G, C},
    "comercial": {G, C},
    "clientes": {G, C},
    "productos_ver": {G, C, B},
    "productos_editar": {G, B},
    "inventario": {G, B},
    "importar": {G},
    "evidencia": {G},
    "configuracion": {G},
    "sus": {G, C, B},
}


def puede(request, area):
    if not request.user.is_authenticated or request.empresa is None:
        return False
    if request.user.is_staff:
        return True
    return request.rol in AREAS.get(area, set())


def requiere(area):
    """Decorador de vista: exige sesión, empresa asignada y rol autorizado."""

    def decorador(vista):
        @login_required
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if request.empresa is None:
                return redirect("sin_empresa")
            if not puede(request, area):
                raise PermissionDenied("Tu rol no tiene acceso a esta sección.")
            return vista(request, *args, **kwargs)

        return envoltura

    return decorador
