import hashlib
import json
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from console.models import (
    AnalyzerConfiguration,
    DatasetImage,
    DatasetImageStatus,
    DatasetSplit,
    TrainingSession,
    VideoTrackReviewStatus,
    VideoVisualizerAnalysis,
    VideoVisualizerStatus,
)
from console.readiness import dataset_readiness, model_artifact_matches, training_dataset_manifest
from console.storage_paths import resolve_model_artifact


def box_iou(left, right):
    lx1, ly1, lx2, ly2 = left
    rx1, ry1, rx2, ry2 = right
    intersection = max(0.0, min(lx2, rx2) - max(lx1, rx1)) * max(0.0, min(ly2, ry2) - max(ly1, ry1))
    union = max(0.0, lx2 - lx1) * max(0.0, ly2 - ly1) + max(0.0, rx2 - rx1) * max(0.0, ry2 - ry1) - intersection
    return intersection / union if union else 0.0


def polygon_iou(left, right, width, height):
    import cv2
    import numpy as np

    if len(left) < 3 or len(right) < 3:
        return 0.0
    left_mask = np.zeros((height, width), dtype=np.uint8)
    right_mask = np.zeros((height, width), dtype=np.uint8)
    left_points = np.asarray(
        [[round(float(x) * width), round(float(y) * height)] for x, y in left], dtype=np.int32
    )
    right_points = np.asarray(
        [[round(float(x) * width), round(float(y) * height)] for x, y in right], dtype=np.int32
    )
    cv2.fillPoly(left_mask, [left_points], 1)
    cv2.fillPoly(right_mask, [right_points], 1)
    intersection = int((left_mask & right_mask).sum())
    union = int((left_mask | right_mask).sum())
    return intersection / union if union else 0.0


def average_precision(tp_flags, fp_flags, total_ground_truth):
    if total_ground_truth <= 0:
        return None
    cumulative_tp = 0
    cumulative_fp = 0
    recalls = []
    precisions = []
    for tp, fp in zip(tp_flags, fp_flags):
        cumulative_tp += tp
        cumulative_fp += fp
        recalls.append(cumulative_tp / total_ground_truth)
        precisions.append(cumulative_tp / max(cumulative_tp + cumulative_fp, 1))
    return sum(
        max((precision for recall, precision in zip(recalls, precisions) if recall >= level), default=0.0)
        for level in [index / 100 for index in range(101)]
    ) / 101


def score_predictions(predictions, ground_truth, iou_threshold, iou_function):
    matched = defaultdict(set)
    tp_flags = []
    fp_flags = []
    for prediction in sorted(predictions, key=lambda item: item["confidence"], reverse=True):
        candidates = ground_truth.get(prediction["image_id"], [])
        best_index = None
        best_iou = 0.0
        for index, target in enumerate(candidates):
            if index in matched[prediction["image_id"]]:
                continue
            score = iou_function(prediction, target)
            if score > best_iou:
                best_iou = score
                best_index = index
        is_match = best_index is not None and best_iou >= iou_threshold
        if is_match:
            matched[prediction["image_id"]].add(best_index)
        tp_flags.append(1 if is_match else 0)
        fp_flags.append(0 if is_match else 1)
    total_ground_truth = sum(len(items) for items in ground_truth.values())
    return average_precision(tp_flags, fp_flags, total_ground_truth), tp_flags, fp_flags


def decimal_metric(value):
    return Decimal(str(round(float(value), 4))) if value is not None else None


