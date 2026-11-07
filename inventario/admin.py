from django.contrib import admin

from .models import MovimientoInventario


@admin.register(MovimientoInventario)
class MovimientoInventarioAdmin(admin.ModelAdmin):
    list_display = ("fecha", "empresa", "producto", "tipo", "cantidad", "costo_unitario", "usuario")
    list_filter = ("empresa", "tipo")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
