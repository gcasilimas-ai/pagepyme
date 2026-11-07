from .models import RegistroActividad


def registrar(request, modulo, accion, detalle=""):
    """Guarda una línea en la bitácora de uso de la empresa actual."""
    empresa = getattr(request, "empresa", None)
    if empresa is None or not request.user.is_authenticated:
        return None
    return RegistroActividad.objects.create(
        empresa=empresa,
        usuario=request.user,
        modulo=modulo,
        accion=accion,
        detalle=str(detalle)[:255],
    )