class Command(BaseCommand):
    help = "Evaluate a segmentation model on the approved local test split and optionally activate it."

    def add_arguments(self, parser):
        parser.add_argument("--session-id", type=int, help="Training session to evaluate; defaults to the newest complete model.")
        parser.add_argument("--threshold", type=float, default=0.30)
        parser.add_argument("--iou", type=float, default=0.45)
        parser.add_argument("--image-size", type=int, default=512)
        parser.add_argument("--device", default="auto")
        parser.add_argument("--activate", action="store_true")

    def handle(self, *args, **options):
        try:
            import torch
            from PIL import Image
            from ultralytics import YOLO
        except Exception as exc:
            raise CommandError("Ultralytics, Torch, OpenCV, and Pillow are required for evaluation.") from exc

        sessions = TrainingSession.objects.filter(status=TrainingSession.Status.COMPLETE).exclude(model_file="")
        session = sessions.filter(pk=options.get("session_id")).first() if options.get("session_id") else sessions.first()
        if session is None:
            raise CommandError("No completed model session is available.")
        if not model_artifact_matches(session):
            raise CommandError("The model artifact is missing or does not match its registered SHA-256 hash.")
        readiness = dataset_readiness()
        if not readiness["ready"]:
            raise CommandError("Local evaluation dataset is not ready: " + "; ".join(readiness["errors"]))
        model = YOLO(str(resolve_model_artifact(session.model_file)))
        if model.task != "segment":
            raise CommandError(f"Session {session.pk} is task {model.task!r}; local evaluation requires segmentation.")

        test_images = list(
            DatasetImage.objects.filter(
                is_archived=False,
                status=DatasetImageStatus.APPROVED,
                split=DatasetSplit.TEST,
            ).prefetch_related("annotations").order_by("source_group", "dataset_id")
        )
        if len(test_images) < settings.MODEL_MIN_LOCAL_TEST_IMAGES:
            raise CommandError(
                f"Local evaluation requires {settings.MODEL_MIN_LOCAL_TEST_IMAGES} approved test images; found {len(test_images)}."
            )

        ground_truth = defaultdict(list)
        for image in test_images:
            for annotation in image.annotations.all():
                polygon = annotation.segmentation_points or []
                if len(polygon) < 3:
                    raise CommandError(f"Approved test image {image.dataset_id} has a non-segmentation annotation.")
                cx, cy = float(annotation.center_x), float(annotation.center_y)
                box_width, box_height = float(annotation.width), float(annotation.height)
                ground_truth[image.id].append(
                    {
                        "box": [cx - box_width / 2, cy - box_height / 2, cx + box_width / 2, cy + box_height / 2],
                        "polygon": polygon,
                        "width": image.width,
                        "height": image.height,
                    }
                )

        device = options["device"]
        if device == "auto":
            device = "0" if torch.cuda.is_available() else "cpu"
        predictions = []
        ignored_classes = defaultdict(int)
        for image in test_images:
            image.image.open("rb")
            try:
                with Image.open(image.image) as opened:
                    source = opened.convert("RGB").copy()
            finally:
                image.image.close()
            result = model.predict(
                source=source,
                conf=0.001,
                iou=options["iou"],
                imgsz=options["image_size"],
                device=device,
                verbose=False,
                save=False,
            )[0]
            if result.boxes is None:
                continue
            mask_polygons = result.masks.xyn if result.masks is not None else []
            for index, (raw_box, confidence, class_id) in enumerate(
                zip(result.boxes.xyxy.cpu().tolist(), result.boxes.conf.cpu().tolist(), result.boxes.cls.int().cpu().tolist())
            ):
                class_name = str(result.names.get(class_id, class_id)).lower().replace(" ", "_")
                if class_name != "pothole":
                    ignored_classes[class_name] += 1
                    continue
                if index >= len(mask_polygons) or len(mask_polygons[index]) < 3:
                    continue
                predictions.append(
                    {
                        "image_id": image.id,
                        "confidence": float(confidence),
                        "box": [
                            float(raw_box[0]) / image.width,
                            float(raw_box[1]) / image.height,
                            float(raw_box[2]) / image.width,
                            float(raw_box[3]) / image.height,
                        ],
                        "polygon": [[float(x), float(y)] for x, y in mask_polygons[index]],
                        "width": image.width,
                        "height": image.height,
                    }
                )

        mask_aps = []
        box_aps = []
        operating = None
        for threshold in [0.50 + index * 0.05 for index in range(10)]:
            mask_ap, mask_tp, mask_fp = score_predictions(
                predictions,
                ground_truth,
                threshold,
                lambda prediction, target: polygon_iou(
                    prediction["polygon"], target["polygon"], target["width"], target["height"]
                ),
            )
            box_ap, _, _ = score_predictions(
                predictions,
                ground_truth,
                threshold,
                lambda prediction, target: box_iou(prediction["box"], target["box"]),
            )
            mask_aps.append(mask_ap or 0.0)
            box_aps.append(box_ap or 0.0)
            if threshold == 0.50:
                operating_predictions = [item for item in predictions if item["confidence"] >= options["threshold"]]
                _, mask_tp, mask_fp = score_predictions(
                    operating_predictions,
                    ground_truth,
                    0.50,
                    lambda prediction, target: polygon_iou(
                        prediction["polygon"], target["polygon"], target["width"], target["height"]
                    ),
                )
                true_positives = sum(mask_tp)
                false_positives = sum(mask_fp)
                false_negatives = sum(len(items) for items in ground_truth.values()) - true_positives
                precision = true_positives / max(true_positives + false_positives, 1)
                recall = true_positives / max(true_positives + false_negatives, 1)
                operating = {
                    "threshold": options["threshold"],
                    "true_positives": true_positives,
                    "false_positives": false_positives,
                    "false_negatives": false_negatives,
                    "precision": precision,
                    "recall": recall,
                    "f1": (2 * precision * recall / (precision + recall)) if precision + recall else 0.0,
                }

        scored_videos = []
        for analysis in VideoVisualizerAnalysis.objects.filter(
            model_session=session,
            status=VideoVisualizerStatus.COMPLETE,
            ground_truth_pothole_count__isnull=False,
        ).prefetch_related("tracks"):
            pothole_tracks = [track for track in analysis.tracks.all() if track.label.lower() == "pothole"]
            if any(track.review_status == VideoTrackReviewStatus.UNRESOLVED for track in pothole_tracks):
                continue
            confirmed = sum(track.review_status == VideoTrackReviewStatus.CONFIRMED for track in pothole_tracks)
            rejected = sum(track.review_status == VideoTrackReviewStatus.REJECTED for track in pothole_tracks)
            truth = analysis.ground_truth_pothole_count
            scored_videos.append(
                {
                    "analysis_id": analysis.id,
                    "ground_truth": truth,
                    "confirmed": confirmed,
                    "rejected": rejected,
                    "count_recall_proxy": min(confirmed, truth) / truth if truth else (1.0 if confirmed == 0 else 0.0),
                    "count_absolute_error": abs(confirmed - truth),
                    "duplicate_tracks_merged": analysis.duplicate_tracks_merged,
                    "raw_track_count": analysis.raw_track_count,
                }
            )
        video_metrics = {
            "scored_videos": len(scored_videos),
            "count_recall_proxy": (
                sum(item["count_recall_proxy"] for item in scored_videos) / len(scored_videos)
                if scored_videos else None
            ),
            "count_mae": sum(item["count_absolute_error"] for item in scored_videos) / len(scored_videos) if scored_videos else None,
            "duplicate_rate": (
                sum(item["duplicate_tracks_merged"] for item in scored_videos)
                / max(sum(item["raw_track_count"] for item in scored_videos), 1)
                if scored_videos else None
            ),
            "analyses": scored_videos,
        }
        manifest = [entry for entry in training_dataset_manifest() if entry["split"] == DatasetSplit.TEST]
        metrics = {
            "source": "approved-local-held-out-test",
            "evaluated_at": timezone.now().isoformat(),
            "model_sha256": session.model_sha256,
            "image_size": options["image_size"],
            "device": device,
            "images": len(test_images),
            "positive_images": sum(bool(ground_truth[image.id]) for image in test_images),
            "negative_images": sum(not ground_truth[image.id] for image in test_images),
            "manifest_sha256": hashlib.sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "operating_point": operating,
            "per_class": {
                "pothole": {
                    "mask_map50": mask_aps[0],
                    "mask_map5095": sum(mask_aps) / len(mask_aps),
                    "box_map50": box_aps[0],
                    "box_map5095": sum(box_aps) / len(box_aps),
                    **operating,
                }
            },
            "ignored_prediction_classes": dict(ignored_classes),
            "video": video_metrics,
        }
        session.model_task = "segment"
        session.local_evaluation_at = timezone.now()
        session.local_test_images = len(test_images)
        session.local_precision = decimal_metric(operating["precision"])
        session.local_recall = decimal_metric(operating["recall"])
        session.local_map50 = decimal_metric(mask_aps[0])
        session.local_map5095 = decimal_metric(sum(mask_aps) / len(mask_aps))
        session.local_metrics = metrics
        session.save(
            update_fields=[
                "model_task", "local_evaluation_at", "local_test_images", "local_precision",
                "local_recall", "local_map50", "local_map5095", "local_metrics",
            ]
        )
        passed = float(session.local_map50 or 0) >= settings.MODEL_MIN_MAP50
        if options["activate"]:
            if not session.is_validated or float(session.map50 or 0) < settings.MODEL_MIN_MAP50:
                raise CommandError("The model has not passed its declared or training validation gate.")
            if not passed:
                raise CommandError(
                    f"Local evaluation failed activation gate: mAP50={session.local_map50}, required={settings.MODEL_MIN_MAP50:.2f}."
                )
            with transaction.atomic():
                TrainingSession.objects.update(is_active_video_model=False)
                session.is_active_video_model = True
                session.save(update_fields=["is_active_video_model"])
                AnalyzerConfiguration.objects.update_or_create(pk=1, defaults={"model_session": session})
        self.stdout.write(json.dumps(metrics, indent=2, default=str))
        self.stdout.write(
            self.style.SUCCESS(
                f"Session {session.pk}: local mask mAP50={session.local_map50}, "
                f"mAP50-95={session.local_map5095}, precision={session.local_precision}, recall={session.local_recall}."
            )
        )
