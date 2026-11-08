from django import forms
from django.forms import formset_factory
from django.utils import timezone

from maestros.models import Cliente, Producto

from .models import Cotizacion, Pago, Seguimiento, Venta


class ItemForm(forms.Form):
    producto = forms.ModelChoiceField(queryset=Producto.objects.none(), label="Producto", empty_label="Selecciona el producto")
    cantidad = forms.IntegerField(min_value=1, label="Cantidad")
    precio_unitario = forms.DecimalField(min_value=0, max_digits=14, decimal_places=2, label="Precio unitario")

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["producto"].queryset = Producto.objects.filter(empresa=empresa, activo=True).order_by("nombre")


ItemFormSet = formset_factory(ItemForm, extra=0, min_num=1, validate_min=True, can_delete=True)


def items_desde_formset(formset):
    items = []
    for form in formset.forms:
        if not form.has_changed() or form.cleaned_data.get("DELETE"):
            continue
        datos = form.cleaned_data
        items.append((datos["producto"], datos["cantidad"], datos["precio_unitario"]))
    return items


class _ConClientes(forms.Form):
    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = Cliente.objects.filter(empresa=empresa, activo=True).order_by("nombre")


class VentaForm(_ConClientes):
    cliente = forms.ModelChoiceField(queryset=Cliente.objects.none(), empty_label="Selecciona el cliente")
    fecha = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    forma_pago = forms.ChoiceField(choices=Venta.FORMAS_PAGO, label="Forma de pago")
    medio_pago = forms.ChoiceField(
        choices=Pago.MEDIOS, label="Medio de pago (contado)", required=False, initial="efectivo"
    )
    observaciones = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        if fecha > timezone.localdate():
            raise forms.ValidationError("La fecha de la venta no puede ser futura.")
        return fecha


class CotizacionForm(_ConClientes):
    cliente = forms.ModelChoiceField(queryset=Cliente.objects.none(), empty_label="Selecciona el cliente")
    fecha = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    vigencia_dias = forms.IntegerField(min_value=1, initial=15, label="Vigencia (días)")
    observaciones = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)


class AbonoForm(forms.Form):
    valor = forms.DecimalField(min_value=1, max_digits=16, decimal_places=2, label="Valor del abono")
    fecha = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    medio = forms.ChoiceField(choices=Pago.MEDIOS, label="Medio")
    referencia = forms.CharField(max_length=80, required=False, label="Referencia o comprobante")

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        if fecha > timezone.localdate():
            raise forms.ValidationError("La fecha no puede ser futura.")
        return fecha


class ConvertirForm(forms.Form):
    forma_pago = forms.ChoiceField(choices=Venta.FORMAS_PAGO, label="Forma de pago")


class EstadoCotizacionForm(forms.Form):
    estado = forms.ChoiceField(choices=[
        (Cotizacion.ENVIADA, "Enviada al cliente"),
        (Cotizacion.ACEPTADA, "Aceptada"),
        (Cotizacion.RECHAZADA, "Rechazada"),
    ])


class SeguimientoForm(forms.ModelForm):
    class Meta:
        model = Seguimiento
        fields = ["cliente", "fecha", "canal", "resultado", "nota", "proxima_fecha", "proxima_accion"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "proxima_fecha": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "nota": forms.Textarea(attrs={"rows": 3, "placeholder": "Qué se habló, qué necesita el cliente, objeciones…"}),
            "proxima_accion": forms.TextInput(attrs={"placeholder": "Por ejemplo: enviar cotización de dotación"}),
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = Cliente.objects.filter(empresa=empresa, activo=True).order_by("nombre")
        self.fields["cliente"].empty_label = "Selecciona el cliente"
        self.fields["resultado"].choices = [("", "Selecciona el resultado")] + list(Seguimiento.RESULTADOS)
        self.fields["proxima_fecha"].help_text = "Si queda un compromiso, la fecha aparecerá en los seguimientos pendientes."

    def clean(self):
        datos = super().clean()
        fecha, proxima = datos.get("fecha"), datos.get("proxima_fecha")
        if fecha and fecha > timezone.localdate():
            self.add_error("fecha", "La fecha de la gestión no puede ser futura.")
        if fecha and proxima and proxima < fecha:
            self.add_error("proxima_fecha", "El próximo contacto debe ser posterior a la gestión.")
        if proxima and not datos.get("proxima_accion"):
            self.add_error("proxima_accion", "Indica qué se hará en el próximo contacto.")
        return datos
