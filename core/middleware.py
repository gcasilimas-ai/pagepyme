from .models import Empresa, Perfil


class EmpresaActualMiddleware:
    """Determina la empresa y el rol con los que trabaja el usuario.

    Los usuarios del piloto quedan amarrados a su empresa. El personal
    consultor (``is_staff``) puede cambiar de empresa desde la barra superior
    y actúa con permisos de gerente.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.empresa = None
        request.rol = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            try:
                perfil = user.perfil
            except Perfil.DoesNotExist:
                perfil = None

            if user.is_staff:
                empresa = None
                empresa_id = request.session.get("empresa_id")
                if empresa_id:
                    empresa = Empresa.objects.filter(pk=empresa_id).first()
                if empresa is None and perfil and perfil.empresa_id:
                    empresa = perfil.empresa
                if empresa is None:
                    empresa = Empresa.objects.filter(activa=True).first()
                request.empresa = empresa
                request.rol = Perfil.GERENTE
            elif perfil is not None:
                request.empresa = perfil.empresa
                request.rol = perfil.rol
        return self.get_response(request)
