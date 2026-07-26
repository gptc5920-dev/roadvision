import hashlib
import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db.models import Count

from .models import DatasetImage, DatasetImageStatus, DatasetSplit, TrainingSession
from .storage_paths import resolve_model_artifact


@lru_cache(maxsize=32)
def _artifact_sha256(path, modified_ns, size):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def model_artifact_matches(session):
    model_path = resolve_model_artifact(session.model_file)
    if not model_path.is_file() or not session.model_sha256:
        return False
    stat = model_path.stat()
    return _artifact_sha256(str(model_path.resolve()), stat.st_mtime_ns, stat.st_size) == session.model_sha256


def dataset_readiness():
    approved = DatasetImage.objects.filter(
        is_archived=False,
        status=DatasetImageStatus.APPROVED,
    )
    positives = approved.annotate(annotation_count=Count("annotations")).filter(annotation_count__gt=0)
    counts = {split: approved.filter(split=split).count() for split in DatasetSplit.values}
    positive_counts = {split: positives.filter(split=split).count() for split in DatasetSplit.values}
    required = {
        DatasetSplit.TRAIN: settings.DATASET_MIN_TRAIN_IMAGES,
        DatasetSplit.VAL: settings.DATASET_MIN_VAL_IMAGES,
        DatasetSplit.TEST: settings.DATASET_MIN_TEST_IMAGES,
    }
    errors = [
        f"{split} requires {required[split]} approved images; found {counts[split]}"
        for split in DatasetSplit.values
        if counts[split] < required[split]
    ]
    errors.extend(
        f"{split} requires at least one approved pothole image with reviewed masks"
        for split in DatasetSplit.values
        if positive_counts[split] == 0
    )
    invalid_mask_images = [
        image.dataset_id
        for image in positives.prefetch_related("annotations")
        if any(len(annotation.segmentation_points or []) < 3 for annotation in image.annotations.all())
    ]
    if invalid_mask_images:
        errors.append(
            "Approved positive images contain box-only annotations: " + ", ".join(invalid_mask_images[:10])
        )
    grouped_splits = {}
    for source_group, split in approved.exclude(source_group="").values_list("source_group", "split"):
        grouped_splits.setdefault(source_group, set()).add(split)
    leaking_groups = sorted(group for group, splits in grouped_splits.items() if len(splits) > 1)
    if leaking_groups:
        errors.append(
            "Source groups span multiple splits: " + ", ".join(leaking_groups[:10])
        )
    return {
        "ready": not errors,
        "counts": counts,
        "positive_counts": positive_counts,
        "required": required,
        "errors": errors,
    }


def training_dataset_manifest():
    """Freeze the approved dataset membership used by a queued training session."""
    images = list(
        DatasetImage.objects.filter(
            is_archived=False,
            status=DatasetImageStatus.APPROVED,
        )
        .order_by("split", "dataset_id")
        .prefetch_related("annotations")
    )
    manifest = []
    for image in images:
        labels = [annotation.yolo_line for annotation in image.annotations.all()]
        label_payload = json.dumps(labels, separators=(",", ":"), ensure_ascii=True)
        manifest.append(
            {
                "id": image.id,
                "dataset_id": image.dataset_id,
                "file_hash": image.file_hash,
                "split": image.split,
                "source_group": image.source_group or f"image-{image.id}",
                "labels": labels,
                "label_sha256": hashlib.sha256(label_payload.encode("utf-8")).hexdigest(),
                "annotation_count": len(labels),
                "segmentation_annotation_count": sum(
                    len(annotation.segmentation_points or []) >= 3 for annotation in image.annotations.all()
                ),
            }
        )
    return manifest


def model_readiness(session=None):
    session = session or (
        TrainingSession.objects.filter(
            status=TrainingSession.Status.COMPLETE,
            is_validated=True,
            is_active_video_model=True,
        )
        .exclude(model_file="")
        .first()
    )
    errors = []
    warnings = []
    if session is None:
        errors.append("No active validated model is registered.")
    else:
        model_path = resolve_model_artifact(session.model_file)
        if not model_path.is_file():
            errors.append(f"Model artifact is missing: {model_path}")
        if not session.model_sha256:
            errors.append("Model artifact has no SHA-256 provenance hash.")
        elif model_path.is_file() and not model_artifact_matches(session):
            errors.append("Model artifact no longer matches its registered SHA-256 hash.")
        if session.model_task != "segment":
            if settings.ALLOW_DETECTION_MODE and session.model_task == "detect":
                if settings.DETECTION_MASK_REFINEMENT:
                    warnings.append(
                        "Standard detection mode is active with visual-only estimated masks; "
                        "model segmentation and mask-derived measurements still require a segmentation model."
                    )
                else:
                    warnings.append(
                        "Standard detection mode is active; masks and mask-derived measurements require a segmentation model."
                    )
            else:
                errors.append("Active model must be a genuine instance-segmentation model.")
        if session.map50 is None or float(session.map50) < settings.MODEL_MIN_MAP50:
            errors.append(f"Model mAP50 must be at least {settings.MODEL_MIN_MAP50:.2f}.")
        if settings.MODEL_REQUIRE_LOCAL_EVALUATION and session.model_task == "segment":
            if session.local_evaluation_at is None:
                errors.append("Model has not passed a local held-out evaluation.")
            if session.local_test_images < settings.MODEL_MIN_LOCAL_TEST_IMAGES:
                errors.append(
                    f"Local evaluation requires at least {settings.MODEL_MIN_LOCAL_TEST_IMAGES} approved test images."
                )
            if session.local_map50 is None or float(session.local_map50) < settings.MODEL_MIN_MAP50:
                errors.append(f"Local held-out mAP50 must be at least {settings.MODEL_MIN_MAP50:.2f}.")
    return {"ready": not errors, "session": session, "errors": errors, "warnings": warnings}
