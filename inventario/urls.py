from django.urls import path

from . import views

app_name = "inventario"

urlpatterns = [
    path("movimientos/", views.movimientos, name="movimientos"),
    path("movimientos/nuevo/", views.movimiento_nuevo, name="movimiento_nuevo"),
    path("alertas/", views.alertas, name="alertas"),
]
