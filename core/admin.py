from django.contrib import admin

from .models import Empresa, Perfil, RegistroActividad, RespuestaSUS


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "ciudad", "numero_empleados", "fecha_vinculacion", "es_demo", "activa")
    list_filter = ("es_demo", "activa", "ciudad")
    search_fields = ("nombre", "nit")
    fieldsets = (
        ("Caracterización", {"fields": ("nombre", "nit", "ciudad", "departamento", "sector", "numero_empleados",
                                        "anios_operacion", "herramienta_previa", "fecha_vinculacion", "es_demo", "activa")}),
        ("Metas de los indicadores", {"fields": ("meta_rotacion", "meta_margen", "meta_dso", "meta_quiebres",
                                                 "meta_recurrentes", "meta_ticket")}),
    )


@admin.register(Perfil)
class PerfilAdmin(admin.ModelAdmin):
    list_display = ("usuario", "empresa", "rol")
    list_filter = ("empresa", "rol")
    autocomplete_fields = ("usuario",)


@admin.register(RegistroActividad)
class RegistroActividadAdmin(admin.ModelAdmin):
    list_display = ("fecha", "empresa", "usuario", "modulo", "accion", "detalle")
    list_filter = ("empresa", "modulo")
    date_hierarchy = "fecha"

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(RespuestaSUS)
class RespuestaSUSAdmin(admin.ModelAdmin):
    list_display = ("fecha", "empresa", "usuario", "puntaje")
    list_filter = ("empresa",)

    def has_change_permission(self, request, obj=None):
        return False
