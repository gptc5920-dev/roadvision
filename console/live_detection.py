import threading
import time
from io import BytesIO

from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

from .readiness import model_readiness
from .storage_paths import resolve_model_artifact


class LiveDetectionError(RuntimeError):
    pass


class LiveDetectionBusy(LiveDetectionError):
    pass


_model_lock = threading.Lock()
_inference_lock = threading.Lock()
_cached_model = None
_cached_model_key = None


def _active_model(configuration):
    global _cached_model, _cached_model_key

    readiness = model_readiness(configuration.model_session)
    if not readiness["ready"]:
        raise LiveDetectionError("Live detection is blocked: " + " ".join(readiness["errors"]))
    session = readiness["session"]
    if session is None:
        raise LiveDetectionError("Live detection is blocked: No active validated model is registered.")
    model_path = resolve_model_artifact(session.model_file)
    if not model_path.is_file():
        raise LiveDetectionError("The active model file is unavailable.")
    cache_key = (str(model_path), session.model_sha256 or "", model_path.stat().st_mtime_ns)

    with _model_lock:
        if _cached_model is not None and _cached_model_key == cache_key:
            return _cached_model
        try:
            from ultralytics import YOLO
        except Exception as exc:
            raise LiveDetectionError("Ultralytics is unavailable for live detection.") from exc
        _cached_model = YOLO(str(model_path))
        _cached_model_key = cache_key
        return _cached_model


def _decode_frame(frame_bytes):
    try:
        with Image.open(BytesIO(frame_bytes)) as opened:
            if opened.width * opened.height > 20_000_000:
                raise LiveDetectionError("The live camera frame has too many pixels.")
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except LiveDetectionError:
        raise
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError) as exc:
        raise LiveDetectionError("The camera frame could not be decoded.") from exc

    width, height = image.size
    if width < 160 or height < 120:
        raise LiveDetectionError("The live camera frame is too small.")
    if width > settings.LIVE_DETECTION_MAX_DIMENSION or height > settings.LIVE_DETECTION_MAX_DIMENSION:
        image.thumbnail(
            (settings.LIVE_DETECTION_MAX_DIMENSION, settings.LIVE_DETECTION_MAX_DIMENSION),
            Image.Resampling.LANCZOS,
        )
    return image


def detect_live_frame(frame_bytes, configuration, confidence_threshold):
    image = _decode_frame(frame_bytes)
    model = _active_model(configuration)
    model_task = getattr(model, "task", None)
    if model_task not in {"segment", "detect"}:
        raise LiveDetectionError("The active model does not support live detection.")
    if model_task == "detect" and not settings.ALLOW_DETECTION_MODE:
        raise LiveDetectionError("Object-detection models are disabled for live analysis.")
    if not _inference_lock.acquire(blocking=False):
        raise LiveDetectionBusy("The live detector is processing another frame.")

    started = time.perf_counter()
    try:
        device = str(configuration.device or "cpu").lower()
        if device == "auto":
            device = "cpu"
        try:
            result = model.predict(
                source=image,
                conf=confidence_threshold / 100,
                iou=configuration.iou_threshold / 100,
                imgsz=max(320, min(int(configuration.input_resolution or 640), 640)),
                device=device,
                half=bool(configuration.half_precision and device != "cpu"),
                max_det=configuration.max_detections,
                verbose=False,
                save=False,
            )[0]
        except Exception as exc:
            raise LiveDetectionError("The active model could not process this live frame.") from exc
    finally:
        _inference_lock.release()

    detections = []
    if result.boxes is not None:
        boxes = result.boxes.xyxyn.cpu().tolist()
        confidences = result.boxes.conf.cpu().tolist()
        class_ids = result.boxes.cls.int().cpu().tolist()
        polygons = result.masks.xyn if result.masks is not None else []
        for index, (box, confidence, class_id) in enumerate(zip(boxes, confidences, class_ids)):
            raw_label = str(result.names.get(class_id, class_id))
            normalized_label = raw_label.strip().lower().replace("-", "_").replace(" ", "_")
            if normalized_label not in {"pothole", "road_damage"}:
                continue
            x1, y1, x2, y2 = [round(max(0.0, min(1.0, float(value))), 6) for value in box]
            polygon = (
                [
                    [round(max(0.0, min(1.0, float(point[0]))), 6), round(max(0.0, min(1.0, float(point[1]))), 6)]
                    for point in polygons[index]
                ]
                if index < len(polygons) and len(polygons[index]) >= 3
                else []
            )
            detections.append(
                {
                    "label": normalized_label,
                    "confidence": round(float(confidence), 4),
                    "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                    "segmentation_points": polygon,
                }
            )

    elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
    return {
        "detections": detections,
        "total_detections": len(detections),
        "inference_ms": elapsed_ms,
        "inference_fps": round(1000 / elapsed_ms, 2),
        "frame_width": image.width,
        "frame_height": image.height,
        "model_task": model_task,
        "recommended_interval_ms": max(
            settings.LIVE_DETECTION_FRAME_INTERVAL_MS,
            elapsed_ms,
        ),
    }
