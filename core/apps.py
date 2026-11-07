from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Núcleo"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from .models import Perfil, RegistroActividad

        def registrar_ingreso(sender, request, user, **kwargs):
            try:
                empresa = user.perfil.empresa
            except Perfil.DoesNotExist:
                empresa = None
            if empresa is not None:
                RegistroActividad.objects.create(
                    empresa=empresa, usuario=user, modulo="sesion", accion="Inicio de sesión"
                )

        user_logged_in.connect(registrar_ingreso, dispatch_uid="pagepyme_registrar_ingreso")
