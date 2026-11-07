from django.urls import path

from . import views

app_name = "maestros"

urlpatterns = [
    path("productos/", views.productos, name="productos"),
    path("productos/nuevo/", views.producto_form, name="producto_nuevo"),
    path("productos/<int:pk>/", views.producto_detalle, name="producto"),
    path("productos/<int:pk>/editar/", views.producto_form, name="producto_editar"),
    path("categorias/", views.categorias, name="categorias"),
    path("categorias/nueva/", views.categoria_form, name="categoria_nueva"),
    path("categorias/<int:pk>/editar/", views.categoria_form, name="categoria_editar"),
    path("clientes/", views.clientes, name="clientes"),
    path("clientes/nuevo/", views.cliente_form, name="cliente_nuevo"),
    path("clientes/<int:pk>/", views.cliente_detalle, name="cliente"),
    path("clientes/<int:pk>/editar/", views.cliente_form, name="cliente_editar"),
    path("importar/", views.importar, name="importar"),
    path("importar/lote/<int:pk>/", views.lote, name="lote"),
    path("importar/plantilla/<str:tipo>/", views.plantilla, name="plantilla"),
]
