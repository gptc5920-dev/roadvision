# RoadVision production deployment

For a Coolify-managed server, use the proxy-free deployment in
[`COOLIFY.md`](COOLIFY.md) instead of this standalone stack.

This package runs RoadVision as four isolated services:

- `proxy`: Nginx reverse proxy and authenticated local-media delivery
- `web`: Gunicorn/Django web application
- `analysis-worker`: durable video and live-stream inference worker
- `db`: MariaDB 10.11

The optional `training-worker` starts only with the `training` profile. For a
larger deployment, replace the bundled database and local media volumes with a
managed MariaDB service and private S3-compatible storage.

## 1. Host prerequisites

- Docker Engine with the Compose plugin
- A DNS hostname
- HTTPS termination at a load balancer, ingress, or trusted reverse proxy
- Enough durable storage for the database, video media, model artifacts, and backups
- A dedicated CUDA-capable analysis host if full-rate live inference is required

The included image is CPU-compatible. Deployment packaging does not make the
current CPU-only machine capable of full 25–30 FPS inference.

## 2. Configure secrets

Copy `.env.production.example` to `.env.production` and replace every
placeholder. Generate a secret without putting it in shell history:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Set the public hostname in both `DJANGO_ALLOWED_HOSTS` and
`DJANGO_CSRF_TRUSTED_ORIGINS`. Keep `localhost` and `127.0.0.1` in allowed hosts
for container health checks.

`DJANGO_SECURE_SSL_REDIRECT=true` assumes the public request reaches the
included Nginx service through an HTTPS-terminating proxy that preserves
`X-Forwarded-Proto: https`. For a local, non-public smoke test only, set it to
`false`.

## 3. Validate and build

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml config
docker compose --env-file .env.production -f docker-compose.prod.yml build
```

The production image uses Django 5.2 LTS and Python 3.12. Do not connect it to
the old XAMPP MariaDB 10.4 database. Import data into MariaDB 10.11 or another
Django-supported production database first.

## 4. Restore durable data

Before the first public start:

1. Restore the MariaDB backup into the `db_data` volume or managed database.
2. Restore the `media_data` volume, or configure the private S3 variables.
3. Restore model files into `model_data`.
4. Verify that the stored model SHA-256 still matches the artifact.

Model paths are resolved portably, so database records imported from a Windows
installation can find a same-named artifact in `/app/models/registered`.

For a fresh database, register and activate a model only with its real
validation evidence:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml run --rm web \
  python manage.py register_pothole_model /app/models/registered/model.pt \
  --name pothole-production --map50 0.72 \
  --validated-by "Independent validation set and report reference" --activate
```

## 5. Start services

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d db web analysis-worker proxy
```

The web startup performs deployment checks, database migrations, cache-table
creation, and static-file collection before Gunicorn starts.

Start automatic training only on a host sized for it:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml --profile training up -d training-worker
```

## 6. Run release checks

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml exec web \
  python manage.py check --deploy

docker compose --env-file .env.production -f docker-compose.prod.yml exec web \
  python manage.py roadvision_readiness --scope analysis --strict
```

The analysis-only readiness scope intentionally does not require a training
dataset. Run the full training gate separately:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml exec web \
  python manage.py roadvision_readiness --scope training --strict
```

Health endpoints:

- `/livez/`: process liveness without a database query
- `/healthz/`: application readiness with a database query

## 7. First-user and security tasks

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml exec web \
  python manage.py createsuperuser
```

Before public access:

- rotate or disable every development/default account;
- verify HTTPS and secure-cookie behavior from the public hostname;
- keep camera credentials out of saved stream URLs;
- restrict database and object-storage credentials to this application;
- configure encrypted backups and perform a restore drill;
- configure centralized log collection and disk/queue alerts;
- allow private RTSP networks only when `ALLOW_PRIVATE_STREAMS=true` is required;
- confirm local `/media/` requests are rejected when unauthenticated.

## 8. Upgrade and rollback

Create database and media backups before every release. Then:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml build
docker compose --env-file .env.production -f docker-compose.prod.yml up -d
```

Application rollback requires the previous image plus a database backup if the
release applied a non-reversible migration. Never roll back code across schema
changes without reviewing the migration plan.
