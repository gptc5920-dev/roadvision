# Deploy RoadVision API and React with Coolify Nixpacks

RoadVision uses two Git-based Coolify applications: Django at
`https://api.roadsense.site` and React at `https://roadsense.site`. The API
container runs Gunicorn and the durable video-analysis worker. It connects
to a separate MariaDB resource and stores uploaded and processed media in a
private S3-compatible bucket or persistent volume.

The repository-level `nixpacks.toml` selects Python 3.12, installs Python
dependencies and FFmpeg, and uses `deploy/start-nixpacks.sh` as the start process.
It does not build or serve React. Do not add MySQL
compiler packages: RoadVision uses the pure-Python PyMySQL compatibility driver.

The independently deployable React repository has its own `package.json`,
`package-lock.json`, `vite.config.js`, `nixpacks.toml`, and `Caddyfile`.

## 1. Create MariaDB

Create a MariaDB 10.11 database resource in the same Coolify project and
environment. Record its internal hostname, port, database, username, and
password. Do not expose the database publicly.

The application and database must be on a network where the database's internal
hostname resolves. Enable Coolify's predefined-network connection if the two
resources are otherwise isolated.

## 2. Configure the Django API application

Create or edit the Git-based application for `gptc5920-dev/roadvision`:

```text
Build Pack: Nixpacks
Base Directory: /
Domain: https://api.roadsense.site
Port Exposes: 8000
Health Check Port: 8000
Health Check Path: /livez/
Is it a static site?: No
```

The health check must use `/livez/`, not `/`. RoadVision permits loopback
hostnames for Coolify's internal probe and exempts only the health endpoints
from the production HTTPS redirect.

Gunicorn listens on `PORT` (8000 by default). Keep `PORT` and Coolify's
exposed/health-check ports equal. Startup sets the public Gunicorn bind
automatically. The API root returns JSON; `/login` and `/app/*` belong to React.

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
DJANGO_ALLOWED_HOSTS=api.roadsense.site
DJANGO_CSRF_TRUSTED_ORIGINS=https://roadsense.site,https://api.roadsense.site
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

These variables must be available at runtime. Django accepts the API hostname
and trusts the React origin for login and other CSRF-protected writes. Do not
set a cross-domain session or CSRF cookie domain; the frontend proxy keeps
browser requests and cookies on `roadsense.site`.

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

Assign `https://api.roadsense.site` to the API application. Coolify routes it to the
exposed application port 8000. Deploy with the build cache cleared once after
switching from the old native MySQL dependency.

The generated API Nixpacks plan must contain Python installation without
Node/npm phases. The React application builds separately.
An initial `docker stop` message saying `No such container` is helper cleanup;
the later build error determines why deployment failed.

The Python provider installs from `requirements.txt`. Startup performs:

1. Django deployment checks
2. database migrations
3. database-cache table creation
4. Django admin static-file collection
5. analysis-worker startup
6. Gunicorn API startup on public port 8000

Startup supervises both processes. If either process exits, it shuts down
the others and exits so Coolify can restart the application.

### React application at roadsense.site

Use `gptc5920-dev/roadvision-frontend` as the frontend source repository. It
contains the complete build setup; serving its raw `index.html` as a static
directory leaves the `RV Loading RoadVision…` placeholder visible because
the browser cannot run uncompiled JSX.

```text
Build Pack: Nixpacks
Base Directory: /
Domain: https://roadsense.site
Port Exposes: 80
Health Check Port: 80
Health Check Path: /frontend-healthz/
Is it a static site?: No
```

Leave Install Command, Build Command, Start Command, and Publish Directory
empty. Its `nixpacks.toml` runs `npm ci`, builds React into `dist/`, then starts
Caddy using the repository `Caddyfile`. Set these frontend runtime variables:

```dotenv
PORT=80
API_UPSTREAM=https://api.roadsense.site
```

Caddy serves all React page routes and forwards API, health, Django admin,
and media/download requests to the API domain. It sets upstream host headers
to the API hostname while preserving the browser Origin header for Django's
CSRF validation. No browser CORS configuration is needed for this proxy setup.
Redeploy both applications after changing their repository commits/settings.

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
- `https://roadsense.site/` loads the React page and references built `/assets/*` files;
- a nested React route such as `https://roadsense.site/app/analyzer` loads after a hard refresh;
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
