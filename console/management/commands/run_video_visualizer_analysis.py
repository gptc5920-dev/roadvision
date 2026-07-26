import gzip
import json
import os
import socket
import subprocess
import tempfile
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from console.models import (
    VideoDetectionSeverity,
    VideoPotholeTrack,
    VideoTrackReviewStatus,
    VideoVisualizerAnalysis,
    VideoVisualizerStatus,
)
from console.segmentation import draw_mask_overlay, estimate_detection_mask, normalized_polygon
from console.readiness import model_readiness
from console.storage_paths import resolve_model_artifact


class AnalysisCancelled(CommandError):
    pass


def transcode_browser_mp4(source_path):
    """Convert OpenCV's intermediate video into a browser-compatible H.264 MP4."""
    output_fd, output_path = tempfile.mkstemp(suffix="-browser.mp4")
    os.close(output_fd)
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                "-map",
                "0:v:0",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-movflags",
                "+faststart",
                "-an",
                output_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            error = (result.stderr or "FFmpeg did not produce a playable MP4.").strip()
            raise CommandError(f"Browser-compatible video encoding failed: {error[-1200:]}")
        return output_path
    except FileNotFoundError as exc:
        if os.path.exists(output_path):
            os.remove(output_path)
        raise CommandError("FFmpeg is required to create a browser-compatible analyzed video.") from exc
    except Exception:
        if os.path.exists(output_path):
            os.remove(output_path)
        raise


def iou(box_a, box_b):
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    x1 = max(ax1, bx1)
    y1 = max(ay1, by1)
    x2 = min(ax2, bx2)
    y2 = min(ay2, by2)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union else 0


def severity_for_bbox(relative_size):
    if relative_size >= 0.08:
        return VideoDetectionSeverity.CRITICAL
    if relative_size >= 0.04:
        return VideoDetectionSeverity.HIGH
    if relative_size >= 0.018:
        return VideoDetectionSeverity.MODERATE
    return VideoDetectionSeverity.LOW


def nearest_gps(points, timestamp):
    if not points:
        return None
    return min(points, key=lambda point: abs(float(point.get("timestamp", 0)) - timestamp))


def normalized_bbox(x1, y1, x2, y2, width, height):
    box_w = max(1, x2 - x1)
    box_h = max(1, y2 - y1)
    return {
        "center_x": round(((x1 + box_w / 2) / width), 6),
        "center_y": round(((y1 + box_h / 2) / height), 6),
        "width": round(box_w / width, 6),
        "height": round(box_h / height, 6),
    }


def normalized_label(value):
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


def polygon_relative_area(points, width, height, cv2, np):
    if len(points) < 3:
        return 0.0
    contour = np.asarray(points, dtype=np.float32)
    return float(abs(cv2.contourArea(contour))) / max(width * height, 1)


def combine_track_state(target, source):
    target["first_frame"] = min(target["first_frame"], source["first_frame"])
    target["last_frame"] = max(target["last_frame"], source["last_frame"])
    target["confidences"].extend(source["confidences"])
    target["relative_bbox_size"] = max(target["relative_bbox_size"], source["relative_bbox_size"])
    if source["best_confidence"] > target["best_confidence"]:
        for key in [
            "best_confidence", "best_frame", "best_bbox_pixels", "best_frame_image",
            "best_crop_image", "best_bbox", "best_polygon", "mask_source", "lat", "lng",
        ]:
            target[key] = source[key]
    if source["last_frame"] >= target["last_frame"]:
        target["last_bbox_pixels"] = source["last_bbox_pixels"]
    return target


def merge_fragmented_tracks(track_states, max_gap_frames, iou_threshold):
    merged = {}
    duplicate_count = 0
    for track_id, state in sorted(track_states.items(), key=lambda item: item[1]["first_frame"]):
        best_id = None
        best_score = 0.0
        for candidate_id, candidate in merged.items():
            gap = state["first_frame"] - candidate["last_frame"]
            if candidate["label"] != state["label"] or gap < 0 or gap > max_gap_frames:
                continue
            score = iou(candidate["last_bbox_pixels"], state["first_bbox_pixels"])
            if score >= iou_threshold and score > best_score:
                best_id = candidate_id
                best_score = score
        if best_id is None:
            merged[track_id] = state
        else:
            combine_track_state(merged[best_id], state)
            duplicate_count += 1
    return merged, duplicate_count


def encode_frame(frame, extension=".jpg"):
    import cv2

    ok, encoded = cv2.imencode(extension, frame)
    if not ok:
        return b""
    return encoded.tobytes()


