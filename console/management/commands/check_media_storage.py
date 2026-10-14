import json
import os
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from console.models import DatasetImage, DetectionTest, VideoPotholeTrack, VideoVisualizerAnalysis


class Command(BaseCommand):
    help = "Inspect media storage and optionally verify a write/read/delete round trip. No credentials are printed."

    def add_arguments(self, parser):
        parser.add_argument("--probe", action="store_true", help="Write, read, and remove one temporary diagnostic object.")
        parser.add_argument("--limit", type=int, default=25, help="Recent records checked per media model.")

    def handle(self, *args, **options):
        root = Path(settings.MEDIA_ROOT).resolve()
        mounted = any(os.path.ismount(path) for path in [root, *root.parents] if path.parent != path)
        report = {
            "backend": default_storage.__class__.__name__,
            "s3_configured": bool(settings.AWS_STORAGE_BUCKET_NAME),
            "media_root": str(root), "local_persistent_mount_detected": mounted,
            "files_checked": 0, "missing_files": [], "errors": [],
        }
        if not settings.AWS_STORAGE_BUCKET_NAME and not mounted:
            report["persistence_note"] = "In Coolify, mount a persistent volume at MEDIA_ROOT before redeploying. A local directory alone does not preserve uploads."
        for model, fields in [
            (DatasetImage, ["image", "labeled_preview"]),
            (DetectionTest, ["image", "result_image"]),
            (VideoVisualizerAnalysis, ["video", "processed_video", "live_preview_frame", "frame_detections_artifact"]),
            (VideoPotholeTrack, ["snapshot_frame", "snapshot_crop"]),
        ]:
            for record in model.objects.order_by("-pk")[:max(1, min(options["limit"], 1000))]:
                for field in fields:
                    file = getattr(record, field)
                    if not file:
                        continue
                    report["files_checked"] += 1
                    try:
                        if not file.storage.exists(file.name):
                            report["missing_files"].append({"model": model.__name__, "id": record.pk, "field": field})
                    except Exception as exc:
                        report["errors"].append(f"Cannot inspect {model.__name__} #{record.pk} {field}: {type(exc).__name__}")
        if options["probe"]:
            name = None
            try:
                content = b"RoadVision media storage round-trip check"
                name = default_storage.save(f"_diagnostics/storage/{uuid.uuid4().hex}.txt", ContentFile(content))
                with default_storage.open(name, "rb") as stored:
                    if stored.read() != content:
                        raise ValueError("Storage returned different data")
                report["probe"] = "passed"
            except Exception as exc:
                report["probe"] = "failed"
                report["errors"].append(f"Storage write/read failed: {type(exc).__name__}")
            finally:
                if name:
                    try:
                        default_storage.delete(name)
                    except Exception as exc:
                        report["errors"].append(f"Temporary probe cleanup failed: {type(exc).__name__}")
        self.stdout.write(json.dumps(report, indent=2))
        if report["errors"] or report["missing_files"]:
            raise CommandError("Media storage checks failed; inspect the report above.")
