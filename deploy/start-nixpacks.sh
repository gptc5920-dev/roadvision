#!/usr/bin/env bash
set -Eeuo pipefail

worker_pid=""
web_pid=""

shutdown() {
    trap - EXIT INT TERM
    if [[ -n "${web_pid}" ]]; then
        kill -TERM "${web_pid}" 2>/dev/null || true
    fi
    if [[ -n "${worker_pid}" ]]; then
        kill -TERM "${worker_pid}" 2>/dev/null || true
    fi
    wait "${web_pid}" 2>/dev/null || true
    wait "${worker_pid}" 2>/dev/null || true
}

trap shutdown EXIT INT TERM

python manage.py check --deploy
python manage.py migrate --noinput
python manage.py createcachetable "${DJANGO_CACHE_TABLE:-roadvision_cache}" --noinput
python manage.py collectstatic --noinput

python manage.py run_video_visualizer_analysis \
    --watch \
    --poll-interval "${ANALYSIS_POLL_INTERVAL:-2}" &
worker_pid=$!

gunicorn config.wsgi:application --config gunicorn.conf.py &
web_pid=$!

set +e
wait -n "${worker_pid}" "${web_pid}"
exit_code=$?
set -e

shutdown
exit "${exit_code}"
