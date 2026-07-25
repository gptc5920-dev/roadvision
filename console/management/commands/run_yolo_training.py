import csv
import hashlib
import shutil
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from console.models import AnalyzerConfiguration, DatasetImage, DatasetSplit, TrainingSession
from console.readiness import dataset_readiness, training_dataset_manifest


AUGMENTATION_PROFILES = {
    "conservative": {
        "hsv_h": 0.008,
        "hsv_s": 0.35,
        "hsv_v": 0.25,
        "degrees": 3.0,
        "translate": 0.05,
        "scale": 0.20,
        "shear": 1.0,
        "perspective": 0.0002,
        "fliplr": 0.25,
        "flipud": 0.0,
        "mosaic": 0.35,
        "mixup": 0.0,
    },
    "balanced": {
        "hsv_h": 0.012,
        "hsv_s": 0.50,
        "hsv_v": 0.35,
        "degrees": 6.0,
        "translate": 0.08,
        "scale": 0.35,
        "shear": 2.0,
        "perspective": 0.0005,
        "fliplr": 0.50,
        "flipud": 0.0,
        "mosaic": 0.70,
        "mixup": 0.05,
    },
    "aggressive": {
        "hsv_h": 0.015,
        "hsv_s": 0.65,
        "hsv_v": 0.45,
        "degrees": 10.0,
        "translate": 0.12,
        "scale": 0.50,
        "shear": 3.0,
        "perspective": 0.001,
        "fliplr": 0.50,
        "flipud": 0.0,
        "mosaic": 1.0,
        "mixup": 0.10,
    },
}


def augmentation_options(profile):
    return dict(AUGMENTATION_PROFILES.get(profile, AUGMENTATION_PROFILES["balanced"]))


def metrics_from_validator(metrics):
    results = getattr(metrics, "results_dict", {}) or {}

    def pick(*names):
        for name in names:
            value = results.get(name)
            if value is not None:
                return float(value)
        return None

    return {
        "precision": pick("metrics/precision(M)", "metrics/precision(B)", "metrics/precision"),
        "recall": pick("metrics/recall(M)", "metrics/recall(B)", "metrics/recall"),
        "map50": pick("metrics/mAP50(M)", "metrics/mAP50(B)", "metrics/mAP50"),
        "map5095": pick("metrics/mAP50-95(M)", "metrics/mAP50-95(B)", "metrics/mAP50-95"),
    }


def session_images(session):
    manifest = list(session.dataset_manifest or [])
    if not manifest:
        manifest = training_dataset_manifest()
        session.dataset_manifest = manifest
        session.save(update_fields=["dataset_manifest"])
    ids = [entry["id"] for entry in manifest]
    records = {
        image.id: image
        for image in DatasetImage.objects.filter(id__in=ids).prefetch_related("annotations")
    }
    missing = [entry["dataset_id"] for entry in manifest if entry["id"] not in records]
    if missing:
        raise CommandError("Training snapshot is missing dataset images: " + ", ".join(missing[:10]))
    ordered = []
    for entry in manifest:
        image = records[entry["id"]]
        if (
            image.file_hash != entry["file_hash"]
            or image.split != entry["split"]
            or (entry.get("source_group") and (image.source_group or f"image-{image.id}") != entry["source_group"])
        ):
            raise CommandError(f"Training snapshot changed after queueing: {entry['dataset_id']}")
        ordered.append(image)
    return ordered


def validate_session_images(images, manifest_by_id=None):
    required = {
        DatasetSplit.TRAIN: settings.DATASET_MIN_TRAIN_IMAGES,
        DatasetSplit.VAL: settings.DATASET_MIN_VAL_IMAGES,
        DatasetSplit.TEST: settings.DATASET_MIN_TEST_IMAGES,
    }
    counts = {split: sum(1 for image in images if image.split == split) for split in DatasetSplit.values}
    manifest_by_id = manifest_by_id or {}
    positive_counts = {
        split: sum(
            1
            for image in images
            if image.split == split
            and (manifest_by_id.get(image.id, {}).get("labels") or (not manifest_by_id and image.annotations.all()))
        )
        for split in DatasetSplit.values
    }
    errors = [
        f"{split} requires {required[split]} approved images; snapshot has {counts[split]}"
        for split in DatasetSplit.values
        if counts[split] < required[split]
    ]
    errors.extend(
        f"{split} requires at least one approved pothole image with reviewed masks"
        for split in DatasetSplit.values
        if positive_counts[split] == 0
    )
    invalid_masks = []
    for image in images:
        entry = manifest_by_id.get(image.id, {}) if manifest_by_id else {}
        if "segmentation_annotation_count" in entry:
            if entry.get("segmentation_annotation_count") != entry.get("annotation_count"):
                invalid_masks.append(image.dataset_id)
        elif any(len(annotation.segmentation_points or []) < 3 for annotation in image.annotations.all()):
            invalid_masks.append(image.dataset_id)
    if invalid_masks:
        errors.append("snapshot contains box-only annotations: " + ", ".join(invalid_masks[:10]))
    if errors:
        raise CommandError("Dataset snapshot readiness gate failed: " + "; ".join(errors))


