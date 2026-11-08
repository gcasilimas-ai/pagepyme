from django import forms
from django.contrib.auth import get_user_model, password_validation

from .models import Empresa, Perfil, RespuestaSUS


class MetasForm(forms.ModelForm):
    class Meta:
        model = Empresa
        fields = ["meta_rotacion", "meta_margen", "meta_dso", "meta_quiebres", "meta_recurrentes", "meta_ticket"]


class EmpresaForm(forms.ModelForm):
    class Meta:
        model = Empresa
        fields = ["nombre", "nit", "ciudad", "departamento", "sector", "numero_empleados", "anios_operacion",
                  "herramienta_previa", "fecha_vinculacion"]
        widgets = {"fecha_vinculacion": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}


class NuevoUsuarioForm(forms.Form):
    username = forms.CharField(label="Usuario", max_length=150, help_text="Sin espacios. Con este nombre se ingresa.")
    first_name = forms.CharField(label="Nombres", max_length=150)
    last_name = forms.CharField(label="Apellidos", max_length=150, required=False)
    email = forms.EmailField(label="Correo electrónico", required=False)
    rol = forms.ChoiceField(choices=Perfil.ROLES)
    password1 = forms.CharField(label="Contraseña", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Repite la contraseña", widget=forms.PasswordInput)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if " " in username:
            raise forms.ValidationError("El usuario no puede tener espacios.")
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Ese usuario ya existe.")
        return username

    def clean(self):
        datos = super().clean()
        p1, p2 = datos.get("password1"), datos.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Las contraseñas no coinciden.")
        elif p1:
            try:
                password_validation.validate_password(p1)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return datos


ESCALA = [(i, str(i)) for i in range(1, 6)]


class SUSForm(forms.ModelForm):
    class Meta:
        model = RespuestaSUS
        fields = [f"p{i}" for i in range(1, 11)] + ["comentario"]
        widgets = {"comentario": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for i, pregunta in enumerate(RespuestaSUS.PREGUNTAS, start=1):
            campo = self.fields[f"p{i}"]
            campo.label = f"{i}. {pregunta}"
            campo.widget = forms.RadioSelect(choices=ESCALA)
            campo.required = True


MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
         "Septiembre", "Octubre", "Noviembre", "Diciembre"]


class MetasComercialesForm(forms.Form):
    """Meta de ventas para cada mes de un año. Un mes vacío queda sin meta."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for i, nombre in enumerate(MESES, start=1):
            self.fields[f"mes_{i}"] = forms.DecimalField(
                label=nombre, required=False, min_value=1, max_digits=16, decimal_places=0,
                widget=forms.NumberInput(attrs={"step": "1", "inputmode": "numeric", "placeholder": "Sin meta"}),
            )

    def valores(self):
        return {i: self.cleaned_data.get(f"mes_{i}") for i in range(1, 13)}
