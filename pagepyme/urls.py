from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core import views as core
from core import vinculacion

admin.site.site_header = "PAGE-PYME · Administración"
admin.site.site_title = "PAGE-PYME"

urlpatterns = [
    path("", core.inicio, name="inicio"),
    path("ingresar/", core.Ingreso.as_view(), name="login"),
    path("salir/", auth_views.LogoutView.as_view(), name="logout"),
    path("sin-empresa/", core.sin_empresa, name="sin_empresa"),
    path("empresa/cambiar/", core.cambiar_empresa, name="cambiar_empresa"),
    path("configuracion/metas/", core.metas, name="metas"),
    path("configuracion/empresa/", core.empresa_datos, name="empresa"),
    path("configuracion/equipo/", core.equipo, name="equipo"),
    path("configuracion/equipo/<int:pk>/estado/", core.equipo_estado, name="equipo_estado"),
    path("piloto/empresas/", vinculacion.empresas_piloto, name="empresas_piloto"),
    path("piloto/empresas/<int:pk>/reiniciar/", vinculacion.empresa_reiniciar, name="empresa_reiniciar"),
    path("piloto/evidencia.xlsx", vinculacion.evidencia_piloto, name="evidencia_piloto"),
    path("evidencia/uso/", core.uso, name="uso"),
    path("evidencia/usabilidad/", core.sus_resultados, name="sus_resultados"),
    path("evidencia/piloto/", core.piloto, name="piloto"),
    path("encuesta/", core.encuesta, name="encuesta"),
    path("ayuda/", core.ayuda, name="ayuda"),
    path("maestros/", include("maestros.urls")),
    path("comercial/", include("comercial.urls")),
    path("inventario/", include("inventario.urls")),
    path("analitica/", include("analitica.urls")),
    path("admin/", admin.site.urls),
]
