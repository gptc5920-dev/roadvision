# Deploy RoadVision with Coolify Nixpacks

RoadVision is prepared as one Git-based Nixpacks application. The application
container runs a separate Caddy frontend server, Gunicorn API server, and
the durable video-analysis worker together. It
connects to a separate Coolify MariaDB resource and stores uploaded and
processed media in a private S3-compatible bucket.

The repository-level `nixpacks.toml` selects Python 3.12 and Node.js 22,
installs Python and npm dependencies, builds the React SPA, installs Caddy and FFmpeg,
and uses `deploy/start-nixpacks.sh` as the start process. Do not add MySQL
compiler packages: RoadVision uses the pure-Python PyMySQL compatibility driver.

Python and Node setup phases pin the same Nixpkgs revision. Keep these pins
aligned: Python exports its C++ runtime through `LD_LIBRARY_PATH`, and mixing
an older Python package set with a newer Node/ICU package set can make `npm ci`
fail with `CXXABI_1.3.15 not found` before the application image is built.

## 1. Create MariaDB

Create a MariaDB 10.11 database resource in the same Coolify project and
environment. Record its internal hostname, port, database, username, and
password. Do not expose the database publicly.

The application and database must be on a network where the database's internal
hostname resolves. Enable Coolify's predefined-network connection if the two
resources are otherwise isolated.

## 2. Configure the Nixpacks application

Create or edit the Git-based application for `gptc5920-dev/roadvision`:

```text
Build Pack: Nixpacks
Base Directory: /
Port Exposes: 8000
Health Check Port: 8000
Health Check Path: /livez/
Is it a static site?: No
```

The health check must use `/livez/`, not `/`. RoadVision permits loopback
hostnames for Coolify's internal probe and exempts only the health endpoints
from the production HTTPS redirect.

Caddy listens on `PORT` (8000 by default) and serves `frontend/dist/` using
`deploy/Caddyfile`. It forwards `/api/*`, protected download routes, health
checks, and the native Django admin to Gunicorn at `127.0.0.1:8001`. All page
routes are served by React. Keep `PORT` and Coolify's exposed/health-check ports
equal. Remove any old `GUNICORN_BIND` override; startup fixes Django's internal
bind to port 8001 so it cannot conflict with the frontend server.

On startup, RoadVision verifies the bundled model artifact against its pinned
SHA-256 digest and idempotently registers it as the active standard detection
model. Set `AUTO_REGISTER_BUNDLED_MODEL=false` only when model activation is
managed separately.

Leave these fields empty so `nixpacks.toml` remains the source of truth:

- Install Command
- Build Command
- Start Command
- Publish Directory
- Docker Image and Docker Image Tag
- Custom Docker Options

Remove the `NIXPACKS_APT_PKGS` variable previously used for
`default-libmysqlclient-dev`. It caused the failed native `mysqlclient` build
and is no longer needed.

## 3. Set environment variables

Add the variables from `.env.coolify.example` to the application's Environment
Variables page. At minimum, replace every placeholder in:

```dotenv
ROADVISION_ENV=production
PORT=8000
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=replace-with-at-least-50-random-characters
DJANGO_ALLOWED_HOSTS=roadvision.example.gov.ph
DJANGO_CSRF_TRUSTED_ORIGINS=https://roadvision.example.gov.ph
MYSQL_DATABASE=roadvision
MYSQL_USER=roadvision
MYSQL_PASSWORD=replace-with-the-database-password
MYSQL_HOST=replace-with-the-database-internal-hostname
MYSQL_PORT=3306
AWS_STORAGE_BUCKET_NAME=roadvision-media
AWS_S3_REGION_NAME=ap-southeast-1
AWS_ACCESS_KEY_ID=replace-with-restricted-access-key
AWS_SECRET_ACCESS_KEY=replace-with-restricted-secret-key
```

