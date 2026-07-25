import hashlib
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from console.models import AnalyzerConfiguration, TrainingSession


class Command(BaseCommand):
    help = "Register an externally validated pothole YOLO model with provenance metadata."

    def add_arguments(self, parser):
        parser.add_argument("model_path")
        parser.add_argument("--name", default="external-pothole")
        parser.add_argument("--map50", type=float, required=True)
        parser.add_argument("--map5095", type=float)
        parser.add_argument("--validated-by", required=True)
        parser.add_argument("--activate", action="store_true")

    def handle(self, *args, **options):
        source = Path(options["model_path"]).resolve()
        if source.suffix.lower() != ".pt" or not source.is_file():
            raise CommandError("Model must be an existing .pt file.")
        if options["map50"] < settings.MODEL_MIN_MAP50:
            raise CommandError(f"mAP50 must be at least {settings.MODEL_MIN_MAP50:.2f}.")
        try:
            from ultralytics import YOLO

            model = YOLO(str(source))
        except Exception as exc:
            raise CommandError(f"Ultralytics could not load this model: {exc}") from exc
        class_names = {int(key): str(value) for key, value in model.names.items()}
        if model.task not in {"segment", "detect"}:
            raise CommandError(
                f"Model task is {model.task!r}; RoadVision supports detection and segmentation models."
            )
        if model.task == "detect" and not settings.ALLOW_DETECTION_MODE:
            raise CommandError("Detection models are disabled. Set ALLOW_DETECTION_MODE=true to register this task.")
        if not any("pothole" in name.lower() for name in class_names.values()):
            raise CommandError(f"Model does not declare a pothole class: {class_names}")

        hasher = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        destination_dir = Path(settings.BASE_DIR) / "models" / "registered"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{digest[:12]}-{source.name}"
        if not destination.exists():
            shutil.copyfile(source, destination)

        if options["activate"] and settings.MODEL_REQUIRE_LOCAL_EVALUATION and model.task == "segment":
            raise CommandError(
                "Register the model without --activate, run evaluate_pothole_model on the local holdout, then activate it."
            )
        if options["activate"]:
            TrainingSession.objects.update(is_active_video_model=False)
        session = TrainingSession.objects.create(
            model_name=options["name"][:20],
            status=TrainingSession.Status.COMPLETE,
            progress=100,
            precision=None,
            recall=None,
            map50=options["map50"],
            map5095=options.get("map5095"),
            metrics={"source": "external", "validated_by": options["validated_by"], "class_names": class_names},
            model_file=str(destination),
            model_task=model.task,
            model_sha256=digest,
            is_validated=True,
            is_active_video_model=options["activate"],
            validation_notes=f"External validation declared by {options['validated_by']}.",
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        if options["activate"]:
            AnalyzerConfiguration.objects.update_or_create(pk=1, defaults={"model_session": session})
        self.stdout.write(self.style.SUCCESS(f"Registered model session {session.pk}: {destination}"))
