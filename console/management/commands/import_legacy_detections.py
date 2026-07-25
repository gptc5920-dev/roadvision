import hashlib
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from PIL import Image

from console.models import (
    DatasetImage,
    DatasetImageSource,
    DatasetImageStatus,
    DatasetSplit,
    DetectionEvent,
    PotholeAnnotation,
)


class Command(BaseCommand):
    help = "Import legacy detection snapshots as partial review candidates; never auto-approve labels."

    def handle(self, *args, **options):
        imported = 0
        skipped = 0
        events = DetectionEvent.objects.exclude(snapshot_image="").select_related("analysis")
        for event in events.iterator():
            try:
                event.snapshot_image.open("rb")
                data = event.snapshot_image.read()
            except (FileNotFoundError, OSError):
                skipped += 1
                continue
            finally:
                try:
                    event.snapshot_image.close()
                except Exception:
                    pass
            digest = hashlib.sha256(data).hexdigest()
            if DatasetImage.objects.filter(file_hash=digest).exists():
                skipped += 1
                continue
            with Image.open(event.snapshot_image.storage.open(event.snapshot_image.name, "rb")) as image:
                width, height = image.size
            bucket = int(digest[:8], 16) % 100
            split = DatasetSplit.TRAIN if bucket < 70 else DatasetSplit.VAL if bucket < 90 else DatasetSplit.TEST
            record = DatasetImage(
                original_filename=Path(event.snapshot_image.name).name,
                file_hash=digest,
                file_size=len(data),
                file_type=Path(event.snapshot_image.name).suffix.lstrip(".").lower() or "jpg",
                width=width,
                height=height,
                split=split,
                status=DatasetImageStatus.PARTIAL,
                source=DatasetImageSource.DETECTION,
                source_group=f"legacy-analysis-{event.analysis_id}",
                uploaded_by=event.analysis.created_by,
                review_notes=f"Imported from legacy event {event.event_code}; requires human verification.",
            )
            record.image.save(Path(event.snapshot_image.name).name, ContentFile(data), save=True)
            PotholeAnnotation.objects.create(
                image=record,
                center_x=min(1, (event.bbox_x + event.bbox_w / 2) / 100),
                center_y=min(1, (event.bbox_y + event.bbox_h / 2) / 100),
                width=min(1, event.bbox_w / 100),
                height=min(1, event.bbox_h / 100),
                confidence=event.confidence / 100,
                source=PotholeAnnotation.Source.PREDICTED,
                created_by=event.analysis.created_by,
            )
            imported += 1
        self.stdout.write(self.style.SUCCESS(f"Imported {imported} review candidates; skipped {skipped}."))