def persist_track_state(analysis, track_id, state, output_fps, width, height, duration, cv2, np):
    """Persist one completed live/recorded track without retaining its images in worker memory."""
    confidences = state["confidences"] or [0]
    avg_conf = sum(confidences) / len(confidences)
    low_conf = min(confidences)
    high_conf = max(confidences)
    first_timestamp = state["first_frame"] / output_fps if output_fps else 0
    last_timestamp = state["last_frame"] / output_fps if output_fps else 0
    relative = state["relative_bbox_size"]
    estimated_length = None
    estimated_width = None
    estimated_area = None
    measurement_basis = VideoPotholeTrack.MeasurementBasis.VISUAL_ESTIMATE
    if (
        analysis.calibration_m_per_pixel
        and state.get("mask_source") == "model"
        and len(state["best_polygon"]) >= 3
    ):
        contour = np.asarray(state["best_polygon"], dtype=np.float32)
        rect_width, rect_height = cv2.minAreaRect(contour)[1]
        pixel_dimensions = sorted([max(1.0, rect_width), max(1.0, rect_height)], reverse=True)
        scale = Decimal(str(analysis.calibration_m_per_pixel))
        estimated_length = Decimal(str(pixel_dimensions[0])) * scale
        estimated_width = Decimal(str(pixel_dimensions[1])) * scale
        estimated_area = Decimal(str(abs(cv2.contourArea(contour)))) * scale * scale
        measurement_basis = VideoPotholeTrack.MeasurementBasis.CALIBRATED

    track, _created = VideoPotholeTrack.objects.update_or_create(
        analysis=analysis,
        track_id=track_id,
        defaults={
            "label": "Road damage" if state["label"] == "road_damage" else "Pothole",
            "first_frame": state["first_frame"],
            "last_frame": state["last_frame"],
            "first_timestamp": Decimal(str(round(first_timestamp, 3))),
            "last_timestamp": Decimal(str(round(last_timestamp, 3))),
            "appearance_count": len(confidences),
            "average_confidence": Decimal(str(round(avg_conf, 4))),
            "highest_confidence": Decimal(str(round(high_conf, 4))),
            "lowest_confidence": Decimal(str(round(low_conf, 4))),
            "best_frame": state["best_frame"],
            "best_bbox": state["best_bbox"],
            "best_segmentation_points": normalized_polygon(state["best_polygon"], width, height),
            "severity": severity_for_bbox(relative),
            "relative_bbox_size": Decimal(str(round(relative, 5))),
            "measurement_basis": measurement_basis,
            "estimated_length_m": estimated_length,
            "estimated_width_m": estimated_width,
            "estimated_surface_area_sqm": estimated_area,
            "latitude": Decimal(str(state["lat"])) if state.get("lat") is not None else None,
            "longitude": Decimal(str(state["lng"])) if state.get("lng") is not None else None,
            "road_section": analysis.road_section,
            "review_status": VideoTrackReviewStatus.UNRESOLVED,
        },
    )
    if state["best_crop_image"] is not None:
        if track.snapshot_crop:
            track.snapshot_crop.delete(save=False)
        track.snapshot_crop.save(
            f"analysis-{analysis.pk}-p{track_id}-crop.jpg",
            ContentFile(encode_frame(state["best_crop_image"])),
            save=False,
        )
    if state["best_frame_image"] is not None:
        if track.snapshot_frame:
            track.snapshot_frame.delete(save=False)
        track.snapshot_frame.save(
            f"analysis-{analysis.pk}-p{track_id}-frame.jpg",
            ContentFile(encode_frame(state["best_frame_image"])),
            save=False,
        )
    track.save()
    marker = {
        "track_id": track_id,
        "timestamp": round(first_timestamp, 3),
        "frame": state["first_frame"],
        "percent": round((first_timestamp / max(float(duration or 0), 1)) * 100, 3),
        "status": VideoTrackReviewStatus.UNRESOLVED,
        "severity": track.severity,
        "confidence": round(high_conf, 4),
        "label": state["label"],
    }
    return track, marker


def persist_track_states(analysis, states, output_fps, width, height, duration, cv2, np):
    min_appearances = max(1, int(analysis.min_track_appearances or 1))
    accepted = {
        track_id: state
        for track_id, state in states.items()
        if len(state["confidences"]) >= min_appearances
    }
    discarded = len(states) - len(accepted)
    accepted, duplicates = merge_fragmented_tracks(
        accepted,
        int(analysis.dedup_max_gap_frames or 0),
        float(analysis.dedup_iou_threshold or 0),
    )
    markers = []
    for track_id, state in accepted.items():
        _track, marker = persist_track_state(
            analysis, track_id, state, output_fps, width, height, duration, cv2, np
        )
        markers.append(marker)
    return discarded, duplicates, markers


