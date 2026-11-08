from django import forms
from django.utils import timezone

from maestros.models import Producto

from .models import MovimientoInventario

TIPOS_MANUALES = [
    (MovimientoInventario.ENTRADA, "Entrada por compra"),
    (MovimientoInventario.AJUSTE_POSITIVO, "Ajuste positivo (conteo físico)"),
    (MovimientoInventario.AJUSTE_NEGATIVO, "Ajuste negativo (conteo, daño o pérdida)"),
]


class MovimientoForm(forms.Form):
    producto = forms.ModelChoiceField(queryset=Producto.objects.none(), empty_label="Selecciona el producto")
    tipo = forms.ChoiceField(choices=TIPOS_MANUALES)
    cantidad = forms.IntegerField(min_value=1)
    costo_unitario = forms.DecimalField(
        label="Costo unitario de compra (COP)", min_value=0, max_digits=14, decimal_places=2, required=False,
        help_text="Obligatorio en las entradas por compra. Con él se recalcula el costo promedio ponderado.",
    )
    fecha = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    observacion = forms.CharField(label="Observación", max_length=200, required=False,
                                  help_text="Para ajustes, explica el motivo (por ejemplo: conteo del 30 de octubre).")

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["producto"].queryset = Producto.objects.filter(empresa=empresa, activo=True).order_by("nombre")

    def clean(self):
        datos = super().clean()
        if datos.get("tipo") == MovimientoInventario.ENTRADA and datos.get("costo_unitario") in (None, ""):
            self.add_error("costo_unitario", "Indica el costo de compra de esta entrada.")
        if datos.get("tipo") in {MovimientoInventario.AJUSTE_POSITIVO, MovimientoInventario.AJUSTE_NEGATIVO} \
                and not datos.get("observacion"):
            self.add_error("observacion", "Los ajustes necesitan un motivo para mantener la trazabilidad.")
        if datos.get("fecha") and datos["fecha"] > timezone.localdate():
            self.add_error("fecha", "La fecha no puede ser futura.")
        return datos