Use your actual browser domain in both Django variables, even though Caddy
serves the frontend. For `https://api.roadsense.site`, use
`DJANGO_ALLOWED_HOSTS=api.roadsense.site` and
`DJANGO_CSRF_TRUSTED_ORIGINS=https://api.roadsense.site`. These variables must
be available at runtime. Caddy preserves the original host and trusts HTTPS
forwarding headers from the private Coolify proxy network.

Use a restricted application database account, not MariaDB `root`. Keep the
media bucket private. Django creates short-lived signed URLs for authorized
video and snapshot access. Set `AWS_S3_ENDPOINT_URL` only for an S3-compatible
provider; leave it blank for AWS S3.

If S3 is not configured, RoadVision stores media under `/app/media` and serves
review videos through an authenticated byte-range endpoint. In Coolify, add a
persistent volume before uploading production footage:

```text
Name: roadvision-media
Destination Path: /app/media
```

Without this volume, uploads and processed videos are deleted when Coolify
replaces the application container during a deployment. Database records for
those deleted files cannot restore the footage; upload and analyze it again.

## 4. Domain and deployment

Assign the normal HTTPS domain to the application. Coolify routes it to the
exposed application port 8000. Deploy with the build cache cleared once after
switching from the old native MySQL dependency.

After updating the Nixpkgs pins, redeploy the updated commit with the build
cache cleared. In the generated Nixpacks plan, both `setup` and `node:setup`
must show `e6f23dc08d3624daab7094b701aa3954923c6bbb` as `nixpkgsArchive`.
The `build` phase must depend on both `install` and `node:install`.
An initial `docker stop` message saying `No such container` is helper cleanup;
the later build error determines why deployment failed.

The Python and Node providers install from `requirements.txt` and
`package-lock.json`; initialize the `frontend/` Git submodule in the source
checkout before the image build runs `npm run build`. Startup verifies
that the compiled React assets exist, then performs:

1. Django deployment checks
2. database migrations
3. database-cache table creation
4. Django admin static-file collection
5. analysis-worker startup
6. Gunicorn API startup on internal port 8001
7. Caddy frontend startup on public port 8000

Startup supervises all three processes. If any process exits, it shuts down
the others and exits so Coolify can restart the application.

Keep the application at one replica. The web server and inference worker share
the same CPU and memory allocation in this Nixpacks layout.

Browser live detection requires the normal HTTPS domain because phone and
desktop browsers block camera access on insecure origins. The default live
camera settings are conservative for a CPU deployment:

```dotenv
LIVE_DETECTION_FRAME_INTERVAL_MS=900
LIVE_DETECTION_MAX_DIMENSION=1280
```

Use **Video Analyzer > Live Camera Detect** for phone, tablet, laptop, and USB
cameras. Use **Fleet Cams > Continuous Detection** for network dashcams and IP
cameras that expose an RTSP or HTTP stream. Private network stream URLs require
`ALLOW_PRIVATE_STREAMS=true`; enable it only when the application network is
trusted.

## 5. Verify

After the application becomes healthy, open its terminal and run:

```bash
python manage.py check --deploy
python manage.py roadvision_readiness --scope analysis --strict
```

Then verify:

- `/livez/` returns HTTP 200;
- `/healthz/` returns HTTP 200 and confirms database access;
- a nested SPA route such as `/app/analyzer` loads directly after a hard refresh;
- `/api/session/` returns JSON and sets the CSRF cookie used by React;
- login works and default/development accounts are disabled;
- a short video can be uploaded, queued, processed, and viewed;
- application logs show the analysis worker claiming and completing the job;
- phone camera permission works over HTTPS and Live Camera Detect draws current
  detections;
- a registered RTSP/HTTP test dashcam can start and stop from Fleet Cams;
- media URLs are signed and the bucket is not public.

## 6. Operations

Enable automated MariaDB backups in Coolify and versioning/lifecycle protection
for the object-storage bucket. Restore-test both before production use.

This CPU Nixpacks deployment supports queued video processing and controlled-rate
live screening. It does not make full-rate 25–30 FPS inference reliable. A
genuinely real-time service requires a separately benchmarked GPU worker
architecture.
