from django.urls import path

from . import views

app_name = "comercial"

urlpatterns = [
    path("ventas/", views.ventas, name="ventas"),
    path("ventas/nueva/", views.venta_nueva, name="venta_nueva"),
    path("ventas/<int:pk>/", views.venta_detalle, name="venta"),
    path("ventas/<int:pk>/abono/", views.venta_abono, name="venta_abono"),
    path("ventas/<int:pk>/anular/", views.venta_anular, name="venta_anular"),
    path("cartera/", views.cartera, name="cartera"),
    path("seguimientos/", views.seguimientos, name="seguimientos"),
    path("seguimientos/nuevo/", views.seguimiento_nuevo, name="seguimiento_nuevo"),
    path("cotizaciones/", views.cotizaciones, name="cotizaciones"),
    path("cotizaciones/nueva/", views.cotizacion_nueva, name="cotizacion_nueva"),
    path("cotizaciones/<int:pk>/", views.cotizacion_detalle, name="cotizacion"),
    path("cotizaciones/<int:pk>/estado/", views.cotizacion_estado, name="cotizacion_estado"),
    path("cotizaciones/<int:pk>/convertir/", views.cotizacion_convertir, name="cotizacion_convertir"),
    path("cotizaciones/<int:pk>/imprimir/", views.cotizacion_imprimir, name="cotizacion_imprimir"),
]
