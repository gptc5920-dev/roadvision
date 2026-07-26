import hashlib
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from console.models import AnalyzerConfiguration, TrainingSession
from console.storage_paths import portable_model_artifact_value


BUNDLED_MODEL_RELATIVE_PATH = Path(
    "models/registered/f380cd373f61-potholenet-yolo11m-v1.pt"
)
EXPECTED_SHA256 = "f380cd373f61f2bc71f7fcc1b0ec072194dc2cd933fd05bc1ae5ad136a333b78"
VALIDATED_BY = "Published PotholeNet-V1 model card; 23,179-image external street dataset"


def artifact_sha256(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def inspect_model(path):
    from ultralytics import YOLO

    model = YOLO(str(path))
    return model.task, {int(key): str(value) for key, value in model.names.items()}


class Command(BaseCommand):
    help = "Idempotently register and activate RoadVision's bundled validated detection model."

    def handle(self, *args, **options):
        if not settings.ALLOW_DETECTION_MODE:
            self.stdout.write(
                self.style.WARNING(
                    "Bundled model registration skipped because ALLOW_DETECTION_MODE is false."
                )
            )
            return

        source = Path(settings.BASE_DIR) / BUNDLED_MODEL_RELATIVE_PATH
        if not source.is_file():
            raise CommandError(f"Bundled model artifact is missing: {source}")

        digest = artifact_sha256(source)
        if digest != EXPECTED_SHA256:
            raise CommandError(
                "Bundled model artifact failed its SHA-256 provenance check."
            )

        session = (
            TrainingSession.objects.filter(
                model_sha256=digest,
                status=TrainingSession.Status.COMPLETE,
                is_validated=True,
            )
            .order_by("-created_at")
            .first()
        )

        if session is None:
            try:
                model_task, class_names = inspect_model(source)
            except Exception as exc:
                raise CommandError(
                    f"Ultralytics could not load the bundled model: {exc}"
                ) from exc
            if model_task != "detect":
                raise CommandError(
                    f"Bundled model task is {model_task!r}; expected 'detect'."
                )
            if not any("pothole" in name.lower() for name in class_names.values()):
                raise CommandError(
                    f"Bundled model does not declare a pothole class: {class_names}"
                )

            now = timezone.now()
            session = TrainingSession.objects.create(
                model_name="potholenet-v1",
                status=TrainingSession.Status.COMPLETE,
                progress=100,
                map50=Decimal("0.8600"),
                metrics={
                    "source": "bundled-external",
                    "validated_by": VALIDATED_BY,
                    "class_names": class_names,
                },
                model_file=portable_model_artifact_value(source),
                model_task=model_task,
                model_sha256=digest,
                is_validated=True,
                validation_notes=f"External validation declared by {VALIDATED_BY}.",
                started_at=now,
                finished_at=now,
            )

        with transaction.atomic():
            TrainingSession.objects.exclude(pk=session.pk).update(
                is_active_video_model=False
            )
            if not session.is_active_video_model:
                session.is_active_video_model = True
                session.save(update_fields=["is_active_video_model"])
            AnalyzerConfiguration.objects.update_or_create(
                pk=1,
                defaults={"model_session": session},
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Bundled model session {session.pk} is registered and active."
            )
        )
