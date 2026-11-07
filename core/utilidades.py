from django.core.paginator import Paginator


def paginar(request, queryset, por_pagina=25):
    paginador = Paginator(queryset, por_pagina)
    return paginador.get_page(request.GET.get("pagina"))


def parametros_sin_pagina(request):
    """Cadena de consulta actual sin el parámetro de página (para los enlaces del paginador)."""
    copia = request.GET.copy()
    copia.pop("pagina", None)
    return copia.urlencode()
