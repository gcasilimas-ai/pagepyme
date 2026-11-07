from django.urls import path

from . import views

app_name = "analitica"

urlpatterns = [
    path("tablero/", views.tablero, name="tablero"),
    path("calidad-de-datos/", views.calidad, name="calidad"),
    path("exportar/", views.exportar, name="exportar"),
    path("exportar/<str:conjunto>/", views.exportar_archivo, name="exportar_archivo"),
]
