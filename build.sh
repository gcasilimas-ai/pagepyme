#!/usr/bin/env bash
# Construcción en Render: dependencias, archivos estáticos, migraciones y datos iniciales.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input

# Empresa de demostración con datos ficticios (se recrea solo si REINICIAR_DEMO=1)
if [ "${REINICIAR_DEMO:-0}" = "1" ]; then
  python manage.py cargar_demo
else
  python manage.py cargar_demo --si-no-existe
fi

# Usuario del equipo consultor (administración y resumen del piloto)
if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  if python manage.py shell -c "import os, sys; from django.contrib.auth import get_user_model; sys.exit(0 if get_user_model().objects.filter(username=os.environ['DJANGO_SUPERUSER_USERNAME']).exists() else 1)"; then
    echo "El usuario administrador ya existe; no se modifica."
  else
    python manage.py createsuperuser --no-input
  fi
fi
