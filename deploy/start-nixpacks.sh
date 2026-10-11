#!/usr/bin/env bash
set -Eeuo pipefail

worker_pid=""
web_pid=""
frontend_pid=""

# Caddy is the public server; Django listens only inside this container.
export PORT="${PORT:-8000}"
export GUNICORN_BIND="127.0.0.1:8001"
export DJANGO_UPSTREAM="127.0.0.1:8001"

shutdown() {
    trap - EXIT INT TERM
    if [[ -n "${frontend_pid}" ]]; then
        kill -TERM "${frontend_pid}" 2>/dev/null || true
    fi
    if [[ -n "${web_pid}" ]]; then
        kill -TERM "${web_pid}" 2>/dev/null || true
    fi
    if [[ -n "${worker_pid}" ]]; then
        kill -TERM "${worker_pid}" 2>/dev/null || true
    fi
    wait "${web_pid}" 2>/dev/null || true
    wait "${worker_pid}" 2>/dev/null || true
    wait "${frontend_pid}" 2>/dev/null || true
}

trap shutdown EXIT INT TERM

if [[ ! -s "frontend/dist/index.html" ]] ||
   ! compgen -G "frontend/dist/assets/*.js" > /dev/null ||
   ! compgen -G "frontend/dist/assets/*.css" > /dev/null; then
    echo "React production assets are missing. The Nixpacks build phase must run npm run build." >&2
    exit 1
fi

caddy validate --config deploy/Caddyfile --adapter caddyfile

python manage.py check --deploy
python manage.py migrate --noinput
python manage.py createcachetable "${DJANGO_CACHE_TABLE:-roadvision_cache}"
python manage.py collectstatic --noinput

if [[ "${AUTO_REGISTER_BUNDLED_MODEL:-true}" =~ ^(1|true|yes|on)$ ]]; then
    python manage.py bootstrap_bundled_model
fi

python manage.py run_video_visualizer_analysis \
    --watch \
    --poll-interval "${ANALYSIS_POLL_INTERVAL:-2}" &
worker_pid=$!

gunicorn config.wsgi:application --config gunicorn.conf.py &
web_pid=$!

caddy run --config deploy/Caddyfile --adapter caddyfile &
frontend_pid=$!

set +e
wait -n "${worker_pid}" "${web_pid}" "${frontend_pid}"
exit_code=$?
set -e

shutdown
exit "${exit_code}"
