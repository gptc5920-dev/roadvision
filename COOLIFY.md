# Deploy RoadVision with Coolify

RoadVision has a dedicated Coolify Compose definition:
`docker-compose.coolify.yml`. Coolify terminates HTTPS and routes the public
domain directly to the `web` container. Do not add a public host-port mapping
or deploy the standalone Nginx service from `docker-compose.prod.yml`.

Use Coolify v4.0.0-beta.411 or newer; Git-based Compose deployments need that
version for the generated secret variables used by this stack.

The Coolify deployment starts:

- `web`: Gunicorn/Django on internal port 8000
- `analysis-worker`: durable queued-video and live-stream inference
- `db`: MariaDB 10.11 with a persistent named volume

Uploaded videos, generated videos, snapshots, and detection artifacts use a
private S3-compatible bucket. Static assets are served by WhiteNoise. The
validated model artifact is bundled into the immutable application image.

## 1. Create the resource

1. Push this repository, including the registered production model, to a
   private Git repository that Coolify can access.
2. In Coolify, create a Git-based application and change its build pack from
   the default **Nixpacks** option to **Docker Compose**.
3. Set **Base Directory** to `/`.
4. Set **Docker Compose Location** to `/docker-compose.coolify.yml`.
5. Select the intended branch and server.

The deployment log must say that Coolify is loading a Docker Compose build. If
it says `Generating nixpacks configuration`, stop: that resource is using the
wrong build pack and will omit the database and analysis-worker services.

Coolify detects all `${VARIABLE}` references in the Compose file. The
`SERVICE_REALBASE64_64_DJANGO`, `SERVICE_PASSWORD_64_DBUSER`, and
`SERVICE_PASSWORD_64_DBROOT` variables are Coolify magic variables and are
generated automatically.

## 2. Configure the environment

Set these required variables in the Coolify resource before deploying:

```dotenv
DJANGO_ALLOWED_HOSTS=roadvision.example.gov.ph
DJANGO_CSRF_TRUSTED_ORIGINS=https://roadvision.example.gov.ph
AWS_STORAGE_BUCKET_NAME=roadvision-media
AWS_S3_REGION_NAME=ap-southeast-1
AWS_S3_ENDPOINT_URL=
AWS_ACCESS_KEY_ID=restricted-bucket-access-key
AWS_SECRET_ACCESS_KEY=restricted-bucket-secret-key
```

Use an access key restricted to the RoadVision media bucket. Keep the bucket
private; Django creates short-lived signed URLs for authorized media access.
For AWS S3, leave `AWS_S3_ENDPOINT_URL` empty. For an S3-compatible provider,
set its HTTPS API endpoint.

The remaining variables have safe defaults in the Compose file. Review
`.env.coolify.example` when tuning workers, model policy, or private camera
access. Never paste `.env.production` from a workstation into Coolify.

## 3. Configure the public domain

In the Coolify service view, assign this domain to the `web` service:

```text
https://roadvision.example.gov.ph:8000
```

The `:8000` suffix tells Coolify which internal container port receives proxy
traffic; users browse the normal HTTPS URL without that internal port. Point
the hostname's DNS record to the Coolify server. Coolify provisions and renews
the TLS certificate.

Do not assign domains to `db` or `analysis-worker`, and do not expose their
ports publicly.

## 4. Deploy and initialize

Deploy the resource. The web container waits for MariaDB, runs Django deployment
checks and migrations, creates the database-backed cache table, collects static
assets, and then starts Gunicorn. The analysis worker starts after the web
health check passes.

For a fresh database, open the `web` service terminal and create the first
administrator:

```bash
python manage.py createsuperuser
```

The repository currently contains the registered model artifact. When importing
an existing database, RoadVision resolves its former Windows model path to the
same-named artifact under `/app/models/registered`.

## 5. Verify the release

In the `web` service terminal, run:

```bash
python manage.py check --deploy
python manage.py roadvision_readiness --scope analysis --strict
```

Then verify:

- `https://roadvision.example.gov.ph/livez/` returns HTTP 200;
- login works and no default/development account remains active;
- one short video can be uploaded, queued, processed, and viewed;
- the `analysis-worker` logs show the job completing;
- signed media links expire and the bucket itself is not publicly listable.

`/livez/` is the container liveness endpoint. `/healthz/` additionally checks
the database and is useful for external monitoring.

## 6. Persistence, backups, and sizing

The MariaDB `db_data` named volume is persistent and managed by Coolify. Enable
scheduled encrypted database backups in Coolify and lifecycle/versioning rules
for the object-storage bucket, then perform a restore drill before production
use.

Start with at least 2 vCPU and 4 GB RAM for the web/database services plus
separate inference capacity. CPU video analysis can process queued footage but
will not reliably sustain full-rate 25–30 FPS live inference. Put the analysis
worker on a CUDA-capable host and benchmark representative footage before
advertising live analysis as real-time.

Automatic model training is deliberately not part of the Coolify stack. Run it
as a separately sized GPU workload after the reviewed dataset readiness gate
passes.

## 7. Updating and rollback

Before every deployment, create a database backup and preserve the current
application image or Git revision. Deploy the new revision through Coolify and
repeat the release checks.

Rolling back application code is safe only when its database schema remains
compatible. If a deployment ran a non-reversible migration, restore the matching
database backup with the previous revision.
