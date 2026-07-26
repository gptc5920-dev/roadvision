#!/bin/sh
set -eu

python manage.py check --deploy
python manage.py migrate --noinput
python manage.py createcachetable "${DJANGO_CACHE_TABLE:-roadvision_cache}" --noinput
python manage.py collectstatic --noinput

exec gunicorn config.wsgi:application --config gunicorn.conf.py
