"""Formato colombiano: punto como separador de miles y coma decimal."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template

register = template.Library()


def _numero(valor, decimales=0):
    if valor is None or valor == "":
        return "—"
    try:
        valor = Decimal(str(valor))
    except InvalidOperation:
        return str(valor)
    cuantizador = Decimal(1).scaleb(-decimales) if decimales else Decimal(1)
    valor = valor.quantize(cuantizador, rounding=ROUND_HALF_UP)
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


@register.filter
def num(valor, decimales=0):
    return _numero(valor, int(decimales))


@register.filter
def cop(valor):
    if valor is None or valor == "":
        return "—"
    return f"$ {_numero(valor, 0)}"


@register.filter
def pct(valor, decimales=1):
    if valor is None or valor == "":
        return "—"
    return f"{_numero(valor, int(decimales))} %"


@register.filter
def valor_ind(valor, unidad):
    if valor is None:
        return "—"
    if unidad == "COP":
        return cop(valor)
    if unidad == "%":
        return pct(valor, 1)
    if unidad == "días":
        return f"{_numero(valor, 0)} días"
    if unidad == "veces/año":
        return f"{_numero(valor, 1)}"
    return _numero(valor, 1)


@register.filter
def variacion_ind(valor, unidad):
    if valor is None:
        return ""
    signo = "+" if valor > 0 else ("−" if valor < 0 else "±")
    absoluto = abs(valor)
    if unidad == "COP":
        return f"{signo}$ {_numero(absoluto, 0)}"
    if unidad == "%":
        return f"{signo}{_numero(absoluto, 1)} pts"
    if unidad == "días":
        return f"{signo}{_numero(absoluto, 0)} días"
    return f"{signo}{_numero(absoluto, 1)}"


@register.filter
def get(diccionario, clave):
    try:
        return diccionario.get(clave)
    except AttributeError:
        return None


@register.filter
def margen_item(item):
    """Margen de contribución de una línea de venta, en porcentaje."""
    if not item.precio_unitario:
        return None
    return (item.precio_unitario - item.costo_unitario) / item.precio_unitario * 100


@register.filter
def por(a, b):
    try:
        return a * b
    except TypeError:
        return None