class Command(BaseCommand):
    help = "Run queued YOLO11 video pothole visualizer analyses."

    def add_arguments(self, parser):
        parser.add_argument("--analysis-id", type=int, help="Run one video analysis by ID.")
        parser.add_argument("--watch", action="store_true", help="Keep polling for queued analyses.")
        parser.add_argument("--poll-interval", type=float, default=2.0)
        parser.add_argument("--max-jobs", type=int, default=0, help="Stop after this many jobs; zero is unlimited.")

    def handle(self, *args, **options):
        try:
            import cv2
            from ultralytics import YOLO
        except Exception as exc:
            raise CommandError("OpenCV and Ultralytics are required. Run pip install -r requirements.txt.") from exc

        worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        completed_jobs = 0
        while True:
            analysis = self.claim_next(worker_id, options.get("analysis_id"))
            if analysis is None:
                if options["watch"] and (not options["max_jobs"] or completed_jobs < options["max_jobs"]):
                    time.sleep(max(options["poll_interval"], 0.2))
                    continue
                if completed_jobs == 0:
                    self.stdout.write("No queued video analyses found.")
                return
            self.stdout.write(f"Processing video analysis {analysis.pk}: {analysis.original_filename}")
            self._active_output_path = None
            self._active_input_path = None
            self._active_detection_path = None
            self._active_capture = None
            self._active_writer = None
            self._active_detection_stream = None
            try:
                self.process_analysis(analysis, cv2, YOLO)
            except AnalysisCancelled:
                self.stdout.write(self.style.WARNING(f"Analysis {analysis.pk} was cancelled or its lease was lost."))
            except Exception as exc:
                self.mark_failure(analysis.pk, exc, worker_id)
                self.stderr.write(self.style.ERROR(f"Analysis {analysis.pk} failed: {exc}"))
            finally:
                if self._active_capture is not None:
                    self._active_capture.release()
                if self._active_writer is not None:
                    self._active_writer.release()
                if self._active_detection_stream is not None:
                    self._active_detection_stream.close()
                if self._active_output_path and os.path.exists(self._active_output_path):
                    os.remove(self._active_output_path)
                if self._active_input_path and os.path.exists(self._active_input_path):
                    os.remove(self._active_input_path)
                if self._active_detection_path and os.path.exists(self._active_detection_path):
                    os.remove(self._active_detection_path)
            completed_jobs += 1
            if options.get("analysis_id") or (options["max_jobs"] and completed_jobs >= options["max_jobs"]):
                return

    def claim_next(self, worker_id, analysis_id=None):
        now = timezone.now()
        lease = now + timedelta(seconds=settings.ANALYSIS_LEASE_SECONDS)
        with transaction.atomic():
            candidates = VideoVisualizerAnalysis.objects.select_for_update().filter(
                Q(status__in=[VideoVisualizerStatus.QUEUED, VideoVisualizerStatus.RETRYING])
                | Q(status=VideoVisualizerStatus.RUNNING, lease_expires_at__lt=now),
                attempt_count__lt=F("max_attempts"),
            )
            if analysis_id:
                candidates = candidates.filter(pk=analysis_id)
            analysis = candidates.select_related("model_session").order_by("created_at").first()
            if analysis is None:
                return None
            analysis.status = VideoVisualizerStatus.RUNNING
            analysis.attempt_count += 1
            analysis.worker_id = worker_id
            analysis.heartbeat_at = now
            analysis.lease_expires_at = lease
            analysis.started_at = analysis.started_at or now
            analysis.error_message = ""
            analysis.save(
                update_fields=[
                    "status", "attempt_count", "worker_id", "heartbeat_at",
                    "lease_expires_at", "started_at", "error_message",
                ]
            )
            return analysis

    def mark_failure(self, analysis_id, exc, worker_id=None):
        with transaction.atomic():
            analysis = VideoVisualizerAnalysis.objects.select_for_update().get(pk=analysis_id)
            if analysis.status == VideoVisualizerStatus.CANCELLED:
                return
            if worker_id and analysis.worker_id != worker_id:
                return
            message = str(exc)[:4000]
            history = list(analysis.error_history or [])
            history.append({"at": timezone.now().isoformat(), "attempt": analysis.attempt_count, "error": message})
            analysis.error_history = history[-20:]
            analysis.error_message = message
            analysis.status = (
                VideoVisualizerStatus.RETRYING
                if analysis.attempt_count < analysis.max_attempts
                else VideoVisualizerStatus.FAILED
            )
            analysis.finished_at = timezone.now() if analysis.status == VideoVisualizerStatus.FAILED else None
            analysis.worker_id = ""
            analysis.lease_expires_at = None
            analysis.save(update_fields=["error_history", "error_message", "status", "finished_at", "worker_id", "lease_expires_at"])

    def process_analysis(self, analysis, cv2, YOLO):
        import numpy as np
        import torch

        expected_worker_id = analysis.worker_id

        if not analysis.model_session or not analysis.model_session.model_file:
            raise CommandError("No trained model file is attached to this video analysis.")
        readiness = model_readiness(analysis.model_session)
        if not readiness["ready"]:
            raise CommandError("Model readiness gate failed: " + " ".join(readiness["errors"]))
        model_path = resolve_model_artifact(analysis.model_session.model_file)
        if not model_path.is_file():
            raise CommandError(f"Model file does not exist: {model_path}")

        model = YOLO(str(model_path))
        detection_mode = model.task == "detect" and settings.ALLOW_DETECTION_MODE
        if model.task != "segment" and not detection_mode:
            raise CommandError("This model task is not enabled for video analysis.")
        if analysis.video:
            try:
                source = analysis.video.path
            except (AttributeError, NotImplementedError):
                suffix = Path(analysis.video.name).suffix or ".mp4"
                input_fd, source = tempfile.mkstemp(suffix=suffix)
                os.close(input_fd)
                self._active_input_path = source
                analysis.video.open("rb")
                try:
                    with open(source, "wb") as target:
                        for chunk in iter(lambda: analysis.video.read(1024 * 1024), b""):
                            target.write(chunk)
                finally:
                    analysis.video.close()
        else:
            source = analysis.source_url
        capture = cv2.VideoCapture(source)
        self._active_capture = capture
        if analysis.is_continuous and not analysis.video:
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not capture.isOpened():
            raise CommandError("Uploaded video could not be opened for processing.")

        fps = float(capture.get(cv2.CAP_PROP_FPS) or analysis.fps or 30)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or analysis.width)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or analysis.height)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or analysis.frame_count)
        output_fps = fps if fps > 0 else 30
        continuous_live = bool(analysis.is_continuous and not analysis.video and analysis.source_url)
        output_path = None
        writer = None
        if not continuous_live:
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            output_fd, output_path = tempfile.mkstemp(suffix=".mp4")
            os.close(output_fd)
            self._active_output_path = output_path
            writer = cv2.VideoWriter(output_path, fourcc, output_fps, (width, height))
            self._active_writer = writer
            if not writer.isOpened():
                raise CommandError("Processed video writer could not be initialized.")

        active_tracks = {}
        track_states = {}
        tracker_track_ids = {}
        existing_track_id = (
            VideoPotholeTrack.objects.filter(analysis=analysis).aggregate(value=Max("track_id"))["value"] or 0
        ) if continuous_live else 0
        next_track_id = existing_track_id + 1
        frame_idx = int(analysis.current_frame or 0) if continuous_live else 0
        processed_frames = int(analysis.frames_processed or 0) if continuous_live else 0
        total_detections = int(analysis.total_detections or 0) if continuous_live else 0
        confidence_count = total_detections
        confidence_sum = float(analysis.average_confidence or 0) * confidence_count
        confidence_low = float(analysis.lowest_confidence) if analysis.lowest_confidence is not None else None
        confidence_high = float(analysis.highest_confidence) if analysis.highest_confidence is not None else None
        previous_processing_ms = int(analysis.processing_time_ms or 0) if continuous_live else 0
        detection_path = None
        detection_stream = None
        if not continuous_live:
            detection_fd, detection_path = tempfile.mkstemp(suffix=".json.gz")
            os.close(detection_fd)
            self._active_detection_path = detection_path
            detection_stream = gzip.open(detection_path, "wt", encoding="utf-8")
            self._active_detection_stream = detection_stream
            detection_stream.write("[")
        detection_record_count = 0
        started = time.perf_counter()
        last_preview_at = 0.0
        discarded_short_tracks = 0
        duplicate_tracks_merged = 0
        frame_skip = max(1, int(analysis.frame_skip or 1))
        if analysis.mode == "real-time":
            frame_skip = max(frame_skip, 2)
        effective_device = str(analysis.device or "auto").lower()
        if effective_device == "auto":
            effective_device = "0" if torch.cuda.is_available() else "cpu"
        if effective_device == "cpu":
            frame_skip = max(frame_skip, 3)
        analysis.device = effective_device
        analysis.effective_frame_skip = frame_skip
        analysis.save(update_fields=["device", "effective_frame_skip"])
        max_age_frames = max(12, frame_skip * 8)

        max_stream_frames = None if continuous_live else 900
        while True:
            if max_stream_frames is not None and not analysis.video and frame_idx >= max_stream_frames:
                break
            ok, frame = capture.read()
            if not ok:
                if not continuous_live:
                    break
                capture.release()
                self._active_capture = None
                current = VideoVisualizerAnalysis.objects.filter(pk=analysis.pk).values(
                    "status", "stop_requested"
                ).first()
                if not current or current["status"] != VideoVisualizerStatus.RUNNING:
                    raise AnalysisCancelled("Continuous analysis was cancelled while reconnecting.")
                if current["stop_requested"]:
                    break
                now = timezone.now()
                VideoVisualizerAnalysis.objects.filter(
                    pk=analysis.pk,
                    status=VideoVisualizerStatus.RUNNING,
                    worker_id=analysis.worker_id,
                ).update(
                    error_message="Live stream interrupted; reconnecting automatically.",
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=settings.ANALYSIS_LEASE_SECONDS),
                )
                time.sleep(settings.CONTINUOUS_STREAM_RECONNECT_SECONDS)
                capture = cv2.VideoCapture(source)
                self._active_capture = capture
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if capture.isOpened():
                    VideoVisualizerAnalysis.objects.filter(pk=analysis.pk).update(error_message="")
                continue
            timestamp = frame_idx / output_fps if output_fps else 0
            detections_for_frame = []

            if frame_idx % frame_skip == 0:
                inference_options = {
                    "source": frame,
                    "conf": analysis.confidence_threshold / 100,
                    "iou": analysis.iou_threshold / 100,
                    "imgsz": analysis.input_resolution,
                    "device": effective_device,
                    "half": analysis.half_precision and effective_device != "cpu",
                    "max_det": analysis.max_detections,
                    "verbose": False,
                }
                use_ultralytics_tracker = analysis.tracker in {"bytetrack.yaml", "botsort.yaml"}
                if use_ultralytics_tracker:
                    results = model.track(persist=True, tracker=analysis.tracker, **inference_options)[0]
                else:
                    results = model.predict(**inference_options)[0]
                boxes = []
                if results.boxes is not None:
                    xyxy = results.boxes.xyxy.cpu().tolist()
                    confidences = results.boxes.conf.cpu().tolist()
                    class_ids = results.boxes.cls.int().cpu().tolist()
                    tracked_ids = (
                        results.boxes.id.int().cpu().tolist()
                        if use_ultralytics_tracker and results.boxes.id is not None
                        else [None] * len(xyxy)
                    )
                    if results.masks is None and not detection_mode:
                        raise CommandError("Segmentation model returned detections without instance masks.")
                    mask_polygons = results.masks.xy if results.masks is not None else []
                    for index, (raw_box, confidence, tracked_id, class_id) in enumerate(zip(xyxy, confidences, tracked_ids, class_ids)):
                        class_name = normalized_label(results.names.get(class_id, class_id))
                        allowed_labels = {"pothole"}
                        if analysis.include_road_damage:
                            allowed_labels.add("road_damage")
                        if class_name not in allowed_labels:
                            continue
                        x1, y1, x2, y2 = [int(round(value)) for value in raw_box]
                        x1 = max(0, min(width - 1, x1))
                        y1 = max(0, min(height - 1, y1))
                        x2 = max(x1 + 1, min(width, x2))
                        y2 = max(y1 + 1, min(height, y2))
                        polygon = (
                            [[int(round(x)), int(round(y))] for x, y in mask_polygons[index]]
                            if index < len(mask_polygons) and len(mask_polygons[index]) >= 3
                            else []
                        )
                        if not polygon and detection_mode and settings.DETECTION_MASK_REFINEMENT:
                            polygon = estimate_detection_mask(
                                frame,
                                (x1, y1, x2, y2),
                                cv2,
                                settings.DETECTION_MASK_MAX_SIZE,
                            )
                        if not polygon and not detection_mode:
                            continue
                        boxes.append(((x1, y1, x2, y2), float(confidence), tracked_id, polygon, class_name))

                for bbox, confidence, tracked_id, polygon, class_name in boxes:
                    best_track_id = None
                    if tracked_id is not None:
                        tracker_key = (int(tracked_id), class_name)
                        best_track_id = tracker_track_ids.get(tracker_key)
                        if best_track_id is None:
                            best_track_id = next_track_id
                            next_track_id += 1
                            tracker_track_ids[tracker_key] = best_track_id
                    best_iou = 0
                    if best_track_id is None:
                        for track_id, state in active_tracks.items():
                            if state["label"] != class_name or frame_idx - state["last_frame"] > max_age_frames:
                                continue
                            score = iou(bbox, state["bbox"])
                            if score > best_iou:
                                best_iou = score
                                best_track_id = track_id
                    if best_track_id is None or (tracked_id is None and best_iou < 0.28):
                        best_track_id = next_track_id
                        next_track_id += 1
                    if best_track_id not in track_states:
                        track_states[best_track_id] = {
                            "track_id": best_track_id,
                            "first_frame": frame_idx,
                            "last_frame": frame_idx,
                            "confidences": [],
                            "best_confidence": -1,
                            "best_frame": frame_idx,
                            "best_bbox_pixels": bbox,
                            "best_frame_image": None,
                            "best_crop_image": None,
                            "best_bbox": {},
                            "relative_bbox_size": 0,
                            "lat": None,
                            "lng": None,
                            "label": class_name,
                            "mask_source": "estimated" if detection_mode and polygon else ("model" if polygon else "none"),
                            "best_polygon": polygon,
                            "first_bbox_pixels": bbox,
                            "last_bbox_pixels": bbox,
                        }

                    active_tracks[best_track_id] = {"bbox": bbox, "last_frame": frame_idx, "label": class_name}
                    state = track_states[best_track_id]
                    state["last_frame"] = frame_idx
                    state["last_bbox_pixels"] = bbox
                    state["confidences"].append(confidence)
                    x1, y1, x2, y2 = bbox
                    relative_size = (
                        polygon_relative_area(polygon, width, height, cv2, np)
                        if polygon and not detection_mode
                        else ((x2 - x1) * (y2 - y1)) / max(width * height, 1)
                    )
                    state["relative_bbox_size"] = max(state["relative_bbox_size"], relative_size)
                    if confidence > state["best_confidence"]:
                        crop = frame[y1:y2, x1:x2].copy()
                        state["best_confidence"] = confidence
                        state["best_frame"] = frame_idx
                        state["best_bbox_pixels"] = bbox
                        state["best_frame_image"] = frame.copy()
                        state["best_crop_image"] = crop
                        state["best_bbox"] = normalized_bbox(x1, y1, x2, y2, width, height)
                        state["best_polygon"] = polygon
                        state["mask_source"] = "estimated" if detection_mode and polygon else ("model" if polygon else "none")
                        point = nearest_gps(analysis.gps_points, timestamp)
                        if point:
                            state["lat"] = point.get("lat")
                            state["lng"] = point.get("lng")

                    total_detections += 1
                    confidence_sum += confidence
                    confidence_count += 1
                    confidence_low = confidence if confidence_low is None else min(confidence_low, confidence)
                    confidence_high = confidence if confidence_high is None else max(confidence_high, confidence)
                    norm_box = normalized_bbox(x1, y1, x2, y2, width, height)
                    detections_for_frame.append(
                        {
                            "track_id": best_track_id,
                            "confidence": round(confidence, 4),
                            "bbox": norm_box,
                            "segmentation_points": normalized_polygon(polygon, width, height),
                            "mask_source": "estimated" if detection_mode and polygon else ("model" if polygon else "none"),
                            "label": class_name,
                            "lat": state.get("lat"),
                            "lng": state.get("lng"),
                        }
                    )

                    draw_mask_overlay(
                        frame,
                        polygon,
                        cv2,
                        color=(0, 165, 255) if detection_mode else (255, 0, 255),
                    )
                    if analysis.show_boxes:
                        color = (60, 60, 255)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                        label_bits = []
                        if analysis.show_labels:
                            label_bits.append("Road damage" if class_name == "road_damage" else "Pothole")
                        if analysis.show_tracking_ids:
                            label_bits.append(f"P{best_track_id}")
                        if analysis.show_confidence:
                            label_bits.append(f"{confidence:.2f}")
                        if label_bits:
                            cv2.putText(frame, " ".join(label_bits), (x1, max(18, y1 - 7)), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 2)

                processed_frames += 1
                if detection_stream is not None:
                    if detection_record_count:
                        detection_stream.write(",")
                    json.dump(
                        {"frame": frame_idx, "timestamp": round(timestamp, 3), "detections": detections_for_frame},
                        detection_stream,
                        separators=(",", ":"),
                    )
                    detection_record_count += 1

            cv2.putText(
                frame,
                f"Unique potholes: {sum(state['label'] == 'pothole' for state in track_states.values())} "
                f"Frame: {frame_idx} Time: {timestamp:.2f}s",
                (16, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (255, 255, 255),
                2,
            )
            if writer is not None:
                writer.write(frame)
            if continuous_live and time.perf_counter() - last_preview_at >= settings.LIVE_PREVIEW_INTERVAL_SECONDS:
                preview_bytes = encode_frame(frame)
                if preview_bytes:
                    if analysis.live_preview_frame:
                        analysis.live_preview_frame.delete(save=False)
                    analysis.live_preview_frame.save(
                        f"analysis-{analysis.pk}-live.jpg",
                        ContentFile(preview_bytes),
                        save=False,
                    )
                    analysis.save(update_fields=["live_preview_frame"])
                last_preview_at = time.perf_counter()
            frame_idx += 1
            if frame_idx % 30 == 0:
                now = timezone.now()
                current_state = VideoVisualizerAnalysis.objects.filter(pk=analysis.pk).values(
                    "status", "stop_requested"
                ).first()
                if not current_state or current_state["status"] == VideoVisualizerStatus.CANCELLED:
                    raise AnalysisCancelled("Analysis was cancelled.")
                stop_requested = continuous_live and current_state["stop_requested"]
                if continuous_live:
                    stale_before = frame_idx - max(
                        max_age_frames,
                        int(analysis.dedup_max_gap_frames or 0),
                    )
                    stale_ids = [
                        track_id
                        for track_id, state in track_states.items()
                        if state["last_frame"] < stale_before
                    ]
                    if stale_ids:
                        stale_states = {track_id: track_states.pop(track_id) for track_id in stale_ids}
                        newly_discarded, newly_merged, _markers = persist_track_states(
                            analysis,
                            stale_states,
                            output_fps,
                            width,
                            height,
                            frame_idx / output_fps if output_fps else 0,
                            cv2,
                            np,
                        )
                        discarded_short_tracks += newly_discarded
                        duplicate_tracks_merged += newly_merged
                        for track_id in stale_ids:
                            active_tracks.pop(track_id, None)
                        stale_set = set(stale_ids)
                        tracker_track_ids = {
                            key: value for key, value in tracker_track_ids.items() if value not in stale_set
                        }
                elapsed_seconds = max(time.perf_counter() - started, 0.001)
                cumulative_elapsed_seconds = max((previous_processing_ms / 1000) + elapsed_seconds, 0.001)
                source_duration = frame_idx / output_fps if output_fps else 0
                persisted_potholes = VideoPotholeTrack.objects.filter(
                    analysis=analysis,
                    label__iexact="Pothole",
                ).count() if continuous_live else 0
                active_potholes = sum(state["label"] == "pothole" for state in track_states.values())
                updated = VideoVisualizerAnalysis.objects.filter(
                    pk=analysis.pk,
                    status=VideoVisualizerStatus.RUNNING,
                    worker_id=analysis.worker_id,
                ).update(
                    current_frame=frame_idx,
                    frame_count=frame_idx if continuous_live else frame_count,
                    duration_seconds=Decimal(str(round(source_duration, 3))) if continuous_live else analysis.duration_seconds,
                    frames_processed=processed_frames,
                    processing_time_ms=int(cumulative_elapsed_seconds * 1000),
                    total_unique_potholes=persisted_potholes + active_potholes,
                    total_detections=total_detections,
                    average_confidence=Decimal(str(round(confidence_sum / confidence_count, 4))) if confidence_count else None,
                    highest_confidence=Decimal(str(round(confidence_high, 4))) if confidence_high is not None else None,
                    lowest_confidence=Decimal(str(round(confidence_low, 4))) if confidence_low is not None else None,
                    average_processing_fps=Decimal(str(round(processed_frames / cumulative_elapsed_seconds, 3))),
                    source_processing_fps=Decimal(str(round(frame_idx / cumulative_elapsed_seconds, 3))),
                    realtime_factor=Decimal(str(round(cumulative_elapsed_seconds / source_duration, 3))) if source_duration else Decimal("0"),
                    raw_track_count=max(0, next_track_id - 1),
                    discarded_short_tracks=discarded_short_tracks,
                    duplicate_tracks_merged=duplicate_tracks_merged,
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=settings.ANALYSIS_LEASE_SECONDS),
                )
                if not updated:
                    raise AnalysisCancelled("Worker lease was lost.")
                if stop_requested:
                    break

        capture.release()
        self._active_capture = None
        if writer is not None:
            writer.release()
            self._active_writer = None
        if detection_stream is not None:
            detection_stream.write("]")
            detection_stream.close()
            self._active_detection_stream = None
        if output_path is not None:
            browser_output_path = transcode_browser_mp4(output_path)
            os.remove(output_path)
            output_path = browser_output_path
            self._active_output_path = output_path

        elapsed_ms = previous_processing_ms + int((time.perf_counter() - started) * 1000)
        if not VideoVisualizerAnalysis.objects.filter(
            pk=analysis.pk,
            status=VideoVisualizerStatus.RUNNING,
            worker_id=analysis.worker_id,
        ).exists():
            raise AnalysisCancelled("Analysis was cancelled before results were committed.")
        source_duration = frame_idx / output_fps if output_fps and frame_idx else 0
        if continuous_live:
            newly_discarded, newly_merged, _markers = persist_track_states(
                analysis,
                track_states,
                output_fps,
                width,
                height,
                source_duration,
                cv2,
                np,
            )
            discarded_short_tracks += newly_discarded
            duplicate_tracks_merged += newly_merged
            raw_track_count = max(0, next_track_id - 1)
            timeline_markers = [
                {
                    "track_id": track.track_id,
                    "timestamp": float(track.first_timestamp),
                    "frame": track.first_frame,
                    "percent": round(
                        max(0, min(100, (float(track.first_timestamp) / source_duration) * 100)),
                        3,
                    ) if source_duration else 0,
                    "status": track.review_status,
                    "severity": track.severity,
                    "confidence": float(track.highest_confidence),
                    "label": "road_damage" if track.label.lower() == "road damage" else "pothole",
                }
                for track in VideoPotholeTrack.objects.filter(analysis=analysis).order_by("first_frame")
            ]
        else:
            raw_track_count = len(track_states)
            VideoPotholeTrack.objects.filter(analysis=analysis).delete()
            discarded_short_tracks, duplicate_tracks_merged, timeline_markers = persist_track_states(
                analysis,
                track_states,
                output_fps,
                width,
                height,
                source_duration,
                cv2,
                np,
            )
            with open(output_path, "rb") as processed:
                analysis.processed_video.save(f"analysis-{analysis.pk}-processed.mp4", File(processed), save=False)
            os.remove(output_path)
            self._active_output_path = None

        analysis.current_frame = frame_idx
        analysis.frame_count = frame_idx if continuous_live else frame_count
        analysis.duration_seconds = Decimal(str(round(source_duration, 3)))
        analysis.frames_processed = processed_frames
        analysis.processing_time_ms = elapsed_ms
        analysis.average_processing_fps = Decimal(str(round((processed_frames / (elapsed_ms / 1000)) if elapsed_ms else 0, 3)))
        analysis.source_processing_fps = Decimal(str(round((frame_idx / (elapsed_ms / 1000)) if elapsed_ms else 0, 3)))
        analysis.realtime_factor = Decimal(str(round((elapsed_ms / 1000) / source_duration, 3))) if source_duration else Decimal("0")
        analysis.raw_track_count = raw_track_count
        analysis.discarded_short_tracks = discarded_short_tracks
        analysis.duplicate_tracks_merged = duplicate_tracks_merged
        analysis.total_unique_potholes = VideoPotholeTrack.objects.filter(
            analysis=analysis,
            label__iexact="Pothole",
        ).count()
        analysis.total_detections = total_detections
        analysis.average_confidence = Decimal(str(round(confidence_sum / confidence_count, 4))) if confidence_count else None
        analysis.highest_confidence = Decimal(str(round(confidence_high, 4))) if confidence_high is not None else None
        analysis.lowest_confidence = Decimal(str(round(confidence_low, 4))) if confidence_low is not None else None
        analysis.timeline_markers = timeline_markers
        if detection_path is not None:
            with open(detection_path, "rb") as detection_artifact:
                analysis.frame_detections_artifact.save(
                    f"analysis-{analysis.pk}-detections.json.gz",
                    File(detection_artifact),
                    save=False,
                )
            os.remove(detection_path)
            self._active_detection_path = None
        analysis.frame_detections = []
        with transaction.atomic():
            locked = VideoVisualizerAnalysis.objects.select_for_update().get(pk=analysis.pk)
            owns_job = (
                locked.status == VideoVisualizerStatus.RUNNING
                and locked.worker_id == expected_worker_id
            )
            if owns_job:
                analysis.status = VideoVisualizerStatus.COMPLETE
                analysis.stop_requested = False
                analysis.error_message = ""
                analysis.finished_at = timezone.now()
                analysis.worker_id = ""
                analysis.lease_expires_at = None
                analysis.save()
        if not owns_job:
            VideoPotholeTrack.objects.filter(analysis=analysis).delete()
            if analysis.processed_video:
                analysis.processed_video.delete(save=False)
            if analysis.frame_detections_artifact:
                analysis.frame_detections_artifact.delete(save=False)
            raise AnalysisCancelled("Analysis was cancelled while results were being finalized.")
        pothole_count = VideoPotholeTrack.objects.filter(analysis=analysis, label__iexact="Pothole").count()
        self.stdout.write(self.style.SUCCESS(f"Analysis {analysis.pk} complete with {pothole_count} unique potholes."))
