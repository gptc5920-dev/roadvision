import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from console.models import VideoVisualizerAnalysis, VideoVisualizerStatus
from console.readiness import dataset_readiness, model_readiness


class Command(BaseCommand):
    help = "Check database, storage, security, dataset, model, and worker readiness."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true", help="Return a failing exit code when blocked.")

    def handle(self, *args, **options):
        failures = []
        warnings = []
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE(), VERSION()")
            database_name, database_version = cursor.fetchone()
        self.stdout.write(f"Database: {database_name} ({database_version})")

        media_root = Path(settings.MEDIA_ROOT)
        media_root.mkdir(parents=True, exist_ok=True)
        free_gb = shutil.disk_usage(media_root).free / (1024 ** 3)
        self.stdout.write(f"Media storage free: {free_gb:.2f} GB")
        if free_gb < 5:
            warnings.append("Media storage has less than 5 GB free.")

        dataset = dataset_readiness()
        self.stdout.write(f"Dataset: {dataset['counts']}")
        failures.extend(dataset["errors"])

        model = model_readiness()
        self.stdout.write(f"Active validated model: {model['session'] or 'none'}")
        failures.extend(model["errors"])
        warnings.extend(model.get("warnings", []))

        stale = VideoVisualizerAnalysis.objects.filter(
            status=VideoVisualizerStatus.RUNNING,
            lease_expires_at__lt=timezone.now(),
        ).count()
        retrying = VideoVisualizerAnalysis.objects.filter(status=VideoVisualizerStatus.RETRYING).count()
        self.stdout.write(f"Jobs: {stale} stale running, {retrying} waiting to retry")
        if stale:
            warnings.append(f"{stale} analysis job(s) have expired leases and will be reclaimed.")

        if settings.DEBUG:
            warnings.append("DEBUG is enabled.")
        if settings.SECRET_KEY.startswith("django-insecure-"):
            warnings.append("DJANGO_SECRET_KEY is still the development default.")
        db = settings.DATABASES["default"]
        if db["USER"] == "root" or not db["PASSWORD"]:
            warnings.append("MySQL is using root or an empty password; create a restricted application user.")

        for warning in warnings:
            self.stdout.write(self.style.WARNING("WARN: " + warning))
        for failure in failures:
            self.stdout.write(self.style.ERROR("BLOCKED: " + failure))
        if failures and options["strict"]:
            raise CommandError(f"Readiness failed with {len(failures)} blocker(s).")
        if not failures:
            self.stdout.write(self.style.SUCCESS("RoadVision is ready for validated analysis."))
