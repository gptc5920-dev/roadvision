FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential default-libmysqlclient-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN python -m pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ROADVISION_ENV=production \
    DJANGO_SETTINGS_MODULE=config.settings

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg libgl1 libglib2.0-0 libmariadb3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 roadvision \
    && useradd --uid 10001 --gid roadvision --create-home --shell /usr/sbin/nologin roadvision

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

WORKDIR /app
COPY --chown=roadvision:roadvision . .
RUN mkdir -p /app/media /app/staticfiles /app/models/registered \
    && chown -R roadvision:roadvision /app/media /app/staticfiles /app/models/registered \
    && chmod +x /app/deploy/start-web.sh

USER roadvision
EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--config", "gunicorn.conf.py"]
