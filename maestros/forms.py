from django import forms

from .importacion import COLUMNAS
from .models import Categoria, Cliente, Producto


class FormularioEmpresa(forms.ModelForm):
    """Formulario base que fija la empresa y valida unicidad dentro de ella."""

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        if self.instance and not self.instance.pk:
            self.instance.empresa = empresa


class CategoriaForm(FormularioEmpresa):
    class Meta:
        model = Categoria
        fields = ["nombre", "meta_margen"]

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()
        repetida = Categoria.objects.filter(empresa=self.empresa, nombre__iexact=nombre).exclude(pk=self.instance.pk)
        if repetida.exists():
            raise forms.ValidationError("Ya existe una categoría con ese nombre.")
        return nombre


class ProductoForm(FormularioEmpresa):
    class Meta:
        model = Producto
        fields = ["codigo", "nombre", "categoria", "unidad", "costo_unitario", "precio_venta", "stock_minimo", "activo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categoria"].queryset = Categoria.objects.filter(empresa=self.empresa)
        self.fields["categoria"].empty_label = "Sin categoría"
        if self.instance.pk:
            self.fields["costo_unitario"].help_text = (
                "Se recalcula con cada entrada de inventario. Modifícalo solo para corregir un error de registro."
            )

    def clean_codigo(self):
        codigo = self.cleaned_data["codigo"].strip().upper()
        repetido = Producto.objects.filter(empresa=self.empresa, codigo__iexact=codigo).exclude(pk=self.instance.pk)
        if repetido.exists():
            raise forms.ValidationError("Ya existe un producto con este código.")
        return codigo


class ClienteForm(FormularioEmpresa):
    class Meta:
        model = Cliente
        fields = ["tipo_documento", "documento", "nombre", "ciudad", "telefono", "email", "plazo_credito_dias", "activo"]

    def clean_documento(self):
        documento = self.cleaned_data["documento"].replace(".", "").replace(" ", "").strip()
        repetido = Cliente.objects.filter(empresa=self.empresa, documento=documento).exclude(pk=self.instance.pk)
        if repetido.exists():
            raise forms.ValidationError("Ya existe un cliente con este documento.")
        return documento


class ImportarForm(forms.Form):
    tipo = forms.ChoiceField(
        label="¿Qué vas a cargar?",
        choices=[
            ("productos", "1. Productos"), ("clientes", "2. Clientes"), ("ventas", "3. Ventas históricas"),
            ("compras", "4. Compras históricas (opcional)"), ("inventario", "5. Inventario actual"),
        ],
    )
    archivo = forms.FileField(label="Archivo (.xlsx o .csv)")

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        if not archivo.name.lower().endswith((".xlsx", ".csv")):
            raise forms.ValidationError("Usa un archivo .xlsx o .csv.")
        if archivo.size > 5 * 1024 * 1024:
            raise forms.ValidationError("El archivo supera 5 MB.")
        return archivo

    @staticmethod
    def columnas():
        return COLUMNAS
