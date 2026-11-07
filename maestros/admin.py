from django.contrib import admin

from .models import Categoria, Cliente, LoteImportacion, Producto


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "empresa", "categoria", "costo_unitario", "precio_venta", "stock_actual", "activo")
    list_filter = ("empresa", "activo")
    search_fields = ("codigo", "nombre")
    readonly_fields = ("stock_actual",)


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("nombre", "documento", "empresa", "ciudad", "plazo_credito_dias", "activo")
    list_filter = ("empresa",)
    search_fields = ("nombre", "documento")


admin.site.register(Categoria)


@admin.register(LoteImportacion)
class LoteImportacionAdmin(admin.ModelAdmin):
    list_display = ("fecha", "empresa", "tipo", "archivo_nombre", "estado", "filas_validas", "filas_con_error")
    list_filter = ("empresa", "tipo", "estado")