def write_dataset_for_session(session):
    root = Path(settings.MEDIA_ROOT) / "training_dataset" / "yolo_exports" / f"session_{session.pk}"
    dataset_root = root / "dataset"
    if root.exists():
        shutil.rmtree(root)
    for split in ["train", "val", "test"]:
        (dataset_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (dataset_root / "labels" / split).mkdir(parents=True, exist_ok=True)

    images = session_images(session)
    if not images:
        raise CommandError("No approved dataset images are available for training.")
    manifest_by_id = {entry["id"]: entry for entry in (session.dataset_manifest or [])}
    validate_session_images(images, manifest_by_id)

    for image in images:
        extension = Path(image.original_filename).suffix.lower() or ".jpg"
        image_name = f"{image.dataset_id}{extension}"
        target_image = dataset_root / "images" / image.split / image_name
        target_label = dataset_root / "labels" / image.split / f"{image.dataset_id}.txt"
        hasher = hashlib.sha256()
        image.image.open("rb")
        try:
            with target_image.open("wb") as target:
                for chunk in iter(lambda: image.image.read(1024 * 1024), b""):
                    hasher.update(chunk)
                    target.write(chunk)
        finally:
            image.image.close()
        if hasher.hexdigest() != image.file_hash:
            raise CommandError(f"Dataset image content no longer matches its recorded hash: {image.dataset_id}")
        label_lines = manifest_by_id.get(image.id, {}).get("labels")
        if label_lines is None:
            label_lines = [annotation.yolo_line for annotation in image.annotations.all()]
        target_label.write_text("\n".join(label_lines) + ("\n" if label_lines else ""), encoding="utf-8")

    (dataset_root / "data.yaml").write_text(
        "\n".join(
            [
                f"path: {dataset_root.as_posix()}",
                "train: images/train",
                "val: images/val",
                "test: images/test",
                "names:",
                "  0: pothole",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root, dataset_root / "data.yaml"


def read_latest_metrics(results_csv):
    if not results_csv.exists():
        return {}
    with results_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return {}
    row = rows[-1]

    def pick(*names):
        for name in names:
            value = row.get(name)
            if value not in (None, ""):
                return value.strip()
        return None

    return {
        "train_loss": pick("train/box_loss"),
        "val_loss": pick("val/box_loss"),
        "precision": pick("metrics/precision(B)", "metrics/precision"),
        "recall": pick("metrics/recall(B)", "metrics/recall"),
        "map50": pick("metrics/mAP50(B)", "metrics/mAP50"),
        "map5095": pick("metrics/mAP50-95(B)", "metrics/mAP50-95"),
    }


class Command(BaseCommand):
    help = "Run queued YOLO11 pothole training sessions with Ultralytics."

    def add_arguments(self, parser):
        parser.add_argument("--session-id", type=int, help="Run one training session by ID.")
        parser.add_argument("--watch", action="store_true", help="Continuously process automatically queued sessions.")
        parser.add_argument("--poll-interval", type=float, default=5.0)

    def handle(self, *args, **options):
        try:
            from ultralytics import YOLO
        except Exception as exc:
            raise CommandError("Ultralytics is not installed. Run pip install -r requirements.txt.") from exc

        while True:
            session = self.claim_next(options.get("session_id"))
            if session is None:
                if options["watch"] and not options.get("session_id"):
                    time.sleep(max(options["poll_interval"], 0.5))
                    continue
                self.stdout.write("No queued training sessions found.")
                return
            self.process_session(session, YOLO)
            if options.get("session_id"):
                return

    def claim_next(self, session_id=None):
        with transaction.atomic():
            sessions = TrainingSession.objects.select_for_update().filter(status=TrainingSession.Status.QUEUED)
            if session_id:
                sessions = sessions.filter(pk=session_id)
            session = sessions.order_by("created_at").first()
            if session is None:
                return None
            session.status = TrainingSession.Status.RUNNING
            session.started_at = timezone.now()
            session.progress = 1
            session.error_message = ""
            session.save(update_fields=["status", "started_at", "progress", "error_message"])
            return session

    def process_session(self, session, YOLO):
        self.stdout.write(f"Running training session {session.pk} ({session.model_name})")
        try:
            training_device = session.device
            if str(training_device).lower() == "auto":
                import torch

                training_device = "0" if torch.cuda.is_available() else "cpu"
            if not session.dataset_manifest:
                readiness = dataset_readiness()
                if not readiness["ready"]:
                    raise CommandError("Dataset readiness gate failed: " + "; ".join(readiness["errors"]))
            export_root, data_yaml = write_dataset_for_session(session)
            model = YOLO(f"{session.model_name}.pt")
            if model.task != "segment":
                raise CommandError("Training architecture must be an instance-segmentation model.")

            def on_epoch_end(trainer):
                session.current_epoch = int(getattr(trainer, "epoch", 0)) + 1
                session.progress = min(99, int((session.current_epoch / max(session.epochs, 1)) * 100))
                session.save(update_fields=["current_epoch", "progress"])

            model.add_callback("on_train_epoch_end", on_epoch_end)
            results = model.train(
                data=str(data_yaml),
                epochs=session.epochs,
                batch=session.batch_size,
                imgsz=session.image_size,
                lr0=float(session.learning_rate),
                device=training_device,
                patience=session.patience,
                workers=session.workers,
                optimizer=session.optimizer,
                seed=session.seed,
                deterministic=True,
                freeze=session.freeze_layers or None,
                cos_lr=True,
                close_mosaic=min(10, max(0, session.epochs // 10)),
                amp=True,
                plots=True,
                **augmentation_options(session.augmentation_profile),
                project=str(export_root / "runs"),
                name="train",
                exist_ok=True,
            )
            save_dir = Path(getattr(results, "save_dir", export_root / "runs" / "train"))
            metrics = read_latest_metrics(save_dir / "results.csv")
            for field, value in metrics.items():
                if value is not None:
                    setattr(session, field, value)
            best_model = save_dir / "weights" / "best.pt"
            session.model_file = str(best_model) if best_model.exists() else ""
            if not best_model.exists():
                raise CommandError("Training completed without producing best.pt.")
            hasher = hashlib.sha256()
            with best_model.open("rb") as model_handle:
                for chunk in iter(lambda: model_handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
            session.model_sha256 = hasher.hexdigest()
            session.results_dir = str(save_dir)
            best_model_instance = YOLO(str(best_model))
            if best_model_instance.task != "segment":
                raise CommandError("Trained artifact is not an instance-segmentation model.")
            test_results = best_model_instance.val(
                data=str(data_yaml),
                split="test",
                imgsz=session.image_size,
                batch=session.batch_size,
                device=training_device,
                workers=session.workers,
                plots=True,
                project=str(export_root / "runs"),
                name="test",
                exist_ok=True,
            )
            test_metrics = metrics_from_validator(test_results)
            session.precision = test_metrics["precision"]
            session.recall = test_metrics["recall"]
            session.map50 = test_metrics["map50"]
            session.map5095 = test_metrics["map5095"]
            session.model_task = "segment"
            session.local_evaluation_at = timezone.now()
            session.local_test_images = sum(
                1 for entry in (session.dataset_manifest or []) if entry.get("split") == DatasetSplit.TEST
            )
            session.local_precision = test_metrics["precision"]
            session.local_recall = test_metrics["recall"]
            session.local_map50 = test_metrics["map50"]
            session.local_map5095 = test_metrics["map5095"]
            session.local_metrics = {
                "source": "held-out-local-test",
                "per_class": {"pothole": test_metrics},
                "test_images": session.local_test_images,
            }
            session.metrics = {
                "validation": metrics,
                "held_out_test": test_metrics,
                "augmentation_profile": session.augmentation_profile,
                "optimizer": session.optimizer,
                "seed": session.seed,
                "freeze_layers": session.freeze_layers,
                "dataset_image_count": len(session.dataset_manifest),
                "model_task": session.model_task,
                "device": training_device,
            }
            map50 = float(test_metrics.get("map50") or 0)
            session.is_validated = map50 >= settings.MODEL_MIN_MAP50
            session.validation_notes = (
                f"Held-out test gate passed: mAP50={map50:.4f}."
                if session.is_validated
                else f"Held-out test gate failed: mAP50={map50:.4f}, required={settings.MODEL_MIN_MAP50:.4f}."
            )
            session.status = TrainingSession.Status.COMPLETE
            session.progress = 100
            session.current_epoch = session.epochs
            session.finished_at = timezone.now()
            session.save()
            if session.is_validated:
                TrainingSession.objects.exclude(pk=session.pk).update(is_active_video_model=False)
                session.is_active_video_model = True
                session.save(update_fields=["is_active_video_model"])
                AnalyzerConfiguration.objects.update_or_create(pk=1, defaults={"model_session": session})
            self.stdout.write(self.style.SUCCESS(f"Training session {session.pk} complete."))
        except Exception as exc:
            session.status = TrainingSession.Status.FAILED
            session.error_message = str(exc)
            session.finished_at = timezone.now()
            session.save(update_fields=["status", "error_message", "finished_at"])
            self.stderr.write(self.style.ERROR(f"Training session {session.pk} failed: {exc}"))
