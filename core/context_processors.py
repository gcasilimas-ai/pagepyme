from .models import Empresa
from .permisos import AREAS, puede


def contexto_global(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    permisos = {area: puede(request, area) for area in AREAS}
    contexto = {
        "empresa_actual": getattr(request, "empresa", None),
        "rol_actual": getattr(request, "rol", None),
        "permisos": permisos,
    }
    if request.user.is_staff:
        contexto["empresas_disponibles"] = Empresa.objects.filter(activa=True)
    return contexto
