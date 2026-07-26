# Deploy RoadVision with Coolify Nixpacks

RoadVision is prepared as one Git-based Nixpacks application. The application
container runs Gunicorn and the durable video-analysis worker together. It
connects to a separate Coolify MariaDB resource and stores uploaded and
processed media in a private S3-compatible bucket.

The repository-level `nixpacks.toml` selects Python 3.12, installs FFmpeg, and
uses `deploy/start-nixpacks.sh` as the start process. Do not add MySQL compiler
packages: RoadVision uses the pure-Python PyMySQL compatibility driver.

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
Health Check Path: /livez/
```

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

Use a restricted application database account, not MariaDB `root`. Keep the
media bucket private. Django creates short-lived signed URLs for authorized
video and snapshot access. Set `AWS_S3_ENDPOINT_URL` only for an S3-compatible
provider; leave it blank for AWS S3.

## 4. Domain and deployment

Assign the normal HTTPS domain to the application. Coolify routes it to the
exposed application port 8000. Deploy with the build cache cleared once after
switching from the old native MySQL dependency.

Startup performs:

1. Django deployment checks
2. database migrations
3. database-cache table creation
4. static-file collection
5. analysis-worker startup
6. Gunicorn startup on port 8000

Keep the application at one replica. The web server and inference worker share
the same CPU and memory allocation in this Nixpacks layout.

## 5. Verify

After the application becomes healthy, open its terminal and run:

```bash
python manage.py check --deploy
python manage.py roadvision_readiness --scope analysis --strict
```

Then verify:

- `/livez/` returns HTTP 200;
- `/healthz/` returns HTTP 200 and confirms database access;
- login works and default/development accounts are disabled;
- a short video can be uploaded, queued, processed, and viewed;
- application logs show the analysis worker claiming and completing the job;
- media URLs are signed and the bucket is not public.

## 6. Operations

Enable automated MariaDB backups in Coolify and versioning/lifecycle protection
for the object-storage bucket. Restore-test both before production use.

This CPU Nixpacks deployment supports queued video processing. It does not make
full-rate 25–30 FPS live inference reliable. A genuinely real-time service
requires a separately benchmarked GPU worker architecture.
