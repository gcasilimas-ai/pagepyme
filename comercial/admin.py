from django.contrib import admin

from .models import Cotizacion, Pago, Venta


class SoloLectura(admin.ModelAdmin):
    """Los documentos se crean desde la aplicación para respetar las reglas de inventario."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Venta)
class VentaAdmin(SoloLectura):
    list_display = ("codigo", "empresa", "cliente", "fecha", "forma_pago", "total", "estado")
    list_filter = ("empresa", "forma_pago", "estado")


@admin.register(Cotizacion)
class CotizacionAdmin(SoloLectura):
    list_display = ("codigo", "empresa", "cliente", "fecha", "total", "estado")
    list_filter = ("empresa", "estado")


@admin.register(Pago)
class PagoAdmin(SoloLectura):
    list_display = ("venta", "fecha", "valor", "medio")
