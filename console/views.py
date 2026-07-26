import csv
import gzip
import hashlib
import io
import json
import ipaddress
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db.models import Count, Min, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import get_valid_filename
from django.utils import timezone
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from .authz import is_admin, is_staff_role, user_role
from .forms import (
    AnalyzerSettingsForm,
    DatasetUploadForm,
    DetectionTestForm,
    FleetDeviceForm,
    GroundTruthCountForm,
    PersonnelAccountForm,
    SignInForm,
    TrainingConfigForm,
    VideoAnalysisForm,
    VideoVisualizerUploadForm,
)
from .ml import run_analysis
from .readiness import dataset_readiness, model_readiness, training_dataset_manifest
from .segmentation import draw_mask_overlay, estimate_detection_mask, pixel_polygon
from .storage_paths import materialized_field_file, resolve_model_artifact
from .models import (
    AppRole,
    AnalyzerConfiguration,
    DatasetAuditLog,
    DatasetAugmentationJob,
    DatasetImage,
    DatasetImageSource,
    DatasetImageStatus,
    DatasetSplit,
    DatasetVersion,
    DetectionTest,
    EngineeringRecommendation,
    FleetDevice,
    PotholeAnnotation,
    PotholeReport,
    ReportStatus,
    TrainingSession,
    UserRole,
    VideoAnalysis,
    VideoDatasetSample,
    VideoPotholeTrack,
    VideoSourceType,
    VideoTrackReviewStatus,
    VideoVisualizerAnalysis,
    VideoVisualizerMode,
    VideoVisualizerStatus,
)


ALLOWED_DATASET_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_DATASET_IMAGE_BYTES = 10 * 1024 * 1024
MIN_DATASET_IMAGE_WIDTH = 320
MIN_DATASET_IMAGE_HEIGHT = 240
MAX_DATASET_IMAGE_DIMENSION = 10000
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
ALLOWED_GPS_EXTENSIONS = {".csv", ".gpx", ".json"}
MAX_VISUALIZER_VIDEO_BYTES = 750 * 1024 * 1024
MAX_VISUALIZER_GPS_BYTES = 10 * 1024 * 1024


def landing(request):
    return render(request, "console/landing.html")


def health(request):
    try:
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def liveness(request):
    return JsonResponse({"status": "alive"})


def media_access(request):
    """Authorize Nginx internal subrequests before serving local media files."""
    original_uri = request.headers.get("X-Original-URI", "")
    original_path = urlparse(original_uri).path
    safe_path = original_path.startswith(settings.MEDIA_URL) and ".." not in original_path.split("/")
    if request.user.is_authenticated and is_staff_role(request.user) and safe_path:
        return HttpResponse(status=204)
    return HttpResponse(status=403)


def auth_page(request):
    if request.user.is_authenticated and is_staff_role(request.user):
        return redirect("admin_video_analyzer")

    email = request.POST.get("email", "").strip().lower()
    remote_address = request.META.get("REMOTE_ADDR", "unknown")
    throttle_key = "login:" + hashlib.sha256(f"{remote_address}:{email}".encode()).hexdigest()
    attempts = cache.get(throttle_key, 0)
    locked = request.method == "POST" and attempts >= settings.LOGIN_MAX_ATTEMPTS
    form = SignInForm(request.POST or None)

    if locked:
        form.is_valid()
        form.add_error(None, "Too many sign-in attempts. Try again in a few minutes.")
    elif request.method == "POST" and form.is_valid():
        cache.delete(throttle_key)
        login(request, form.cleaned_data["user"])
        return redirect("admin_video_analyzer")
    elif request.method == "POST":
        cache.set(throttle_key, attempts + 1, settings.LOGIN_LOCKOUT_SECONDS)

    return render(request, "console/auth.html", {"form": form})


def sign_out(request):
    logout(request)
    return redirect("home")


@login_required
def authenticated_index(request):
    return redirect("admin_video_analyzer")


def not_found_redirect(request, unused):
    return redirect("home")


def staff_required(view_func):
    @login_required
    def wrapper(request, *args, **kwargs):
        if not is_staff_role(request.user):
            return render(request, "console/access_pending.html", status=403)
        return view_func(request, *args, **kwargs)

    return wrapper


def admin_context(request, active):
    profile = getattr(request.user, "profile", None)
    online_count = FleetDevice.objects.filter(status="online").count()
    return {
        "active": active,
        "profile": profile,
        "role": user_role(request.user) or AppRole.VIEWER,
        "is_admin": is_admin(request.user),
        "online_vehicle_count": online_count,
    }


def audit_dataset(user, action, message, dataset_image=None, dataset_version=None):
    DatasetAuditLog.objects.create(
        action=action,
        message=message[:255],
        dataset_image=dataset_image,
        dataset_version=dataset_version,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )


def create_dataset_version(user, notes="", train_percent=70, val_percent=20, test_percent=10):
    latest = DatasetVersion.objects.order_by("-version_number").first()
    version_number = latest.version_number + 1 if latest else 1
    active_images = DatasetImage.objects.filter(is_archived=False)
    version = DatasetVersion.objects.create(
        version_number=version_number,
        train_percent=train_percent,
        val_percent=val_percent,
        test_percent=test_percent,
        total_images=active_images.count(),
        total_annotations=PotholeAnnotation.objects.filter(image__is_archived=False).count(),
        notes=notes,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    audit_dataset(user, "version", f"Created dataset version v{version.version_number}.", dataset_version=version)
    return version


def current_dataset_version():
    return DatasetVersion.objects.order_by("-version_number").first()


def dataset_summary():
    images = DatasetImage.objects.filter(is_archived=False)
    total_images = images.count()
    annotated_images = images.filter(annotations__isnull=False).distinct().count()
    current_version = current_dataset_version()
    latest_complete = TrainingSession.objects.filter(
        status=TrainingSession.Status.COMPLETE,
        is_validated=True,
        local_map50__isnull=False,
    ).first()
    return {
        "total_images": total_images,
        "annotated_images": annotated_images,
        "unannotated_images": images.filter(status=DatasetImageStatus.UNANNOTATED).count(),
        "approved_images": images.filter(status=DatasetImageStatus.APPROVED).count(),
        "rejected_images": images.filter(status=DatasetImageStatus.REJECTED).count(),
        "total_potholes": PotholeAnnotation.objects.filter(image__is_archived=False).count(),
        "current_dataset_version": current_version.version_number if current_version else 0,
        "latest_model_accuracy": (
            latest_complete.local_map50
            if latest_complete and latest_complete.local_map50 is not None
            else None
        ),
    }


def read_uploaded_image(upload):
    extension = os.path.splitext(upload.name)[1].lower()
    if extension not in ALLOWED_DATASET_IMAGE_EXTENSIONS:
        raise ValueError("Unsupported image type. Upload JPG, JPEG, PNG, or WEBP.")
    data = b"".join(upload.chunks())
    if not data:
        raise ValueError("The uploaded image is empty.")
    if len(data) > MAX_DATASET_IMAGE_BYTES:
        raise ValueError("Image exceeds the 10 MB size limit.")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise ValueError("Image is corrupted or cannot be decoded.") from exc
    width, height = image.size
    if width < MIN_DATASET_IMAGE_WIDTH or height < MIN_DATASET_IMAGE_HEIGHT:
        raise ValueError(f"Image resolution must be at least {MIN_DATASET_IMAGE_WIDTH}x{MIN_DATASET_IMAGE_HEIGHT}.")
    if width > MAX_DATASET_IMAGE_DIMENSION or height > MAX_DATASET_IMAGE_DIMENSION:
        raise ValueError(f"Image dimensions must not exceed {MAX_DATASET_IMAGE_DIMENSION}px.")
    return {
        "data": data,
        "file_hash": hashlib.sha256(data).hexdigest(),
        "file_size": len(data),
        "file_type": (image.format or extension.lstrip(".")).lower(),
        "width": width,
        "height": height,
        "extension": extension,
    }


def read_uploaded_video(upload):
    extension = os.path.splitext(upload.name)[1].lower()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise ValueError("Unsupported video type. Upload MP4, AVI, MOV, MKV, or WEBM.")
    file_size = int(getattr(upload, "size", 0) or 0)
    if not file_size:
        raise ValueError("The uploaded video is empty.")
    if file_size > MAX_VISUALIZER_VIDEO_BYTES:
        raise ValueError("Video exceeds the 750 MB size limit.")
    try:
        import cv2
    except Exception as exc:
        raise ValueError("OpenCV is required to inspect uploaded video files.") from exc

    temp_path = ""
    owns_temp_path = False
    try:
        hasher = hashlib.sha256()
        if hasattr(upload, "temporary_file_path"):
            temp_path = upload.temporary_file_path()
            for chunk in upload.chunks():
                hasher.update(chunk)
        else:
            with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temp_file:
                owns_temp_path = True
                temp_path = temp_file.name
                for chunk in upload.chunks():
                    hasher.update(chunk)
                    temp_file.write(chunk)
        upload.seek(0)
        capture = cv2.VideoCapture(temp_path)
        if not capture.isOpened():
            raise ValueError("Video could not be opened. The file may be corrupted or encoded with an unsupported codec.")
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        capture.release()
    finally:
        if owns_temp_path and temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    if width < 160 or height < 120:
        raise ValueError("Video resolution must be at least 160x120.")
    if fps <= 0 or fps > 240:
        raise ValueError("Video frame rate must be readable and no higher than 240 FPS.")
    if frame_count <= 0:
        raise ValueError("Video must contain at least one readable frame.")
    duration = frame_count / fps if fps else 0
    if duration > 7200:
        raise ValueError("Video duration exceeds the 2 hour analysis limit.")
    return {
        "file_hash": hasher.hexdigest(),
        "file_size": file_size,
        "file_type": extension.lstrip("."),
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": frame_count,
        "duration_seconds": duration,
        "extension": extension,
    }


def parse_gps_upload(upload):
    if not upload:
        return [], {}
    extension = os.path.splitext(upload.name)[1].lower()
    if extension not in ALLOWED_GPS_EXTENSIONS:
        raise ValueError("GPS files must be CSV, GPX, or JSON.")
    data = b"".join(upload.chunks())
    if len(data) > MAX_VISUALIZER_GPS_BYTES:
        raise ValueError("GPS file exceeds the 10 MB size limit.")
    text = data.decode("utf-8-sig", errors="replace")
    points = []
    if extension == ".json":
        payload = json.loads(text or "[]")
        if isinstance(payload, dict):
            payload = payload.get("points", [])
        for item in payload:
            points.append(
                {
                    "timestamp": float(item.get("timestamp", item.get("time", 0)) or 0),
                    "lat": float(item.get("lat", item.get("latitude"))),
                    "lng": float(item.get("lng", item.get("lon", item.get("longitude")))),
                }
            )
    elif extension == ".csv":
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            lat = row.get("lat") or row.get("latitude")
            lng = row.get("lng") or row.get("lon") or row.get("longitude")
            timestamp = row.get("timestamp") or row.get("seconds") or row.get("time") or 0
            if lat and lng:
                points.append({"timestamp": float(timestamp or 0), "lat": float(lat), "lng": float(lng)})
    elif extension == ".gpx":
        root = ElementTree.fromstring(text)
        namespace = ""
        if root.tag.startswith("{"):
            namespace = root.tag.split("}")[0] + "}"
        index = 0
        for point in root.iter(f"{namespace}trkpt"):
            lat = point.attrib.get("lat")
            lng = point.attrib.get("lon")
            if lat and lng:
                points.append({"timestamp": float(index), "lat": float(lat), "lng": float(lng)})
                index += 1
    return points, {"filename": upload.name, "point_count": len(points)}


def read_video_stream_metadata(stream_url):
    parsed = urlparse(stream_url)
    if parsed.scheme.lower() not in {"rtsp", "http", "https"} or not parsed.hostname:
        raise ValueError("Live stream URL must start with rtsp://, http://, or https://.")
    if parsed.username or parsed.password:
        raise ValueError("Put stream credentials in a protected camera configuration, not in the URL.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 80)}
    except socket.gaierror as exc:
        raise ValueError("Live stream hostname could not be resolved.") from exc
    if not settings.ALLOW_PRIVATE_STREAMS:
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError("Private-network streams are disabled. Set ALLOW_PRIVATE_STREAMS=true for trusted cameras.")
    try:
        import cv2
    except Exception as exc:
        raise ValueError("OpenCV is required to inspect live stream sources.") from exc
    capture = cv2.VideoCapture(stream_url)
    if not capture.isOpened():
        raise ValueError("Live stream could not be opened. Check the URL and camera availability.")
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0) or 30
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    capture.release()
    if width <= 0 or height <= 0:
        width, height = 1280, 720
    return {
        "file_hash": hashlib.sha256(stream_url.encode("utf-8")).hexdigest(),
        "file_size": 0,
        "file_type": "stream",
        "width": width,
        "height": height,
        "fps": min(max(fps, 1), 240),
        "frame_count": frame_count,
        "duration_seconds": frame_count / fps if frame_count and fps else 0,
    }


def active_video_model():
    candidates = TrainingSession.objects.filter(
        status=TrainingSession.Status.COMPLETE,
        is_validated=True,
    ).exclude(model_file="")
    candidates = candidates.filter(
        model_task__in=["segment", "detect"] if settings.ALLOW_DETECTION_MODE else ["segment"]
    )
    if settings.MODEL_REQUIRE_LOCAL_EVALUATION:
        local_gate = Q(
            model_task="segment",
            local_evaluation_at__isnull=False,
            local_test_images__gte=settings.MODEL_MIN_LOCAL_TEST_IMAGES,
            local_map50__gte=settings.MODEL_MIN_MAP50,
        )
        if settings.ALLOW_DETECTION_MODE:
            local_gate |= Q(model_task="detect")
        candidates = candidates.filter(local_gate)
    return (
        candidates.filter(is_active_video_model=True)
        .exclude(model_file="")
        .first()
        or candidates.order_by("-created_at").first()
    )


def analyzer_configuration():
    active_model = active_video_model()
    configuration, _ = AnalyzerConfiguration.objects.get_or_create(
        pk=1,
        defaults={"model_session": active_model, "max_attempts": settings.ANALYSIS_MAX_ATTEMPTS},
    )
    if (configuration.model_session_id is None or not model_readiness(configuration.model_session)["ready"]) and active_model:
        configuration.model_session = active_model
        configuration.save(update_fields=["model_session", "updated_at"])
    return configuration


def recalculate_analysis_potholes(analysis):
    analysis.total_unique_potholes = analysis.tracks.filter(label__iexact="Pothole").exclude(
        review_status=VideoTrackReviewStatus.REJECTED
    ).count()
    analysis.save(update_fields=["total_unique_potholes"])
    return analysis.total_unique_potholes


def analysis_frame_detections(analysis):
    if not analysis:
        return []
    if analysis.frame_detections_artifact:
        analysis.frame_detections_artifact.open("rb")
        try:
            return json.loads(gzip.decompress(analysis.frame_detections_artifact.read()).decode("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        finally:
            analysis.frame_detections_artifact.close()
    return analysis.frame_detections


def start_analysis_worker(analysis_id):
    if not settings.AUTO_START_ANALYSIS_WORKER:
        return
    command = [
        sys.executable,
        str(settings.BASE_DIR / "manage.py"),
        "run_video_visualizer_analysis",
        "--analysis-id",
        str(analysis_id),
    ]
    options = {
        "cwd": str(settings.BASE_DIR),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen(command, **options)


def ensure_analysis_worker(analysis):
    if not analysis or not settings.AUTO_START_ANALYSIS_WORKER:
        return
    now = timezone.now()
    is_waiting = analysis.status in {VideoVisualizerStatus.QUEUED, VideoVisualizerStatus.RETRYING}
    has_expired_lease = (
        analysis.status == VideoVisualizerStatus.RUNNING
        and analysis.lease_expires_at
        and analysis.lease_expires_at < now
    )
    has_source = bool(analysis.video or analysis.source_url)
    if not has_source or analysis.attempt_count >= analysis.max_attempts or not (is_waiting or has_expired_lease):
        return
    if cache.add(f"video-analysis-worker-start:{analysis.pk}", True, timeout=15):
        start_analysis_worker(analysis.pk)


def reset_visualizer_analysis(analysis, *, confidence_threshold=None):
    """Remove generated outputs and return an existing source video to the queue."""
    if analysis.processed_video:
        analysis.processed_video.delete(save=False)
    if analysis.frame_detections_artifact:
        analysis.frame_detections_artifact.delete(save=False)
    if analysis.live_preview_frame:
        analysis.live_preview_frame.delete(save=False)
    for track in analysis.tracks.all():
        if track.snapshot_crop:
            track.snapshot_crop.delete(save=False)
        if track.snapshot_frame:
            track.snapshot_frame.delete(save=False)
    analysis.tracks.all().delete()
    if confidence_threshold is not None:
        analysis.confidence_threshold = confidence_threshold
    if analysis.source_type == VideoSourceType.LIVE_STREAM and analysis.source_url:
        analysis.is_continuous = True
        analysis.mode = VideoVisualizerMode.REAL_TIME
    analysis.status = VideoVisualizerStatus.QUEUED
    analysis.current_frame = 0
    analysis.frames_processed = 0
    analysis.processing_time_ms = 0
    analysis.average_processing_fps = 0
    analysis.source_processing_fps = 0
    analysis.realtime_factor = 0
    analysis.effective_frame_skip = 1
    analysis.raw_track_count = 0
    analysis.discarded_short_tracks = 0
    analysis.duplicate_tracks_merged = 0
    analysis.total_unique_potholes = 0
    analysis.total_detections = 0
    analysis.average_confidence = None
    analysis.highest_confidence = None
    analysis.lowest_confidence = None
    analysis.timeline_markers = []
    analysis.frame_detections = []
    analysis.stop_requested = False
    analysis.error_message = ""
    analysis.attempt_count = 0
    analysis.worker_id = ""
    analysis.lease_expires_at = None
    analysis.heartbeat_at = None
    analysis.started_at = None
    analysis.finished_at = None
    analysis.save()
    return analysis


def request_continuous_analysis_stop(analysis):
    """Request a graceful stop so live tracks and snapshots can be finalized."""
    if not analysis.is_continuous:
        return False
    if analysis.status == VideoVisualizerStatus.RUNNING:
        analysis.stop_requested = True
        analysis.save(update_fields=["stop_requested"])
        return True
    if analysis.status in {VideoVisualizerStatus.QUEUED, VideoVisualizerStatus.RETRYING}:
        analysis.status = VideoVisualizerStatus.CANCELLED
        analysis.stop_requested = True
        analysis.worker_id = ""
        analysis.lease_expires_at = None
        analysis.finished_at = timezone.now()
        analysis.save(
            update_fields=[
                "status", "stop_requested", "worker_id", "lease_expires_at", "finished_at",
            ]
        )
        return True
    return False


def apply_current_analyzer_settings(analysis, configuration):
    """Refresh runtime settings while preserving this analysis's source and survey metadata."""
    for field in [
        "model_session",
        "mode",
        "confidence_threshold",
        "iou_threshold",
        "device",
        "input_resolution",
        "frame_skip",
        "half_precision",
        "tracker",
        "max_detections",
        "max_attempts",
        "include_road_damage",
        "min_track_appearances",
        "dedup_iou_threshold",
        "dedup_max_gap_frames",
        "show_labels",
        "show_confidence",
        "show_tracking_ids",
        "show_boxes",
        "show_gps_overlay",
    ]:
        setattr(analysis, field, getattr(configuration, field))
    return analysis


def save_visualizer_video(upload, filename, metadata, form, configuration, user, gps_upload=None):
    model_session = configuration.model_session
    gps_points = []
    route_metadata = {}
    if gps_upload:
        gps_points, route_metadata = parse_gps_upload(gps_upload)
        gps_upload.seek(0)
    analysis = VideoVisualizerAnalysis(
        original_filename=filename,
        file_hash=metadata["file_hash"],
        file_size=metadata["file_size"],
        file_type=metadata["file_type"],
        width=metadata["width"],
        height=metadata["height"],
        fps=Decimal(str(round(metadata["fps"], 3))),
        duration_seconds=Decimal(str(round(metadata["duration_seconds"], 3))),
        frame_count=metadata["frame_count"],
        source_type=form.cleaned_data["source_type"],
        mode=configuration.mode,
        model_session=model_session,
        confidence_threshold=form.cleaned_data.get("confidence_threshold") or configuration.confidence_threshold,
        iou_threshold=configuration.iou_threshold,
        device=configuration.device,
        input_resolution=configuration.input_resolution,
        frame_skip=configuration.frame_skip,
        half_precision=configuration.half_precision,
        tracker=configuration.tracker,
        max_detections=configuration.max_detections,
        include_road_damage=configuration.include_road_damage,
        min_track_appearances=configuration.min_track_appearances,
        dedup_iou_threshold=configuration.dedup_iou_threshold,
        dedup_max_gap_frames=configuration.dedup_max_gap_frames,
        show_labels=configuration.show_labels,
        show_confidence=configuration.show_confidence,
        show_tracking_ids=configuration.show_tracking_ids,
        show_boxes=configuration.show_boxes,
        show_gps_overlay=configuration.show_gps_overlay,
        road_section=form.cleaned_data["road_section"],
        chainage_station=form.cleaned_data["chainage_station"],
        calibration_m_per_pixel=form.cleaned_data["calibration_m_per_pixel"],
        calibration_notes=form.cleaned_data["calibration_notes"],
        route_metadata=route_metadata,
        gps_points=gps_points,
        max_attempts=configuration.max_attempts,
        created_by=user,
    )
    safe_stem = get_valid_filename(os.path.splitext(filename)[0] or "road-video")
    upload.seek(0)
    analysis.video.save(f"{timezone.now():%Y%m%d%H%M%S}-{safe_stem}{metadata['extension']}", upload, save=False)
    if gps_upload:
        gps_upload.seek(0)
        analysis.gps_file.save(get_valid_filename(gps_upload.name), gps_upload, save=False)
    analysis.save()
    return analysis


def create_visualizer_stream_analysis(stream_url, metadata, form, configuration, user, gps_upload=None):
    model_session = configuration.model_session
    gps_points = []
    route_metadata = {}
    gps_data = b""
    if gps_upload:
        gps_points, route_metadata = parse_gps_upload(gps_upload)
        gps_upload.seek(0)
        gps_data = b"".join(gps_upload.chunks())
    analysis = VideoVisualizerAnalysis(
        source_url=stream_url,
        original_filename=stream_url,
        file_hash=metadata["file_hash"],
        file_size=metadata["file_size"],
        file_type=metadata["file_type"],
        width=metadata["width"],
        height=metadata["height"],
        fps=Decimal(str(round(metadata["fps"], 3))),
        duration_seconds=Decimal(str(round(metadata["duration_seconds"], 3))),
        frame_count=metadata["frame_count"],
        source_type=VideoSourceType.LIVE_STREAM,
        mode=VideoVisualizerMode.REAL_TIME,
        is_continuous=True,
        model_session=model_session,
        confidence_threshold=form.cleaned_data.get("confidence_threshold") or configuration.confidence_threshold,
        iou_threshold=configuration.iou_threshold,
        device=configuration.device,
        input_resolution=configuration.input_resolution,
        frame_skip=configuration.frame_skip,
        half_precision=configuration.half_precision,
        tracker=configuration.tracker,
        max_detections=configuration.max_detections,
        include_road_damage=configuration.include_road_damage,
        min_track_appearances=configuration.min_track_appearances,
        dedup_iou_threshold=configuration.dedup_iou_threshold,
        dedup_max_gap_frames=configuration.dedup_max_gap_frames,
        show_labels=configuration.show_labels,
        show_confidence=configuration.show_confidence,
        show_tracking_ids=configuration.show_tracking_ids,
        show_boxes=configuration.show_boxes,
        show_gps_overlay=configuration.show_gps_overlay,
        road_section=form.cleaned_data["road_section"],
        chainage_station=form.cleaned_data["chainage_station"],
        calibration_m_per_pixel=form.cleaned_data["calibration_m_per_pixel"],
        calibration_notes=form.cleaned_data["calibration_notes"],
        route_metadata=route_metadata,
        gps_points=gps_points,
        max_attempts=configuration.max_attempts,
        created_by=user,
    )
    if gps_upload and gps_data:
        analysis.gps_file.save(get_valid_filename(gps_upload.name), ContentFile(gps_data), save=False)
    analysis.save()
    return analysis


def fleet_analysis_form(device, configuration, confidence_threshold, source_type):
    form = VideoVisualizerUploadForm(
        {
            "source_type": source_type,
            "confidence_threshold": confidence_threshold,
            "road_section": device.road_section or f"{device.name} - {device.city}",
            "chainage_station": device.chainage_station,
        },
        default_confidence=configuration.confidence_threshold,
    )
    if not form.is_valid():
        raise ValueError("Fleet camera analysis settings are invalid.")
    return form


def attach_fleet_source(analysis, device, capture_type):
    route_metadata = dict(analysis.route_metadata or {})
    route_metadata.update(
        {
            "source": "fleet-camera",
            "capture_type": capture_type,
            "fleet_device_id": device.pk,
            "fleet_device_name": device.name,
            "fleet_device_city": device.city,
        }
    )
    analysis.route_metadata = route_metadata
    analysis.save(update_fields=["route_metadata"])
    return analysis


def split_for_position(index, total, train_percent, val_percent):
    if total <= 1:
        return DatasetSplit.TRAIN
    percent = (index / total) * 100
    if percent < train_percent:
        return DatasetSplit.TRAIN
    if percent < train_percent + val_percent:
        return DatasetSplit.VAL
    return DatasetSplit.TEST


def next_balanced_split(train_percent=70, val_percent=20, test_percent=10):
    """Keep cumulative uploads close to the requested split ratios."""
    counts = {
        row["split"]: row["total"]
        for row in DatasetImage.objects.filter(is_archived=False).values("split").annotate(total=Count("id"))
    }
    ratios = {
        DatasetSplit.TRAIN: max(train_percent, 1) / 100,
        DatasetSplit.VAL: max(val_percent, 1) / 100,
        DatasetSplit.TEST: max(test_percent, 1) / 100,
    }
    return min(DatasetSplit.values, key=lambda split: counts.get(split, 0) / ratios[split])


def save_dataset_image_from_bytes(
    data,
    filename,
    metadata,
    split,
    user,
    source=DatasetImageSource.UPLOAD,
    parent=None,
    source_group="",
):
    record = DatasetImage(
        original_filename=filename,
        file_hash=metadata["file_hash"],
        file_size=metadata["file_size"],
        file_type=metadata["file_type"],
        width=metadata["width"],
        height=metadata["height"],
        split=split,
        source=source,
        source_group=source_group,
        parent_image=parent,
        uploaded_by=user,
    )
    safe_stem = get_valid_filename(os.path.splitext(filename)[0] or record.dataset_id)
    extension = metadata.get("extension") or os.path.splitext(filename)[1].lower() or ".jpg"
    record.image.save(f"{record.dataset_id}-{safe_stem}{extension}", ContentFile(data), save=False)
    record.save()
    return record


def auto_label_dataset_images(images, user):
    session = active_video_model()
    readiness = model_readiness(session)
    if not readiness["ready"]:
        raise ValueError("Automatic labeling requires an active validated pothole model. " + " ".join(readiness["errors"]))
    try:
        import cv2
        import numpy as np
        from ultralytics import YOLO
    except Exception as exc:
        raise ValueError("Ultralytics is required for automatic image labeling.") from exc

    model = YOLO(str(resolve_model_artifact(session.model_file)))
    total_annotations = 0
    for record in images:
        record.image.open("rb")
        try:
            with Image.open(record.image) as opened:
                source = opened.convert("RGB").copy()
        finally:
            record.image.close()
        result = model.predict(
            source=source,
            conf=settings.AUTO_LABEL_CONFIDENCE,
            iou=settings.AUTO_LABEL_IOU,
            imgsz=settings.AUTO_TRAIN_IMAGE_SIZE,
            device=settings.AUTO_TRAIN_DEVICE,
            verbose=False,
        )[0]
        source_bgr = cv2.cvtColor(np.asarray(source), cv2.COLOR_RGB2BGR)
        annotations = []
        preview_frame = source_bgr.copy()
        if result.boxes is not None:
            if result.masks is None:
                raise ValueError("The active model returned boxes without instance masks.")
            xywhn = result.boxes.xywhn.cpu().tolist()
            xyxy = result.boxes.xyxy.cpu().tolist()
            confidences = result.boxes.conf.cpu().tolist()
            class_ids = result.boxes.cls.int().cpu().tolist()
            mask_polygons = result.masks.xyn if result.masks is not None else []
            for index, (box, raw_box, confidence, class_id) in enumerate(zip(xywhn, xyxy, confidences, class_ids)):
                class_name = str(result.names.get(class_id, class_id))
                if "pothole" not in class_name.lower():
                    continue
                if index >= len(mask_polygons) or len(mask_polygons[index]) < 3:
                    continue
                polygon = [[round(float(x), 6), round(float(y), 6)] for x, y in mask_polygons[index]]
                mask_pixels = pixel_polygon(polygon, record.width, record.height)
                draw_mask_overlay(preview_frame, mask_pixels, cv2)
                x1, y1, x2, y2 = [int(round(value)) for value in raw_box]
                cv2.rectangle(preview_frame, (x1, y1), (x2, y2), (60, 60, 255), 2)
                label = f"pothole {float(confidence):.2f}"
                (text_width, text_height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.62, 2)
                label_top = max(0, y1 - text_height - baseline - 7)
                cv2.rectangle(preview_frame, (x1, label_top), (x1 + text_width + 8, y1), (60, 60, 255), -1)
                cv2.putText(
                    preview_frame,
                    label,
                    (x1 + 4, y1 - baseline - 3),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.62,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                annotations.append(
                    PotholeAnnotation(
                        image=record,
                        class_id=0,
                        label="pothole",
                        center_x=normalized_decimal(box[0]),
                        center_y=normalized_decimal(box[1]),
                        width=normalized_decimal(box[2]),
                        height=normalized_decimal(box[3]),
                        segmentation_points=polygon,
                        confidence=Decimal(str(round(float(confidence), 4))),
                        source=PotholeAnnotation.Source.PREDICTED,
                        created_by=user,
                    )
                )
        PotholeAnnotation.objects.bulk_create(annotations)
        ok, encoded_preview = cv2.imencode(".jpg", preview_frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            record.labeled_preview.save(
                f"{record.dataset_id}-segmentation.jpg",
                ContentFile(encoded_preview.tobytes()),
                save=False,
            )
        record.status = DatasetImageStatus.FULL
        record.reviewed_by = None
        record.reviewed_at = None
        record.review_notes = (
            f"Automatically labeled by model session {session.pk}; "
            f"{len(annotations)} pothole(s) at confidence >= {settings.AUTO_LABEL_CONFIDENCE:.2f}. "
            "Manual mask review is required before approval."
        )
        record.save(update_fields=["labeled_preview", "status", "reviewed_by", "reviewed_at", "review_notes"])
        total_annotations += len(annotations)
    return total_annotations


def queue_automatic_training(user, dataset_version):
    readiness = dataset_readiness()
    if not readiness["ready"]:
        return None
    manifest = training_dataset_manifest()
    manifest_signature = [
        (
            entry["id"], entry["file_hash"], entry["split"], entry.get("source_group"),
            entry.get("label_sha256"), entry.get("segmentation_annotation_count"),
        )
        for entry in manifest
    ]
    recent_sessions = TrainingSession.objects.filter(
        status__in=[
            TrainingSession.Status.QUEUED,
            TrainingSession.Status.RUNNING,
            TrainingSession.Status.COMPLETE,
        ]
    ).order_by("-created_at")[:10]
    configuration = analyzer_configuration()
    training_model = configuration.training_model or settings.AUTO_TRAIN_MODEL
    for session in recent_sessions:
        existing = [
            (
                entry["id"], entry["file_hash"], entry["split"], entry.get("source_group"),
                entry.get("label_sha256"), entry.get("segmentation_annotation_count"),
            )
            for entry in (session.dataset_manifest or [])
        ]
        if existing == manifest_signature and session.model_name == training_model:
            return None
    return TrainingSession.objects.create(
        model_name=training_model,
        dataset_version=dataset_version,
        epochs=settings.AUTO_TRAIN_EPOCHS,
        batch_size=settings.AUTO_TRAIN_BATCH_SIZE,
        image_size=settings.AUTO_TRAIN_IMAGE_SIZE,
        learning_rate=Decimal("0.001"),
        device=settings.AUTO_TRAIN_DEVICE,
        patience=30,
        workers=2,
        optimizer="AdamW",
        augmentation_profile="balanced",
        seed=42,
        freeze_layers=0,
        dataset_manifest=manifest,
        created_by=user,
    )


def normalized_decimal(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
        return number.quantize(Decimal("0.000001"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Annotation coordinates must be numeric.") from exc


def parse_annotation_payload(payload):
    try:
        raw_boxes = json.loads(payload or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("Annotation data could not be parsed.") from exc
    if not isinstance(raw_boxes, list):
        raise ValueError("Annotation data must be a list of bounding boxes.")
    boxes = []
    for item in raw_boxes:
        if not isinstance(item, dict):
            raise ValueError("Each annotation must be a polygon object.")
        raw_points = item.get("segmentation_points") or []
        if len(raw_points) < 3:
            raise ValueError("Every pothole annotation must include a polygon mask with at least three points.")
        points = []
        for point in raw_points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("Segmentation mask points are invalid.")
            points.append([normalized_decimal(point[0]), normalized_decimal(point[1])])
        if len({(x, y) for x, y in points}) < 3:
            raise ValueError("Segmentation masks require at least three distinct points.")
        polygon_area = abs(
            sum(
                points[index][0] * points[(index + 1) % len(points)][1]
                - points[(index + 1) % len(points)][0] * points[index][1]
                for index in range(len(points))
            )
        ) / 2
        if polygon_area <= Decimal("0.000001"):
            raise ValueError("Segmentation mask area is too small or its points are collinear.")
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        left, right = min(xs), max(xs)
        top, bottom = min(ys), max(ys)
        width = right - left
        height = bottom - top
        cx = left + width / 2
        cy = top + height / 2
        if width <= 0 or height <= 0:
            raise ValueError("Bounding boxes must have positive width and height.")
        if cx - width / 2 < 0 or cy - height / 2 < 0 or cx + width / 2 > 1 or cy + height / 2 > 1:
            raise ValueError("Bounding boxes must stay within image bounds.")
        boxes.append(
            {
                "center_x": cx,
                "center_y": cy,
                "width": width,
                "height": height,
                "segmentation_points": [[float(x), float(y)] for x, y in points],
            }
        )
    return boxes


def annotation_quality_flags(image):
    flags = []
    annotations = list(image.annotations.all())
    if not annotations and image.status != DatasetImageStatus.REJECTED:
        flags.append("missing labels")
    for annotation in annotations:
        cx = annotation.center_x
        cy = annotation.center_y
        width = annotation.width
        height = annotation.height
        if width <= 0 or height <= 0 or cx - width / 2 < 0 or cy - height / 2 < 0 or cx + width / 2 > 1 or cy + height / 2 > 1:
            flags.append("invalid bbox")
            break
        if len(annotation.segmentation_points or []) < 3:
            flags.append("missing mask")
            break
    try:
        with materialized_field_file(image.image) as image_path:
            with Image.open(image_path) as opened:
                opened.verify()
    except Exception:
        flags.append("corrupted image")
    return flags


def box_to_corners(box):
    cx = float(box["center_x"])
    cy = float(box["center_y"])
    width = float(box["width"])
    height = float(box["height"])
    left = cx - width / 2
    right = cx + width / 2
    top = cy - height / 2
    bottom = cy + height / 2
    return [(left, top), (right, top), (right, bottom), (left, bottom)]


def corners_to_box(points):
    xs = [max(0, min(1, point[0])) for point in points]
    ys = [max(0, min(1, point[1])) for point in points]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    width = right - left
    height = bottom - top
    if width <= 0.002 or height <= 0.002:
        return None
    return {
        "center_x": normalized_decimal(left + width / 2),
        "center_y": normalized_decimal(top + height / 2),
        "width": normalized_decimal(width),
        "height": normalized_decimal(height),
    }


def transform_annotation_box(option, box):
    points = box_to_corners(box)
    if option == "hflip":
        return corners_to_box([(1 - x, y) for x, y in points])
    if option == "rotate":
        return corners_to_box([(1 - y, x) for x, y in points])
    if option == "scale":
        margin = 0.055
        return corners_to_box([((x - margin) / (1 - margin * 2), (y - margin) / (1 - margin * 2)) for x, y in points])
    if option == "crop":
        margin = 0.05
        return corners_to_box([((x - margin) / 0.9, (y - margin) / 0.9) for x, y in points])
    return box


def transform_segmentation_points(option, points):
    transformed = [(float(x), float(y)) for x, y in points]
    if option == "hflip":
        transformed = [(1 - x, y) for x, y in transformed]
    elif option == "rotate":
        transformed = [(1 - y, x) for x, y in transformed]
    elif option == "scale":
        margin = 0.055
        transformed = [((x - margin) / (1 - margin * 2), (y - margin) / (1 - margin * 2)) for x, y in transformed]
    elif option == "crop":
        transformed = [((x - 0.05) / 0.9, (y - 0.05) / 0.9) for x, y in transformed]
    transformed = [
        [round(max(0.0, min(1.0, x)), 6), round(max(0.0, min(1.0, y)), 6)]
        for x, y in transformed
    ]
    return transformed if len({tuple(point) for point in transformed}) >= 3 else []


def apply_augmentation_image(image, option):
    source = ImageOps.exif_transpose(image).convert("RGB")
    width, height = source.size
    if option == "hflip":
        return ImageOps.mirror(source)
    if option == "rotate":
        return source.rotate(-90, expand=True)
    if option == "brightness":
        return ImageEnhance.Brightness(source).enhance(1.22)
    if option == "contrast":
        return ImageEnhance.Contrast(source).enhance(1.28)
    if option == "blur":
        return source.filter(ImageFilter.GaussianBlur(radius=1.15))
    if option == "low_light":
        return ImageEnhance.Brightness(ImageEnhance.Contrast(source).enhance(0.9)).enhance(0.58)
    if option == "noise":
        pixels = source.load()
        for _ in range(max(900, width * height // 120)):
            x = random.randrange(width)
            y = random.randrange(height)
            r, g, b = pixels[x, y]
            delta = random.randint(-28, 28)
            pixels[x, y] = (max(0, min(255, r + delta)), max(0, min(255, g + delta)), max(0, min(255, b + delta)))
        return source
    if option == "rain":
        draw = ImageDraw.Draw(source)
        for _ in range(max(60, width // 10)):
            x = random.randrange(width)
            y = random.randrange(height)
            draw.line((x, y, x + 12, y + 26), fill=(205, 220, 235), width=1)
        return ImageEnhance.Brightness(source).enhance(0.88)
    if option == "shadow":
        overlay = Image.new("RGBA", source.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.polygon([(0, height * 0.2), (width * 0.65, 0), (width, height * 0.12), (width * 0.35, height)], fill=(0, 0, 0, 75))
        return Image.alpha_composite(source.convert("RGBA"), overlay).convert("RGB")
    if option == "scale":
        scaled = source.resize((int(width * 1.12), int(height * 1.12)))
        left = (scaled.width - width) // 2
        top = (scaled.height - height) // 2
        return scaled.crop((left, top, left + width, top + height))
    if option == "crop":
        left = int(width * 0.05)
        top = int(height * 0.05)
        cropped = source.crop((left, top, int(width * 0.95), int(height * 0.95)))
        return cropped.resize((width, height))
    return source


def run_augmentation_job(user, selected_options):
    source_images = DatasetImage.objects.filter(
        is_archived=False,
        status__in=[DatasetImageStatus.FULL, DatasetImageStatus.APPROVED],
    ).prefetch_related("annotations")
    job = DatasetAugmentationJob.objects.create(
        source_version=current_dataset_version(),
        options={"enabled": selected_options},
        status="running",
        created_by=user,
    )
    generated = 0
    for source_image in source_images:
        annotations = [
            {
                "center_x": annotation.center_x,
                "center_y": annotation.center_y,
                "width": annotation.width,
                "height": annotation.height,
                "segmentation_points": annotation.segmentation_points,
            }
            for annotation in source_image.annotations.all()
        ]
        if not annotations:
            continue
        with materialized_field_file(source_image.image) as source_path:
            with Image.open(source_path) as opened:
                for option in selected_options:
                    augmented = apply_augmentation_image(opened, option)
                    next_boxes = []
                    for box in annotations:
                        points = transform_segmentation_points(option, box["segmentation_points"])
                        if len(points) < 3:
                            continue
                        transformed = transform_annotation_box(option, box)
                        if transformed:
                            transformed["segmentation_points"] = points
                            next_boxes.append(transformed)
                    if not next_boxes:
                        continue
                    buffer = io.BytesIO()
                    augmented.save(buffer, format="JPEG", quality=92)
                    data = buffer.getvalue()
                    metadata = {
                        "file_hash": hashlib.sha256(data).hexdigest(),
                        "file_size": len(data),
                        "file_type": "jpeg",
                        "width": augmented.width,
                        "height": augmented.height,
                        "extension": ".jpg",
                    }
                    if DatasetImage.objects.filter(file_hash=metadata["file_hash"]).exists():
                        continue
                    record = save_dataset_image_from_bytes(
                        data,
                        f"{source_image.dataset_id}-{option}.jpg",
                        metadata,
                        source_image.split,
                        user,
                        source=DatasetImageSource.AUGMENTED,
                        parent=source_image,
                        source_group=source_image.source_group or f"image-{source_image.pk}",
                    )
                    record.status = DatasetImageStatus.FULL
                    record.save(update_fields=["status"])
                    PotholeAnnotation.objects.bulk_create(
                        [
                            PotholeAnnotation(
                                image=record,
                                source=PotholeAnnotation.Source.AUGMENTED,
                                created_by=user,
                                **box,
                            )
                            for box in next_boxes
                        ]
                    )
                    generated += 1
                    audit_dataset(user, "augment", f"Generated augmented image {record.dataset_id}.", dataset_image=record)
    version = create_dataset_version(user, notes=f"Augmentation job {job.pk}")
    DatasetImage.objects.filter(dataset_version__isnull=True).update(dataset_version=version)
    job.generated_count = generated
    job.status = "complete"
    job.save(update_fields=["generated_count", "status"])
    return job


def run_optional_detection(test):
    session = test.model_session
    model_path = resolve_model_artifact(session.model_file) if session and session.model_file else None
    if not model_path or not model_path.is_file():
        test.status = "pending"
        test.save(update_fields=["status"])
        return "No trained model file is available yet. Test image was saved for later inference."
    try:
        import cv2
        from ultralytics import YOLO
    except Exception:
        test.status = "pending"
        test.save(update_fields=["status"])
        return "Ultralytics is not installed. Test image was saved for later inference."
    started = time.perf_counter()
    model = YOLO(str(model_path))
    if model.task not in {"segment", "detect"} or (model.task == "detect" and not settings.ALLOW_DETECTION_MODE):
        test.status = "failed"
        test.save(update_fields=["status"])
        return "Detection blocked: this model task is not enabled."
    is_segmentation = model.task == "segment"
    detections = []
    with materialized_field_file(test.image) as test_image_path:
        result = model.predict(
            source=str(test_image_path),
            conf=test.confidence_threshold / 100,
            iou=test.iou_threshold / 100,
            verbose=False,
        )[0]
        frame = cv2.imread(str(test_image_path))
    if frame is None:
        test.status = "failed"
        test.save(update_fields=["status"])
        return "Detection failed: the uploaded image could not be decoded."
    if result.boxes is not None:
        if result.masks is None and is_segmentation:
            test.status = "failed"
            test.save(update_fields=["status"])
            return "Detection failed: the segmentation model returned boxes without masks."
        xywhn = result.boxes.xywhn.cpu().tolist()
        xyxy = result.boxes.xyxy.cpu().tolist()
        confidences = result.boxes.conf.cpu().tolist()
        class_ids = result.boxes.cls.int().cpu().tolist()
        mask_polygons = result.masks.xyn if result.masks is not None else []
        for index, (box, raw_box, confidence, class_id) in enumerate(zip(xywhn, xyxy, confidences, class_ids)):
            class_name = str(result.names.get(class_id, class_id))
            if "pothole" not in class_name.lower():
                continue
            x1, y1, x2, y2 = [int(round(value)) for value in raw_box]
            polygon = (
                [[round(float(x), 6), round(float(y), 6)] for x, y in mask_polygons[index]]
                if index < len(mask_polygons) and len(mask_polygons[index]) >= 3
                else []
            )
            if not polygon and not is_segmentation and settings.DETECTION_MASK_REFINEMENT:
                mask_pixels = estimate_detection_mask(
                    frame,
                    (x1, y1, x2, y2),
                    cv2,
                    settings.DETECTION_MASK_MAX_SIZE,
                )
                polygon = [
                    [round(float(x) / frame.shape[1], 6), round(float(y) / frame.shape[0], 6)]
                    for x, y in mask_pixels
                ]
            if not polygon and is_segmentation:
                continue
            if polygon:
                mask_pixels = pixel_polygon(polygon, frame.shape[1], frame.shape[0])
                draw_mask_overlay(
                    frame,
                    mask_pixels,
                    cv2,
                    color=(255, 0, 255) if is_segmentation else (0, 165, 255),
                )
            cv2.rectangle(frame, (x1, y1), (x2, y2), (60, 60, 255), 2)
            label = f"pothole {confidence:.2f}"
            (text_width, text_height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.62, 2)
            label_top = max(0, y1 - text_height - baseline - 7)
            cv2.rectangle(frame, (x1, label_top), (x1 + text_width + 8, y1), (60, 60, 255), -1)
            cv2.putText(frame, label, (x1 + 4, y1 - baseline - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2, cv2.LINE_AA)
            detections.append(
                {
                    "class_id": 0,
                    "label": "pothole",
                    "center_x": round(float(box[0]), 6),
                    "center_y": round(float(box[1]), 6),
                    "width": round(float(box[2]), 6),
                    "height": round(float(box[3]), 6),
                    "confidence": round(float(confidence), 4),
                    "segmentation_points": polygon,
                    "mask_source": "model" if is_segmentation and polygon else ("estimated" if polygon else "none"),
                }
            )
    result_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    buffer = io.BytesIO()
    result_image.save(buffer, format="JPEG", quality=92)
    test.result_image.save(f"test-{test.pk}-result.jpg", ContentFile(buffer.getvalue()), save=False)
    test.detections = detections
    test.detection_count = len(detections)
    test.processing_time_ms = int((time.perf_counter() - started) * 1000)
    test.status = "complete"
    test.save(update_fields=["result_image", "detections", "detection_count", "processing_time_ms", "status"])
    return f"Detection complete with {test.detection_count} potholes."


def mark_analysis_failed(analysis, error):
    sample = analysis.dataset_sample
    analysis.status = VideoAnalysis.Status.FAILED
    analysis.model_version = "opencv-unavailable"
    analysis.frames_processed = 0
    analysis.inference_fps = 0
    analysis.duration_seconds = sample.duration_seconds if sample else 0
    analysis.analyzed_at = timezone.now()
    analysis.save(
        update_fields=[
            "status",
            "model_version",
            "frames_processed",
            "inference_fps",
            "duration_seconds",
            "analyzed_at",
        ]
    )
    analysis.events.all().delete()
    EngineeringRecommendation.objects.filter(analysis=analysis).delete()
    return f"Analysis failed: {error}"


def get_engineering_recommendation(analysis):
    if not analysis:
        return None
    try:
        return analysis.engineering_recommendation
    except EngineeringRecommendation.DoesNotExist:
        return None


@staff_required
def video_analyzer(request):
    if request.method == "POST":
        form = VideoAnalysisForm(request.POST, request.FILES)
        if form.is_valid():
            analysis = form.save(request.user)
            try:
                run_analysis(analysis)
            except Exception as exc:
                messages.error(request, mark_analysis_failed(analysis, exc))
            else:
                messages.success(request, f"Analysis complete with {analysis.events.count()} detections.")
            return redirect("admin_video_analyzer_explicit")
    else:
        form = VideoAnalysisForm(
            initial={
                "min_confidence": 85,
            }
        )

    latest_analysis = VideoAnalysis.objects.prefetch_related("events").select_related("engineering_recommendation").first()
    detections = list(latest_analysis.events.order_by("timecode_seconds", "-confidence")) if latest_analysis else []
    engineering_recommendation = get_engineering_recommendation(latest_analysis)
    detections_json = [
        {
            "event_code": event.event_code,
            "road_name": event.road_name,
            "timecode_seconds": float(event.timecode_seconds),
            "severity": event.severity,
            "confidence": event.confidence,
            "bbox_x": event.bbox_x,
            "bbox_y": event.bbox_y,
            "bbox_w": event.bbox_w,
            "bbox_h": event.bbox_h,
            "damage_length_m": float(event.damage_length_m),
            "damage_width_m": float(event.damage_width_m),
            "damage_perimeter_m": float(event.damage_perimeter_m),
            "damage_surface_area_sqm": float(event.damage_surface_area_sqm),
            "estimated_repair_area_sqm": float(event.estimated_repair_area_sqm),
            "snapshot_url": event.snapshot_image.url if event.snapshot_image else "",
        }
        for event in detections
    ]
    context = admin_context(request, "video_analyzer") | {
        "form": form,
        "analysis": latest_analysis,
        "detections": detections,
        "detections_json": detections_json,
        "engineering_recommendation": engineering_recommendation,
    }
    return render(request, "console/video_analyzer.html", context)


@staff_required
def video_analysis_csv(request, analysis_id):
    analysis = get_object_or_404(VideoAnalysis, pk=analysis_id)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="roadvision-analysis-{analysis.pk}.csv"'
    writer = csv.writer(response)
    recommendation = get_engineering_recommendation(analysis)
    if recommendation:
        writer.writerow(["road_section", analysis.road_name or recommendation.road_name])
        writer.writerow(["barangay", analysis.barangay])
        writer.writerow(["city", analysis.city])
        writer.writerow(["route_start", analysis.route_start])
        writer.writerow(["route_end", analysis.route_end])
        writer.writerow(["chainage_station", analysis.chainage_station or recommendation.chainage_station])
        writer.writerow([])
        writer.writerow(["engineering_priority", recommendation.priority])
        writer.writerow(["recommended_action", recommendation.recommended_action])
        writer.writerow(["repair_method", recommendation.repair_method])
        writer.writerow(["response_window", recommendation.response_window])
        writer.writerow(["estimated_length_m", recommendation.estimated_length_m])
        writer.writerow(["estimated_width_m", recommendation.estimated_width_m])
        writer.writerow(["estimated_perimeter_m", recommendation.estimated_perimeter_m])
        writer.writerow(["estimated_depth_mm", recommendation.estimated_depth_mm])
        writer.writerow(["estimated_surface_area_sqm", recommendation.estimated_affected_area_sqm])
        writer.writerow(["estimated_repair_area_sqm", recommendation.estimated_repair_area_sqm])
        writer.writerow(["estimated_material_volume_cum", recommendation.estimated_material_volume_cum])
        writer.writerow(["road_name", recommendation.road_name])
        writer.writerow(["latitude", recommendation.latitude or "field capture required"])
        writer.writerow(["longitude", recommendation.longitude or "field capture required"])
        writer.writerow(["chainage_station", recommendation.chainage_station or "field capture required"])
        writer.writerow(["photo_before_count", recommendation.photo_before_count])
        writer.writerow(["photo_during_count", recommendation.photo_during_count])
        writer.writerow(["photo_after_count", recommendation.photo_after_count])
        writer.writerow(["lanes_affected", recommendation.lanes_affected])
        writer.writerow(["traffic_volume_note", recommendation.traffic_volume_note])
        writer.writerow(["work_zone_requirements", recommendation.work_zone_requirements])
        writer.writerow(["weather_constraints", recommendation.weather_constraints])
        writer.writerow(["pavement_temperature_note", recommendation.pavement_temperature_note])
        writer.writerow(["asphalt_quantity_tons", recommendation.asphalt_quantity_tons])
        writer.writerow(["aggregate_quantity_tons", recommendation.aggregate_quantity_tons])
        writer.writerow(["equipment_hours", recommendation.equipment_hours])
        writer.writerow(["fuel_liters", recommendation.fuel_liters])
        writer.writerow(["labor_hours", recommendation.labor_hours])
        writer.writerow(["traffic_control_cost", recommendation.traffic_control_cost])
        writer.writerow(["estimated_cost_min", recommendation.estimated_cost_min])
        writer.writerow(["estimated_cost_max", recommendation.estimated_cost_max])
        writer.writerow([])
    writer.writerow([
        "event_code",
        "road_name",
        "timecode_seconds",
        "severity",
        "confidence",
        "bbox_x",
        "bbox_y",
        "bbox_w",
        "bbox_h",
        "damage_length_m",
        "damage_width_m",
        "damage_perimeter_m",
        "damage_surface_area_sqm",
        "estimated_repair_area_sqm",
        "snapshot",
    ])
    for event in analysis.events.all():
        writer.writerow([
            event.event_code,
            event.road_name,
            event.timecode_seconds,
            event.severity,
            event.confidence,
            event.bbox_x,
            event.bbox_y,
            event.bbox_w,
            event.bbox_h,
            event.damage_length_m,
            event.damage_width_m,
            event.damage_perimeter_m,
            event.damage_surface_area_sqm,
            event.estimated_repair_area_sqm,
            event.snapshot_image.url if event.snapshot_image else "",
        ])
    return response


@staff_required
def clear_video_analyses(request):
    if request.method == "POST":
        VideoAnalysis.objects.all().delete()
    return redirect("admin_video_analyzer_explicit")


@staff_required
def training_dataset_module(request):
    tab = request.GET.get("tab", "upload")
    if tab not in {"upload", "history", "overview", "annotate", "review", "augmentation", "train", "test", "export"}:
        tab = "upload"
    upload_form = DatasetUploadForm()
    training_form = TrainingConfigForm()
    detection_form = DetectionTestForm()

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "upload_images":
            upload_form = DatasetUploadForm(request.POST)
            files = request.FILES.getlist("images")
            if upload_form.is_valid():
                if not files:
                    messages.error(request, "Select one or more road images to upload.")
                    return redirect(f"{request.path}?tab=upload")
                label_readiness = model_readiness(active_video_model())
                can_auto_label = bool(
                    label_readiness["ready"]
                    and label_readiness["session"]
                    and label_readiness["session"].model_task == "segment"
                )
                validated = []
                skipped = []
                for upload in files:
                    try:
                        metadata = read_uploaded_image(upload)
                    except Exception as exc:
                        skipped.append(f"{upload.name}: {exc}")
                        continue
                    if DatasetImage.objects.filter(file_hash=metadata["file_hash"]).exists():
                        skipped.append(f"{upload.name}: duplicate image skipped")
                        continue
                    validated.append((upload.name, metadata))
                created = []
                for filename, metadata in validated:
                    source_group = (
                        upload_form.cleaned_data.get("source_group")
                        or f"upload-{metadata['file_hash'][:16]}"
                    )
                    existing_group = DatasetImage.objects.filter(
                        is_archived=False,
                        source_group=source_group,
                    ).values_list("split", flat=True).first()
                    split = existing_group or next_balanced_split(
                        upload_form.cleaned_data["train_percent"],
                        upload_form.cleaned_data["val_percent"],
                        upload_form.cleaned_data["test_percent"],
                    )
                    created.append(
                        save_dataset_image_from_bytes(
                            metadata["data"],
                            filename,
                            metadata,
                            split,
                            request.user,
                            source_group=source_group,
                        )
                    )
                if created:
                    annotation_count = 0
                    auto_label_error = ""
                    if can_auto_label:
                        try:
                            annotation_count = auto_label_dataset_images(created, request.user)
                        except ValueError as exc:
                            auto_label_error = str(exc)
                    version = create_dataset_version(
                        request.user,
                        notes=upload_form.cleaned_data.get("notes") or (
                            "Automatically proposed mask upload" if can_auto_label and not auto_label_error
                            else "Manual mask annotation upload"
                        ),
                        train_percent=upload_form.cleaned_data["train_percent"],
                        val_percent=upload_form.cleaned_data["val_percent"],
                        test_percent=upload_form.cleaned_data["test_percent"],
                    )
                    DatasetImage.objects.filter(pk__in=[image.pk for image in created]).update(dataset_version=version)
                    for image in created:
                        action_name = "auto-label" if can_auto_label and not auto_label_error else "upload"
                        audit_dataset(request.user, action_name, f"Uploaded {image.dataset_id} for mask review.", image, version)
                    session = queue_automatic_training(request.user, version)
                    if session and session.status == TrainingSession.Status.QUEUED:
                        audit_dataset(
                            request.user,
                            "auto-train",
                            f"Automatically queued {session.model_name} training session {session.pk}.",
                            dataset_version=version,
                        )
                        training_message = f" Training session {session.pk} was queued automatically."
                    elif dataset_readiness()["ready"]:
                        training_message = " This exact dataset already has a training session."
                    else:
                        training_message = " More images are needed before automatic training can start."
                    messages.success(
                        request,
                        f"Uploaded {len(created)} images with {annotation_count} proposed pothole masks "
                        f"in dataset v{version.version_number}. Manual mask review is required.{training_message}",
                    )
                    if not can_auto_label:
                        messages.info(request, "No production segmentation model is active; draw masks manually to bootstrap the dataset.")
                    elif auto_label_error:
                        messages.error(request, f"Automatic mask proposal failed; the images were retained for manual annotation. {auto_label_error}")
                if skipped:
                    messages.error(request, "Skipped files: " + " | ".join(skipped[:5]))
                return redirect(f"{request.path}?tab=upload")
            messages.error(request, "Dataset split settings are invalid.")
            return redirect(f"{request.path}?tab=upload")

        if action == "save_annotations":
            image = get_object_or_404(DatasetImage, pk=request.POST.get("image_id"), is_archived=False)
            try:
                boxes = parse_annotation_payload(request.POST.get("annotations_json", "[]"))
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect(f"{request.path}?tab=annotate&image={image.pk}")
            image.annotations.all().delete()
            PotholeAnnotation.objects.bulk_create(
                [
                    PotholeAnnotation(
                        image=image,
                        center_x=box["center_x"],
                        center_y=box["center_y"],
                        width=box["width"],
                        height=box["height"],
                        segmentation_points=box["segmentation_points"],
                        created_by=request.user,
                    )
                    for box in boxes
                ]
            )
            image.status = DatasetImageStatus.FULL if boxes else DatasetImageStatus.UNANNOTATED
            if image.labeled_preview:
                image.labeled_preview.delete(save=False)
                image.labeled_preview = ""
            version = create_dataset_version(request.user, notes=f"Annotations updated for {image.dataset_id}")
            image.dataset_version = version
            image.save(update_fields=["status", "dataset_version", "labeled_preview"])
            audit_dataset(request.user, "annotate", f"Saved {len(boxes)} pothole annotations for {image.dataset_id}.", image, version)
            messages.success(request, f"Saved {len(boxes)} YOLO annotations for {image.dataset_id}.")
            return redirect(f"{request.path}?tab=annotate&image={image.pk}")

        if action == "move_source_group":
            source_group = request.POST.get("source_group", "").strip()
            split = request.POST.get("split", "")
            if not source_group or split not in DatasetSplit.values:
                messages.error(request, "Select a valid source group and destination split.")
                return redirect(f"{request.path}?tab=review")
            snapshot_in_use = any(
                any(entry.get("source_group") == source_group for entry in (session.dataset_manifest or []))
                for session in TrainingSession.objects.filter(
                    status__in=[TrainingSession.Status.QUEUED, TrainingSession.Status.RUNNING]
                ).only("dataset_manifest")
            )
            if snapshot_in_use:
                messages.error(request, "This group is frozen in a queued or running training session and cannot be moved.")
                return redirect(f"{request.path}?tab=review")
            group_images = DatasetImage.objects.filter(is_archived=False, source_group=source_group)
            if not group_images.exists():
                messages.error(request, "The selected source group no longer exists.")
                return redirect(f"{request.path}?tab=review")
            moved = group_images.exclude(split=split).update(split=split)
            version = create_dataset_version(request.user, notes=f"Moved source group {source_group} to {split}")
            group_images.update(dataset_version=version)
            audit_dataset(
                request.user,
                "group-split",
                f"Moved {moved} images in source group {source_group} to {split}.",
                dataset_version=version,
            )
            messages.success(request, f"Source group {source_group} is now entirely in {split} ({group_images.count()} images).")
            return redirect(f"{request.path}?tab=review")

        if action == "review_image":
            image = get_object_or_404(DatasetImage, pk=request.POST.get("image_id"), is_archived=False)
            status = request.POST.get("status")
            if status not in DatasetImageStatus.values:
                messages.error(request, "Invalid review status.")
                return redirect(f"{request.path}?tab=review")
            if status == DatasetImageStatus.APPROVED:
                invalid_masks = [
                    annotation.pk
                    for annotation in image.annotations.all()
                    if len(annotation.segmentation_points or []) < 3
                ]
                if invalid_masks:
                    messages.error(
                        request,
                        "Approval blocked: every positive annotation must contain a reviewed segmentation mask.",
                    )
                    return redirect(f"{request.path}?tab=review")
                leaking_split = (
                    DatasetImage.objects.filter(
                        is_archived=False,
                        status=DatasetImageStatus.APPROVED,
                        source_group=image.source_group,
                    )
                    .exclude(pk=image.pk)
                    .exclude(split=image.split)
                    .exists()
                )
                if image.source_group and leaking_split:
                    messages.error(request, "Approval blocked: this source group already exists in another split.")
                    return redirect(f"{request.path}?tab=review")
            image.status = status
            image.review_notes = request.POST.get("review_notes", "").strip()
            if status in {DatasetImageStatus.APPROVED, DatasetImageStatus.REJECTED}:
                image.reviewed_by = request.user
                image.reviewed_at = timezone.now()
            version = create_dataset_version(request.user, notes=f"Reviewed {image.dataset_id}")
            image.dataset_version = version
            image.save(update_fields=["status", "review_notes", "reviewed_by", "reviewed_at", "dataset_version"])
            audit_dataset(request.user, "review", f"Marked {image.dataset_id} as {status}.", image, version)
            session = queue_automatic_training(request.user, version) if status == DatasetImageStatus.APPROVED else None
            suffix = f" Training session {session.pk} was queued." if session else ""
            messages.success(request, f"{image.dataset_id} marked as {status}.{suffix}")
            return redirect(f"{request.path}?tab=review")

        if action == "archive_image":
            image = get_object_or_404(DatasetImage, pk=request.POST.get("image_id"), is_archived=False)
            image.is_archived = True
            image.save(update_fields=["is_archived"])
            version = create_dataset_version(request.user, notes=f"Archived {image.dataset_id}")
            audit_dataset(request.user, "archive", f"Archived {image.dataset_id}.", image, version)
            messages.success(request, f"{image.dataset_id} archived.")
            return redirect(f"{request.path}?tab=review")

        if action == "run_augmentation":
            allowed = {"hflip", "rotate", "brightness", "contrast", "scale", "crop", "blur", "noise", "rain", "shadow", "low_light"}
            options = [option for option in request.POST.getlist("augmentations") if option in allowed]
            if not options:
                messages.error(request, "Select at least one augmentation option.")
                return redirect(f"{request.path}?tab=augmentation")
            job = run_augmentation_job(request.user, options)
            messages.success(request, f"Augmentation complete. Generated {job.generated_count} images.")
            return redirect(f"{request.path}?tab=augmentation")

        if action == "start_training":
            training_form = TrainingConfigForm(request.POST)
            version = current_dataset_version()
            if training_form.is_valid() and version:
                readiness = dataset_readiness()
                if not readiness["ready"]:
                    messages.error(request, "Training blocked: " + "; ".join(readiness["errors"]))
                    return redirect(f"{request.path}?tab=train")
                session = training_form.save(request.user, version)
                audit_dataset(request.user, "train", f"Queued {session.model_name} training on dataset v{version.version_number}.", dataset_version=version)
                messages.success(request, f"{session.get_model_name_display() if hasattr(session, 'get_model_name_display') else session.model_name} training session queued.")
                return redirect(f"{request.path}?tab=history")
            messages.error(request, "Training configuration is invalid or no dataset version exists.")
            return redirect(f"{request.path}?tab=train")

        if action == "test_detection":
            detection_form = DetectionTestForm(request.POST, request.FILES)
            if detection_form.is_valid():
                upload = request.FILES.get("test_image")
                if not upload:
                    messages.error(request, "Upload an image to test detection.")
                    return redirect(f"{request.path}?tab=test")
                try:
                    metadata = read_uploaded_image(upload)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect(f"{request.path}?tab=test")
                test = DetectionTest(
                    original_filename=upload.name,
                    model_session=active_video_model(),
                    confidence_threshold=detection_form.cleaned_data["confidence_threshold"],
                    iou_threshold=detection_form.cleaned_data["iou_threshold"],
                    created_by=request.user,
                )
                safe_name = get_valid_filename(upload.name)
                test.image.save(f"test-{timezone.now():%Y%m%d%H%M%S}-{safe_name}", ContentFile(metadata["data"]), save=False)
                test.save()
                message = run_optional_detection(test)
                audit_dataset(request.user, "test", f"Created detection test {test.pk}.")
                messages.success(request, message)
                return redirect(f"{request.path}?tab=test")
            messages.error(request, "Detection test settings are invalid.")
            return redirect(f"{request.path}?tab=test")

        if action == "add_detection_to_dataset":
            test = get_object_or_404(DetectionTest, pk=request.POST.get("test_id"))
            test.image.open("rb")
            try:
                data = test.image.read()
            finally:
                test.image.close()
            metadata = {
                "file_hash": hashlib.sha256(data).hexdigest(),
                "file_size": len(data),
                "file_type": os.path.splitext(test.original_filename)[1].lstrip(".").lower() or "jpg",
                "width": 0,
                "height": 0,
                "extension": os.path.splitext(test.original_filename)[1].lower() or ".jpg",
            }
            with Image.open(io.BytesIO(data)) as opened:
                metadata["width"], metadata["height"] = opened.size
            if DatasetImage.objects.filter(file_hash=metadata["file_hash"]).exists():
                messages.error(request, "This detection image already exists in the dataset.")
                return redirect(f"{request.path}?tab=test")
            record = save_dataset_image_from_bytes(
                data,
                test.original_filename,
                metadata,
                DatasetSplit.TRAIN,
                request.user,
                source=DatasetImageSource.DETECTION,
                source_group=f"detection-test-{test.pk}",
            )
            record.status = DatasetImageStatus.PARTIAL if test.detections else DatasetImageStatus.UNANNOTATED
            record.save(update_fields=["status"])
            for detection in test.detections:
                try:
                    box = parse_annotation_payload(json.dumps([detection]))[0]
                except ValueError:
                    continue
                PotholeAnnotation.objects.create(
                    image=record,
                    center_x=box["center_x"],
                    center_y=box["center_y"],
                    width=box["width"],
                    height=box["height"],
                    segmentation_points=detection.get("segmentation_points") or [],
                    confidence=Decimal(str(detection.get("confidence"))) if detection.get("confidence") is not None else None,
                    source=PotholeAnnotation.Source.PREDICTED,
                    created_by=request.user,
                )
            version = create_dataset_version(request.user, notes=f"Detection feedback {record.dataset_id}")
            record.dataset_version = version
            record.save(update_fields=["dataset_version"])
            audit_dataset(request.user, "feedback", f"Added detection test {test.pk} as {record.dataset_id}.", record, version)
            messages.success(request, "Detection image added to the dataset for annotation review.")
            return redirect(f"{request.path}?tab=annotate&image={record.pk}")

        return redirect(request.path)

    images = DatasetImage.objects.filter(is_archived=False).prefetch_related("annotations", "dataset_version")
    selected_image = None
    selected_id = request.GET.get("image")
    if selected_id:
        selected_image = images.filter(pk=selected_id).first()
    if not selected_image:
        selected_image = images.exclude(status=DatasetImageStatus.APPROVED).order_by("status", "-uploaded_at").first() or images.first()
    selected_annotations = []
    if selected_image:
        selected_annotations = [
            {
                "center_x": float(annotation.center_x),
                "center_y": float(annotation.center_y),
                "width": float(annotation.width),
                "height": float(annotation.height),
                "segmentation_points": annotation.segmentation_points,
            }
            for annotation in selected_image.annotations.all()
        ]
    reviewed_images = []
    for image in images[:80]:
        reviewed_images.append({"image": image, "quality_flags": annotation_quality_flags(image)})

    context = admin_context(request, "training_dataset") | {
        "module_title": "YOLO11 Training Dataset",
        "tab": tab,
        "upload_form": upload_form,
        "training_form": training_form,
        "detection_form": detection_form,
        "summary": dataset_summary(),
        "images": images,
        "selected_image": selected_image,
        "selected_annotations": selected_annotations,
        "reviewed_images": reviewed_images,
        "versions": DatasetVersion.objects.all()[:12],
        "augmentation_jobs": DatasetAugmentationJob.objects.all()[:8],
        "training_sessions": TrainingSession.objects.select_related("dataset_version").all()[:10],
        "detection_tests": DetectionTest.objects.select_related("model_session").all()[:8],
        "audit_logs": DatasetAuditLog.objects.select_related("dataset_image", "dataset_version", "created_by").all()[:20],
        "status_counts": images.values("status").annotate(total=Count("id")).order_by("status"),
        "split_counts": images.values("split").annotate(total=Count("id")).order_by("split"),
        "source_groups": images.exclude(source_group="").values("source_group").annotate(
            total=Count("id"),
            split_count=Count("split", distinct=True),
            current_split=Min("split"),
        ).order_by("source_group"),
        "dataset_readiness": dataset_readiness(),
        "model_readiness": model_readiness(),
    }
    return render(request, "console/training_dataset.html", context)


@staff_required
def yolo_dataset_export(request):
    export_images = DatasetImage.objects.filter(
        is_archived=False,
        status=DatasetImageStatus.APPROVED,
    ).prefetch_related("annotations")
    if not export_images.exists():
        messages.error(request, "Approve annotated images before exporting a YOLO11 dataset.")
        return redirect("admin_training_dataset")
    invalid_masks = [
        image.dataset_id
        for image in export_images
        if any(len(annotation.segmentation_points or []) < 3 for annotation in image.annotations.all())
    ]
    if invalid_masks:
        messages.error(
            request,
            "Export blocked because approved images contain box-only annotations: " + ", ".join(invalid_masks[:10]),
        )
        return redirect("admin_training_dataset")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "dataset/data.yaml",
            "\n".join(
                [
                    "path: dataset",
                    "train: images/train",
                    "val: images/val",
                    "test: images/test",
                    "names:",
                    "  0: pothole",
                    "",
                ]
            ),
        )
        manifest_rows = [["dataset_id", "filename", "split", "width", "height", "annotations"]]
        for image in export_images:
            extension = os.path.splitext(image.original_filename)[1].lower() or ".jpg"
            image_name = f"{image.dataset_id}{extension}"
            image_path = f"dataset/images/{image.split}/{image_name}"
            label_path = f"dataset/labels/{image.split}/{image.dataset_id}.txt"
            with image.image.open("rb") as source:
                archive.writestr(image_path, source.read())
            label_lines = [annotation.yolo_line for annotation in image.annotations.all()]
            archive.writestr(label_path, "\n".join(label_lines) + ("\n" if label_lines else ""))
            manifest_rows.append([image.dataset_id, image.original_filename, image.split, image.width, image.height, len(label_lines)])
        manifest = io.StringIO()
        writer = csv.writer(manifest)
        writer.writerows(manifest_rows)
        archive.writestr("dataset/manifest.csv", manifest.getvalue())

    audit_dataset(request.user, "export", f"Exported {export_images.count()} approved images as YOLO11 ZIP.")
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="roadvision-yolo11-pothole-dataset.zip"'
    return response


@staff_required
def video_visualizer(request):
    configuration = analyzer_configuration()
    upload_form = VideoVisualizerUploadForm(default_confidence=configuration.confidence_threshold)

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "stop_continuous_analysis":
            analysis = get_object_or_404(VideoVisualizerAnalysis, pk=request.POST.get("analysis_id"))
            if request_continuous_analysis_stop(analysis):
                messages.success(request, "Continuous detection is stopping. Live tracks and snapshots are being finalized.")
            else:
                messages.info(request, "This continuous analysis is already stopped.")
            return redirect(f"{request.path}?analysis={analysis.pk}")

        if action == "cancel_analysis":
            analysis = get_object_or_404(VideoVisualizerAnalysis, pk=request.POST.get("analysis_id"))
            if analysis.status in {VideoVisualizerStatus.QUEUED, VideoVisualizerStatus.RETRYING, VideoVisualizerStatus.RUNNING}:
                analysis.status = VideoVisualizerStatus.CANCELLED
                analysis.worker_id = ""
                analysis.lease_expires_at = None
                analysis.finished_at = timezone.now()
                analysis.save(update_fields=["status", "worker_id", "lease_expires_at", "finished_at"])
                messages.success(request, "Video analysis cancelled.")
            return redirect(f"{request.path}?analysis={analysis.pk}")

        if action == "retry_analysis":
            analysis = get_object_or_404(VideoVisualizerAnalysis, pk=request.POST.get("analysis_id"))
            readiness = model_readiness(analysis.model_session)
            if not readiness["ready"]:
                messages.error(request, "Retry blocked: " + " ".join(readiness["errors"]))
            elif analysis.status in {VideoVisualizerStatus.FAILED, VideoVisualizerStatus.CANCELLED}:
                analysis.status = VideoVisualizerStatus.QUEUED
                if analysis.source_type == VideoSourceType.LIVE_STREAM and analysis.source_url:
                    analysis.is_continuous = True
                    analysis.mode = VideoVisualizerMode.REAL_TIME
                analysis.attempt_count = 0
                analysis.error_message = ""
                analysis.finished_at = None
                analysis.stop_requested = False
                analysis.save(
                    update_fields=[
                        "status", "is_continuous", "mode", "attempt_count", "error_message",
                        "finished_at", "stop_requested",
                    ]
                )
                ensure_analysis_worker(analysis)
                messages.success(request, "Video analysis queued for retry.")
            return redirect(f"{request.path}?analysis={analysis.pk}")

        if action == "restart_analysis":
            analysis = get_object_or_404(
                VideoVisualizerAnalysis.objects.select_related("model_session"),
                pk=request.POST.get("analysis_id"),
            )
            if analysis.status not in {
                VideoVisualizerStatus.COMPLETE,
                VideoVisualizerStatus.FAILED,
                VideoVisualizerStatus.CANCELLED,
            }:
                messages.error(request, "Wait for the current analysis to finish or cancel it before restarting.")
            elif not (analysis.video or analysis.source_url):
                messages.error(request, "Restart blocked because the original video source is unavailable.")
            else:
                readiness = model_readiness(configuration.model_session)
                if not readiness["ready"]:
                    messages.error(request, "Restart blocked: " + " ".join(readiness["errors"]))
                else:
                    apply_current_analyzer_settings(analysis, configuration)
                    reset_visualizer_analysis(analysis)
                    audit_dataset(
                        request.user,
                        "video-restart",
                        f"Restarted video visualizer analysis {analysis.pk} with current analyzer settings.",
                    )
                    ensure_analysis_worker(analysis)
                    messages.success(
                        request,
                        "Pothole analysis restarted with the latest active model and current analyzer settings.",
                    )
            return redirect(f"{request.path}?analysis={analysis.pk}")

        if action == "reanalyze_with_sensitivity":
            analysis = get_object_or_404(VideoVisualizerAnalysis, pk=request.POST.get("analysis_id"))
            try:
                confidence_threshold = int(request.POST.get("confidence_threshold", "35"))
            except (TypeError, ValueError):
                confidence_threshold = 35
            if confidence_threshold not in {25, 35}:
                messages.error(request, "Select a supported higher-sensitivity level.")
            elif analysis.status not in {
                VideoVisualizerStatus.COMPLETE,
                VideoVisualizerStatus.FAILED,
                VideoVisualizerStatus.CANCELLED,
            }:
                messages.error(request, "Wait for the current analysis to finish before reanalyzing it.")
            elif analysis.total_detections:
                messages.error(request, "Higher-sensitivity reanalysis is available only when no detections were produced.")
            else:
                readiness = model_readiness(analysis.model_session)
                if not readiness["ready"]:
                    messages.error(request, "Reanalysis blocked: " + " ".join(readiness["errors"]))
                else:
                    reset_visualizer_analysis(analysis, confidence_threshold=confidence_threshold)
                    ensure_analysis_worker(analysis)
                    messages.success(
                        request,
                        f"Reanalysis queued at {confidence_threshold}% confidence. Review the results for false positives.",
                    )
            return redirect(f"{request.path}?analysis={analysis.pk}")

        if action == "reanalyze_with_estimated_masks":
            analysis = get_object_or_404(VideoVisualizerAnalysis.objects.select_related("model_session"), pk=request.POST.get("analysis_id"))
            if not settings.DETECTION_MASK_REFINEMENT:
                messages.error(request, "Estimated detection-mask refinement is disabled.")
            elif not analysis.model_session or analysis.model_session.model_task != "detect":
                messages.error(request, "Estimated-mask reanalysis is available only for detection models.")
            elif analysis.status not in {
                VideoVisualizerStatus.COMPLETE,
                VideoVisualizerStatus.FAILED,
                VideoVisualizerStatus.CANCELLED,
            }:
                messages.error(request, "Wait for the current analysis to finish before generating masks.")
            elif not (analysis.video or analysis.source_url):
                messages.error(request, "The source video is unavailable, so masks cannot be regenerated.")
            else:
                readiness = model_readiness(analysis.model_session)
                if not readiness["ready"]:
                    messages.error(request, "Mask reanalysis blocked: " + " ".join(readiness["errors"]))
                else:
                    reset_visualizer_analysis(analysis)
                    ensure_analysis_worker(analysis)
                    messages.success(request, "Reanalysis queued to generate visual-only estimated masks.")
            return redirect(f"{request.path}?analysis={analysis.pk}")

        if action == "upload_video":
            upload_form = VideoVisualizerUploadForm(
                request.POST,
                request.FILES,
                default_confidence=configuration.confidence_threshold,
            )
            if upload_form.is_valid():
                upload = request.FILES.get("video")
                stream_url = upload_form.cleaned_data.get("stream_url", "").strip()
                if not upload and not stream_url:
                    messages.error(request, "Upload a road-survey video or provide a live camera stream URL before queueing analysis.")
                    return redirect("admin_video_analyzer_explicit")
                readiness = model_readiness(configuration.model_session)
                if not readiness["ready"]:
                    messages.error(request, "Video analysis is blocked: " + " ".join(readiness["errors"]))
                    return redirect("admin_video_analyzer_explicit")
                try:
                    if upload:
                        metadata = read_uploaded_video(upload)
                        analysis = save_visualizer_video(
                            upload,
                            upload.name,
                            metadata,
                            upload_form,
                            configuration,
                            request.user,
                            gps_upload=request.FILES.get("gps_file"),
                        )
                    else:
                        metadata = read_video_stream_metadata(stream_url)
                        analysis = create_visualizer_stream_analysis(
                            stream_url,
                            metadata,
                            upload_form,
                            configuration,
                            request.user,
                            gps_upload=request.FILES.get("gps_file"),
                        )
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect("admin_video_analyzer_explicit")
                if not analysis.model_session:
                    analysis.status = VideoVisualizerStatus.FAILED
                    analysis.error_message = "No completed YOLO11 training session is available. Train or mark a model active before processing."
                    analysis.save(update_fields=["status", "error_message"])
                    messages.error(request, analysis.error_message)
                else:
                    audit_dataset(request.user, "video-queue", f"Queued video visualizer analysis {analysis.pk}.")
                    start_analysis_worker(analysis.pk)
                    messages.success(request, f"Video analysis queued for {analysis.original_filename}.")
                return redirect(f"{request.path}?analysis={analysis.pk}")
            messages.error(request, "Video upload details are invalid.")
            return redirect("admin_video_analyzer_explicit")

        if action == "set_active_video_model":
            messages.info(request, "Model selection has moved to Settings.")
            return redirect("admin_settings")

        if action == "set_ground_truth":
            analysis = get_object_or_404(VideoVisualizerAnalysis, pk=request.POST.get("analysis_id"))
            form = GroundTruthCountForm(request.POST)
            if form.is_valid():
                analysis.ground_truth_pothole_count = form.cleaned_data["ground_truth_pothole_count"]
                analysis.ground_truth_notes = form.cleaned_data["ground_truth_notes"]
                analysis.save(update_fields=["ground_truth_pothole_count", "ground_truth_notes"])
                messages.success(request, "Independent pothole ground truth saved for model evaluation.")
            else:
                messages.error(request, "Enter a valid independently reviewed pothole count.")
            return redirect(f"{request.path}?analysis={analysis.pk}")

        if action == "review_track":
            track = get_object_or_404(VideoPotholeTrack, pk=request.POST.get("track_id"))
            status = request.POST.get("review_status")
            severity = request.POST.get("severity")
            if status in VideoTrackReviewStatus.values:
                track.review_status = status
            if severity in [choice[0] for choice in track._meta.get_field("severity").choices]:
                track.severity = severity
            track.road_section = request.POST.get("road_section", "").strip()
            track.remarks = request.POST.get("remarks", "").strip()
            depth = request.POST.get("measured_depth_mm", "").strip()
            lat = request.POST.get("latitude", "").strip()
            lng = request.POST.get("longitude", "").strip()
            track.measured_depth_mm = Decimal(depth) if depth else None
            if depth:
                track.measurement_basis = VideoPotholeTrack.MeasurementBasis.FIELD_MEASURED
            track.latitude = Decimal(lat) if lat else None
            track.longitude = Decimal(lng) if lng else None
            track.save(update_fields=["review_status", "severity", "road_section", "remarks", "measured_depth_mm", "measurement_basis", "latitude", "longitude"])
            recalculate_analysis_potholes(track.analysis)
            messages.success(request, f"Track P{track.track_id} updated.")
            return redirect(f"{request.path}?analysis={track.analysis_id}")

        if action == "merge_tracks":
            analysis = get_object_or_404(VideoVisualizerAnalysis, pk=request.POST.get("analysis_id"))
            primary = get_object_or_404(VideoPotholeTrack, analysis=analysis, track_id=request.POST.get("primary_track_id"))
            duplicate = get_object_or_404(VideoPotholeTrack, analysis=analysis, track_id=request.POST.get("duplicate_track_id"))
            if primary.pk == duplicate.pk:
                messages.error(request, "Select two different pothole tracks to merge.")
                return redirect(f"{request.path}?analysis={analysis.pk}")
            if primary.label.lower() != duplicate.label.lower():
                messages.error(request, "Only tracks with the same defect class can be merged.")
                return redirect(f"{request.path}?analysis={analysis.pk}")
            primary.first_frame = min(primary.first_frame, duplicate.first_frame)
            primary.last_frame = max(primary.last_frame, duplicate.last_frame)
            primary.first_timestamp = min(primary.first_timestamp, duplicate.first_timestamp)
            primary.last_timestamp = max(primary.last_timestamp, duplicate.last_timestamp)
            primary.appearance_count += duplicate.appearance_count
            primary.highest_confidence = max(primary.highest_confidence, duplicate.highest_confidence)
            primary.lowest_confidence = min(primary.lowest_confidence, duplicate.lowest_confidence)
            weighted = (
                float(primary.average_confidence) * max(primary.appearance_count - duplicate.appearance_count, 1)
                + float(duplicate.average_confidence) * duplicate.appearance_count
            ) / max(primary.appearance_count, 1)
            primary.average_confidence = Decimal(str(round(weighted, 4)))
            primary.remarks = (primary.remarks + "\nMerged duplicate P" + str(duplicate.track_id)).strip()
            primary.save()
            duplicate.delete()
            recalculate_analysis_potholes(analysis)
            analysis.duplicate_tracks_merged += 1
            analysis.save(update_fields=["duplicate_tracks_merged"])
            messages.success(request, "Duplicate track merged.")
            return redirect(f"{request.path}?analysis={analysis.pk}")

        if action == "remove_track":
            track = get_object_or_404(VideoPotholeTrack, pk=request.POST.get("track_id"))
            analysis_id = track.analysis_id
            track.delete()
            analysis = get_object_or_404(VideoVisualizerAnalysis, pk=analysis_id)
            recalculate_analysis_potholes(analysis)
            messages.success(request, "False detection removed.")
            return redirect(f"{request.path}?analysis={analysis_id}")

        if action == "add_track_to_dataset":
            track = get_object_or_404(VideoPotholeTrack, pk=request.POST.get("track_id"))
            if track.label.lower() != "pothole":
                messages.error(request, "Only pothole tracks can be added to the pothole training dataset.")
                return redirect(f"{request.path}?analysis={track.analysis_id}")
            if not track.snapshot_frame:
                messages.error(request, "This track does not have a saved full-frame snapshot.")
                return redirect(f"{request.path}?analysis={track.analysis_id}")
            track.snapshot_frame.open("rb")
            try:
                data = track.snapshot_frame.read()
            finally:
                track.snapshot_frame.close()
            file_hash = hashlib.sha256(data).hexdigest()
            existing_record = DatasetImage.objects.filter(file_hash=file_hash, is_archived=False).first()
            if existing_record:
                messages.info(request, "This video frame is already in the dataset. Continue drawing its mask.")
                return redirect(f"{reverse('admin_training_dataset')}?tab=annotate&image={existing_record.pk}")
            if DatasetImage.objects.filter(file_hash=file_hash).exists():
                messages.error(request, "This video frame exists in the archived dataset and cannot be added again.")
                return redirect(f"{request.path}?analysis={track.analysis_id}")
            with Image.open(io.BytesIO(data)) as opened:
                width, height = opened.size
            metadata = {
                "file_hash": file_hash,
                "file_size": len(data),
                "file_type": "jpeg",
                "width": width,
                "height": height,
                "extension": ".jpg",
            }
            record = save_dataset_image_from_bytes(
                data,
                f"video-{track.analysis_id}-p{track.track_id}.jpg",
                metadata,
                DatasetSplit.TRAIN,
                request.user,
                source=DatasetImageSource.DETECTION,
                source_group=f"video-analysis-{track.analysis_id}",
            )
            box = track.best_bbox or {}
            if box:
                PotholeAnnotation.objects.create(
                    image=record,
                    center_x=normalized_decimal(box.get("center_x", 0.5)),
                    center_y=normalized_decimal(box.get("center_y", 0.5)),
                    width=normalized_decimal(box.get("width", 0.1)),
                    height=normalized_decimal(box.get("height", 0.1)),
                    segmentation_points=track.best_segmentation_points,
                    confidence=track.highest_confidence,
                    source=PotholeAnnotation.Source.PREDICTED,
                    created_by=request.user,
                )
            record.status = DatasetImageStatus.PARTIAL
            version = create_dataset_version(request.user, notes=f"Video feedback P{track.track_id}")
            record.dataset_version = version
            record.save(update_fields=["status", "dataset_version"])
            audit_dataset(request.user, "video-feedback", f"Added video track P{track.track_id} to dataset as {record.dataset_id}.", record, version)
            messages.success(request, "Video frame added. Draw or refine the pothole mask, then save it to the dataset.")
            return redirect(f"{reverse('admin_training_dataset')}?tab=annotate&image={record.pk}")

        return redirect("admin_video_analyzer_explicit")

    analyses = VideoVisualizerAnalysis.objects.select_related("model_session").prefetch_related("tracks").all()[:12]
    selected_analysis = None
    selected_id = request.GET.get("analysis")
    if selected_id:
        selected_analysis = VideoVisualizerAnalysis.objects.filter(pk=selected_id).select_related("model_session").prefetch_related("tracks").first()
    if not selected_analysis:
        selected_analysis = next(
            (item for item in analyses if item.status == VideoVisualizerStatus.COMPLETE and (item.video or item.processed_video)),
            analyses[0] if analyses else None,
        )
    ensure_analysis_worker(selected_analysis)

    tracks = selected_analysis.tracks.all() if selected_analysis else []
    confidence_values = [float(track.average_confidence) for track in tracks]
    severity_counts = tracks.values("severity").annotate(total=Count("id")).order_by("severity") if selected_analysis else []
    timeline_markers = []
    if selected_analysis:
        duration = float(selected_analysis.duration_seconds or 0)
        fps = float(selected_analysis.fps or 0)
        if duration <= 0 and fps > 0 and selected_analysis.frame_count:
            duration = selected_analysis.frame_count / fps
        for track in tracks:
            timestamp = float(track.best_frame / fps) if fps > 0 else float(track.first_timestamp or 0)
            timeline_markers.append(
                {
                    "track_id": track.track_id,
                    "timestamp": round(timestamp, 3),
                    "frame": track.best_frame,
                    "percent": round(max(0, min(100, (timestamp / duration) * 100)), 4) if duration else 0,
                    "status": track.review_status,
                    "severity": track.severity,
                    "confidence": float(track.highest_confidence),
                    "label": "road_damage" if track.label.lower() == "road damage" else "pothole",
                }
            )
        timeline_markers.sort(key=lambda marker: (marker["timestamp"], marker["track_id"]))
    frame_detections = analysis_frame_detections(selected_analysis)
    has_estimated_masks = any(
        detection.get("mask_source") == "estimated"
        for frame in frame_detections
        if isinstance(frame, dict)
        for detection in frame.get("detections", [])
        if isinstance(detection, dict)
    )
    context = admin_context(request, "video_analyzer") | {
        "upload_form": upload_form,
        "analyses": analyses,
        "analysis": selected_analysis,
        "tracks": tracks,
        "road_damage_count": tracks.filter(label__iexact="Road damage").exclude(
            review_status=VideoTrackReviewStatus.REJECTED
        ).count() if selected_analysis else 0,
        "ground_truth_form": GroundTruthCountForm(
            initial={
                "ground_truth_pothole_count": selected_analysis.ground_truth_pothole_count,
                "ground_truth_notes": selected_analysis.ground_truth_notes,
            }
        ) if selected_analysis else None,
        "timeline_markers": timeline_markers,
        "frame_detections": frame_detections,
        "has_estimated_masks": has_estimated_masks,
        "active_model": configuration.model_session,
        "analyzer_configuration": configuration,
        "detection_mask_refinement": settings.DETECTION_MASK_REFINEMENT,
        "severity_counts": severity_counts,
        "video_summary": {
            "total_unique": selected_analysis.total_unique_potholes if selected_analysis else 0,
            "total_detections": selected_analysis.total_detections if selected_analysis else 0,
            "average_confidence": selected_analysis.average_confidence if selected_analysis else None,
            "highest_confidence": max(confidence_values) if confidence_values else None,
            "lowest_confidence": min(confidence_values) if confidence_values else None,
        },
    }
    return render(request, "console/video_visualizer.html", context)


@staff_required
def video_visualizer_status(request, analysis_id):
    analysis = get_object_or_404(VideoVisualizerAnalysis, pk=analysis_id)
    ensure_analysis_worker(analysis)
    road_damage_count = analysis.tracks.filter(label__iexact="Road damage").exclude(
        review_status=VideoTrackReviewStatus.REJECTED
    ).count()
    live_preview_url = ""
    if analysis.live_preview_frame:
        try:
            live_preview_url = analysis.live_preview_frame.url
        except (ValueError, OSError):
            live_preview_url = ""
    response = JsonResponse(
        {
            "id": analysis.pk,
            "status": analysis.status,
            "is_continuous": analysis.is_continuous,
            "stop_requested": analysis.stop_requested,
            "current_frame": analysis.current_frame,
            "frame_count": analysis.frame_count,
            "frames_processed": analysis.frames_processed,
            "total_unique_potholes": analysis.total_unique_potholes,
            "road_damage_count": road_damage_count,
            "total_detections": analysis.total_detections,
            "average_confidence": float(analysis.average_confidence) if analysis.average_confidence is not None else None,
            "highest_confidence": float(analysis.highest_confidence) if analysis.highest_confidence is not None else None,
            "lowest_confidence": float(analysis.lowest_confidence) if analysis.lowest_confidence is not None else None,
            "average_processing_fps": float(analysis.average_processing_fps or 0),
            "source_processing_fps": float(analysis.source_processing_fps or 0),
            "realtime_factor": float(analysis.realtime_factor or 0),
            "duration_seconds": float(analysis.duration_seconds or 0),
            "live_preview_url": live_preview_url,
            "error_message": analysis.error_message,
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@staff_required
def video_visualizer_report(request, analysis_id):
    analysis = get_object_or_404(VideoVisualizerAnalysis.objects.select_related("model_session").prefetch_related("tracks"), pk=analysis_id)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="roadvision-video-inspection-{analysis.pk}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Video filename", analysis.original_filename])
    writer.writerow(["Date of inspection", analysis.created_at.isoformat()])
    writer.writerow(["Road location", analysis.road_section])
    writer.writerow(["Road station / chainage", analysis.chainage_station])
    writer.writerow(["Model", analysis.model_session.model_name if analysis.model_session else "No model"])
    writer.writerow(["Model task", analysis.model_session.model_task if analysis.model_session else ""])
    writer.writerow(["Model file", analysis.model_session.model_file if analysis.model_session else ""])
    writer.writerow(["Model SHA-256", analysis.model_session.model_sha256 if analysis.model_session else ""])
    writer.writerow(["Local test images", analysis.model_session.local_test_images if analysis.model_session else ""])
    writer.writerow(["Local mask precision", analysis.model_session.local_precision if analysis.model_session else ""])
    writer.writerow(["Local mask recall", analysis.model_session.local_recall if analysis.model_session else ""])
    writer.writerow(["Local mask mAP50", analysis.model_session.local_map50 if analysis.model_session else ""])
    writer.writerow(["Local mask mAP50-95", analysis.model_session.local_map5095 if analysis.model_session else ""])
    writer.writerow(["Measurement notice", "Visual screening only unless a track is marked camera-calibrated or field-measured."])
    if analysis.model_session and analysis.model_session.model_task == "detect":
        writer.writerow([
            "Detection-mode mask policy",
            "Orange masks, when present, are box-derived visual estimates only; they are excluded from segmentation metrics and calibrated measurements.",
        ])
    writer.writerow(["Calibration meters/pixel", analysis.calibration_m_per_pixel or "not supplied"])
    writer.writerow(["Calibration notes", analysis.calibration_notes])
    writer.writerow(["Total unique potholes", analysis.total_unique_potholes])
    writer.writerow(["Independently reviewed potholes", analysis.ground_truth_pothole_count if analysis.ground_truth_pothole_count is not None else ""])
    writer.writerow(["Ground-truth notes", analysis.ground_truth_notes])
    writer.writerow(["Total detections", analysis.total_detections])
    writer.writerow(["Average confidence", analysis.average_confidence or ""])
    writer.writerow(["Highest confidence", analysis.highest_confidence or ""])
    writer.writerow(["Lowest confidence", analysis.lowest_confidence or ""])
    writer.writerow(["Processing FPS", analysis.average_processing_fps])
    writer.writerow(["Source-frame throughput FPS", analysis.source_processing_fps])
    writer.writerow(["Real-time factor", analysis.realtime_factor])
    writer.writerow(["Effective frame skip", analysis.effective_frame_skip])
    writer.writerow(["Raw tracks", analysis.raw_track_count])
    writer.writerow(["Short tracks discarded", analysis.discarded_short_tracks])
    writer.writerow(["Duplicate fragments merged", analysis.duplicate_tracks_merged])
    writer.writerow(["Engineer remarks", ""])
    writer.writerow([])
    writer.writerow([
        "Track ID",
        "Class",
        "First timestamp",
        "Last timestamp",
        "Average confidence",
        "Highest confidence",
        "Lowest confidence",
        "Appearances",
        "Severity",
        "Latitude",
        "Longitude",
        "Road section",
        "Review status",
        "Measurement basis",
        "Estimated length m",
        "Estimated width m",
        "Estimated surface sqm",
        "Measured depth mm",
        "Snapshot crop",
        "Snapshot frame",
        "Remarks",
    ])
    for track in analysis.tracks.all():
        writer.writerow([
            f"P{track.track_id}",
            track.label,
            track.first_timestamp,
            track.last_timestamp,
            track.average_confidence,
            track.highest_confidence,
            track.lowest_confidence,
            track.appearance_count,
            track.severity,
            track.latitude or "",
            track.longitude or "",
            track.road_section,
            track.review_status,
            track.get_measurement_basis_display(),
            track.estimated_length_m or "unavailable without calibration",
            track.estimated_width_m or "unavailable without calibration",
            track.estimated_surface_area_sqm or "unavailable without calibration",
            track.measured_depth_mm or "",
            track.snapshot_crop.url if track.snapshot_crop else "",
            track.snapshot_frame.url if track.snapshot_frame else "",
            track.remarks,
        ])
    return response


@staff_required
def module_placeholder(request, module):
    titles = {
        "overview": "Dashboard",
        "live-map": "Route Map",
        "detections": "Defect Inventory",
        "dispatch": "Work Orders",
        "fleet-cams": "Survey Vehicles",
        "detection-sources": "Survey Sources",
        "personnel": "Personnel",
        "settings": "Settings",
    }
    title = titles.get(module, "Module")
    active = module.replace("-", "_")
    configuration = analyzer_configuration() if module == "settings" else None
    analyzer_settings_form = AnalyzerSettingsForm(instance=configuration) if configuration else None
    personnel_account_form = PersonnelAccountForm() if module == "personnel" else None
    fleet_device_form = FleetDeviceForm() if module == "fleet-cams" else None
    if analyzer_settings_form and not is_admin(request.user):
        for field in analyzer_settings_form.fields.values():
            field.disabled = True

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create_personnel_account":
            if not is_admin(request.user):
                messages.error(request, "Only administrators can create personnel accounts.")
            else:
                personnel_account_form = PersonnelAccountForm(request.POST)
                if personnel_account_form.is_valid():
                    user = personnel_account_form.save()
                    messages.success(
                        request,
                        f"Account created for {user.profile.full_name} ({user.email}) as {user.console_role.get_role_display()}.",
                    )
                else:
                    messages.error(request, "Correct the account details below.")
                    users = get_user_model().objects.select_related("profile", "console_role").order_by("email", "username")
                    context = admin_context(request, active) | {
                        "module": module,
                        "module_title": title,
                        "users": users,
                        "roles": AppRole.choices,
                        "personnel_account_form": personnel_account_form,
                    }
                    return render(request, "console/module_placeholder.html", context, status=400)
        elif action == "review_track":
            track = get_object_or_404(VideoPotholeTrack, pk=request.POST.get("track_id"))
            status = request.POST.get("review_status")
            severity = request.POST.get("severity")
            if status in VideoTrackReviewStatus.values:
                track.review_status = status
            if severity in [choice[0] for choice in track._meta.get_field("severity").choices]:
                track.severity = severity
            track.road_section = request.POST.get("road_section", "").strip()
            track.remarks = request.POST.get("remarks", "").strip()
            depth = request.POST.get("measured_depth_mm", "").strip()
            lat = request.POST.get("latitude", "").strip()
            lng = request.POST.get("longitude", "").strip()
            track.measured_depth_mm = Decimal(depth) if depth else None
            if depth:
                track.measurement_basis = VideoPotholeTrack.MeasurementBasis.FIELD_MEASURED
            track.latitude = Decimal(lat) if lat else None
            track.longitude = Decimal(lng) if lng else None
            track.save(
                update_fields=[
                    "review_status", "severity", "road_section", "remarks",
                    "measured_depth_mm", "measurement_basis", "latitude", "longitude",
                ]
            )
            recalculate_analysis_potholes(track.analysis)
            messages.success(request, f"Defect A{track.analysis_id}-P{track.track_id} updated.")
        elif action == "remove_track":
            track = get_object_or_404(VideoPotholeTrack, pk=request.POST.get("track_id"))
            analysis_id = track.analysis_id
            track.delete()
            analysis = get_object_or_404(VideoVisualizerAnalysis, pk=analysis_id)
            recalculate_analysis_potholes(analysis)
            messages.success(request, "False detection removed from the defect inventory.")
        elif action == "add_track_to_dataset":
            track = get_object_or_404(VideoPotholeTrack, pk=request.POST.get("track_id"))
            if track.label.lower() != "pothole":
                messages.error(request, "Only pothole tracks can be added to the pothole training dataset.")
                return redirect("admin_detections")
            if not track.snapshot_frame:
                messages.error(request, "This defect does not have a saved full-frame snapshot.")
                return redirect("admin_detections")
            track.snapshot_frame.open("rb")
            try:
                data = track.snapshot_frame.read()
            finally:
                track.snapshot_frame.close()
            file_hash = hashlib.sha256(data).hexdigest()
            existing_record = DatasetImage.objects.filter(file_hash=file_hash, is_archived=False).first()
            if existing_record:
                messages.info(request, "This defect frame is already in the dataset. Continue drawing its mask.")
                return redirect(f"{reverse('admin_training_dataset')}?tab=annotate&image={existing_record.pk}")
            if DatasetImage.objects.filter(file_hash=file_hash).exists():
                messages.error(request, "This defect frame exists in the archived dataset and cannot be added again.")
                return redirect("admin_detections")
            with Image.open(io.BytesIO(data)) as opened:
                width, height = opened.size
            metadata = {
                "file_hash": file_hash,
                "file_size": len(data),
                "file_type": "jpeg",
                "width": width,
                "height": height,
                "extension": ".jpg",
            }
            record = save_dataset_image_from_bytes(
                data,
                f"video-{track.analysis_id}-p{track.track_id}.jpg",
                metadata,
                DatasetSplit.TRAIN,
                request.user,
                source=DatasetImageSource.DETECTION,
                source_group=f"video-analysis-{track.analysis_id}",
            )
            box = track.best_bbox or {}
            if box:
                PotholeAnnotation.objects.create(
                    image=record,
                    center_x=normalized_decimal(box.get("center_x", 0.5)),
                    center_y=normalized_decimal(box.get("center_y", 0.5)),
                    width=normalized_decimal(box.get("width", 0.1)),
                    height=normalized_decimal(box.get("height", 0.1)),
                    segmentation_points=track.best_segmentation_points,
                    confidence=track.highest_confidence,
                    source=PotholeAnnotation.Source.PREDICTED,
                    created_by=request.user,
                )
            record.status = DatasetImageStatus.PARTIAL
            version = create_dataset_version(request.user, notes=f"Video feedback A{track.analysis_id}-P{track.track_id}")
            record.dataset_version = version
            record.save(update_fields=["status", "dataset_version"])
            audit_dataset(
                request.user,
                "video-feedback",
                f"Added defect A{track.analysis_id}-P{track.track_id} as {record.dataset_id}.",
                record,
                version,
            )
            messages.success(request, "Defect frame added. Draw or refine the pothole mask, then save it to the dataset.")
            return redirect(f"{reverse('admin_training_dataset')}?tab=annotate&image={record.pk}")
        elif action == "merge_tracks":
            analysis = get_object_or_404(VideoVisualizerAnalysis, pk=request.POST.get("analysis_id"))
            primary = get_object_or_404(VideoPotholeTrack, analysis=analysis, track_id=request.POST.get("primary_track_id"))
            duplicate = get_object_or_404(VideoPotholeTrack, analysis=analysis, track_id=request.POST.get("duplicate_track_id"))
            if primary.pk == duplicate.pk:
                messages.error(request, "Select two different defects to merge.")
                return redirect("admin_detections")
            if primary.label.lower() != duplicate.label.lower():
                messages.error(request, "Only tracks with the same defect class can be merged.")
                return redirect("admin_detections")
            primary.first_frame = min(primary.first_frame, duplicate.first_frame)
            primary.last_frame = max(primary.last_frame, duplicate.last_frame)
            primary.first_timestamp = min(primary.first_timestamp, duplicate.first_timestamp)
            primary.last_timestamp = max(primary.last_timestamp, duplicate.last_timestamp)
            original_appearances = primary.appearance_count
            primary.appearance_count += duplicate.appearance_count
            primary.highest_confidence = max(primary.highest_confidence, duplicate.highest_confidence)
            primary.lowest_confidence = min(primary.lowest_confidence, duplicate.lowest_confidence)
            weighted = (
                float(primary.average_confidence) * max(original_appearances, 1)
                + float(duplicate.average_confidence) * duplicate.appearance_count
            ) / max(primary.appearance_count, 1)
            primary.average_confidence = Decimal(str(round(weighted, 4)))
            primary.remarks = (primary.remarks + f"\nMerged duplicate P{duplicate.track_id}").strip()
            primary.save()
            duplicate.delete()
            recalculate_analysis_potholes(analysis)
            analysis.duplicate_tracks_merged += 1
            analysis.save(update_fields=["duplicate_tracks_merged"])
            messages.success(request, "Duplicate defects merged.")
        elif action == "update_analyzer_settings":
            if not is_admin(request.user):
                messages.error(request, "Only administrators can change Video Analyzer settings.")
            else:
                analyzer_settings_form = AnalyzerSettingsForm(request.POST, instance=configuration)
                if analyzer_settings_form.is_valid():
                    configuration = analyzer_settings_form.save(commit=False)
                    configuration.updated_by = request.user
                    configuration.save()
                    TrainingSession.objects.update(is_active_video_model=False)
                    configuration.model_session.is_active_video_model = True
                    configuration.model_session.save(update_fields=["is_active_video_model"])
                    messages.success(request, "Video Analyzer settings saved.")
                else:
                    messages.error(request, "Correct the invalid Video Analyzer settings.")
                    context = admin_context(request, active) | {
                        "module": module,
                        "module_title": title,
                        "analyzer_settings_form": analyzer_settings_form,
                        "analyzer_configuration": configuration,
                        "model_readiness": model_readiness(configuration.model_session),
                        "dataset_readiness": dataset_readiness(),
                        "training_sessions": TrainingSession.objects.filter(status=TrainingSession.Status.COMPLETE, is_validated=True),
                    }
                    return render(request, "console/module_placeholder.html", context, status=400)
        elif action == "update_report_status":
            report = get_object_or_404(PotholeReport, pk=request.POST.get("report_id"))
            status = request.POST.get("status")
            if status in ReportStatus.values:
                report.status = status
                report.save(update_fields=["status"])
                messages.success(request, f"Report #{report.pk} moved to {report.get_status_display()}.")
        elif action == "save_fleet_device":
            if not is_admin(request.user):
                messages.error(request, "Only administrators can register or update fleet cameras.")
            else:
                fleet_device_form = FleetDeviceForm(request.POST)
                if fleet_device_form.is_valid():
                    device = fleet_device_form.save()
                    messages.success(request, f"{device.name} camera configuration saved.")
                else:
                    errors = [
                        str(message)
                        for field_errors in fleet_device_form.errors.values()
                        for message in field_errors
                    ]
                    messages.error(request, "Camera configuration is invalid. " + " ".join(errors[:4]))
        elif action == "stop_fleet_stream":
            device = get_object_or_404(FleetDevice, pk=request.POST.get("device_id"))
            analysis = get_object_or_404(VideoVisualizerAnalysis, pk=request.POST.get("analysis_id"))
            belongs_to_device = str((analysis.route_metadata or {}).get("fleet_device_id", "")) == str(device.pk)
            if not belongs_to_device or not analysis.is_continuous:
                messages.error(request, "That live analysis does not belong to this fleet camera.")
            elif request_continuous_analysis_stop(analysis):
                messages.success(request, f"{device.name} continuous detection is stopping safely.")
            else:
                messages.info(request, f"{device.name} continuous detection is already stopped.")
        elif action == "analyze_fleet_stream":
            device = get_object_or_404(FleetDevice, pk=request.POST.get("device_id"))
            configuration = analyzer_configuration()
            readiness = model_readiness(configuration.model_session)
            active_live_analysis = VideoVisualizerAnalysis.objects.filter(
                is_continuous=True,
                source_type=VideoSourceType.LIVE_STREAM,
                status__in=[
                    VideoVisualizerStatus.QUEUED,
                    VideoVisualizerStatus.RETRYING,
                    VideoVisualizerStatus.RUNNING,
                ],
                route_metadata__fleet_device_id=device.pk,
            ).first()
            if device.status != "online":
                messages.error(request, f"{device.name} is offline. Bring it online before analyzing its stream.")
            elif not device.stream_url:
                messages.error(request, f"{device.name} does not have a stream URL configured.")
            elif active_live_analysis:
                messages.info(request, f"{device.name} already has continuous detection running.")
                return redirect(f"{request.path.replace('fleet-cams/', 'video-analyzer/')}?analysis={active_live_analysis.pk}")
            elif not readiness["ready"]:
                messages.error(request, "Fleet stream analysis is blocked: " + " ".join(readiness["errors"]))
            else:
                try:
                    confidence_threshold = int(
                        request.POST.get("confidence_threshold") or configuration.confidence_threshold
                    )
                    if confidence_threshold not in {25, 35, 50}:
                        confidence_threshold = configuration.confidence_threshold
                    metadata = read_video_stream_metadata(device.stream_url)
                    upload_form = fleet_analysis_form(
                        device,
                        configuration,
                        confidence_threshold,
                        VideoSourceType.LIVE_STREAM,
                    )
                    analysis = create_visualizer_stream_analysis(
                        device.stream_url,
                        metadata,
                        upload_form,
                        configuration,
                        request.user,
                    )
                    analysis.original_filename = f"{device.name} live stream"
                    analysis.save(update_fields=["original_filename"])
                    attach_fleet_source(analysis, device, "live-stream")
                except (TypeError, ValueError) as exc:
                    messages.error(request, str(exc))
                else:
                    audit_dataset(
                        request.user,
                        "fleet-stream-queue",
                        f"Queued fleet stream {device.pk} as video analysis {analysis.pk}.",
                    )
                    start_analysis_worker(analysis.pk)
                    messages.success(request, f"{device.name} continuous real-time detection started.")
                    return redirect(f"{request.path.replace('fleet-cams/', 'video-analyzer/')}?analysis={analysis.pk}")
        elif action == "analyze_fleet_capture":
            device = get_object_or_404(FleetDevice, pk=request.POST.get("device_id"))
            configuration = analyzer_configuration()
            readiness = model_readiness(configuration.model_session)
            upload = request.FILES.get("video")
            if device.status != "online":
                messages.error(request, f"{device.name} is offline. Bring it online before sending a capture.")
            elif not upload:
                messages.error(request, "Record a fleet camera clip before sending it to Video Analyzer.")
            elif not readiness["ready"]:
                messages.error(request, "Fleet capture analysis is blocked: " + " ".join(readiness["errors"]))
            else:
                try:
                    confidence_threshold = int(
                        request.POST.get("confidence_threshold") or configuration.confidence_threshold
                    )
                    if confidence_threshold not in {25, 35, 50}:
                        confidence_threshold = configuration.confidence_threshold
                    metadata = read_uploaded_video(upload)
                    upload_form = fleet_analysis_form(
                        device,
                        configuration,
                        confidence_threshold,
                        VideoSourceType.DASHCAM,
                    )
                    analysis = save_visualizer_video(
                        upload,
                        upload.name,
                        metadata,
                        upload_form,
                        configuration,
                        request.user,
                    )
                    attach_fleet_source(analysis, device, "captured-clip")
                except (TypeError, ValueError) as exc:
                    messages.error(request, str(exc))
                else:
                    audit_dataset(
                        request.user,
                        "fleet-capture-queue",
                        f"Queued fleet capture {device.pk} as video analysis {analysis.pk}.",
                    )
                    start_analysis_worker(analysis.pk)
                    messages.success(request, f"{device.name} captured clip sent to Video Analyzer.")
                    return redirect(f"{request.path.replace('fleet-cams/', 'video-analyzer/')}?analysis={analysis.pk}")
        elif action == "toggle_device_status":
            device = get_object_or_404(FleetDevice, pk=request.POST.get("device_id"))
            device.status = "offline" if device.status == "online" else "online"
            device.last_seen_at = timezone.now()
            device.save(update_fields=["status", "last_seen_at"])
            messages.success(request, f"{device.name} is now {device.status}.")
        elif action == "analyze_sample":
            sample = get_object_or_404(VideoDatasetSample, pk=request.POST.get("sample_id"))
            analysis = VideoAnalysis.objects.create(
                source_type=VideoAnalysis.SourceType.DATASET,
                dataset_sample=sample,
                original_filename=sample.file_name,
                road_name=sample.road_name,
                barangay=sample.barangay,
                city=sample.city,
                route_start=sample.road_name,
                route_end=sample.city,
                min_confidence=int(request.POST.get("min_confidence") or 85),
                created_by=request.user,
            )
            try:
                run_analysis(analysis)
            except Exception as exc:
                messages.error(request, mark_analysis_failed(analysis, exc))
            else:
                messages.success(request, f"Analysis complete for {sample.name} with {analysis.events.count()} detections.")
            return redirect("admin_video_analyzer_explicit")
        elif action == "add_sample":
            VideoDatasetSample.objects.create(
                name=request.POST.get("name", "").strip() or "Untitled sample",
                road_name=request.POST.get("road_name", "").strip() or "Unassigned road",
                barangay=request.POST.get("barangay", "").strip(),
                city=request.POST.get("city", "").strip() or "Unassigned city",
                file_name=request.POST.get("file_name", "").strip(),
                duration_seconds=int(request.POST.get("duration_seconds") or 40),
                frame_count=int(request.POST.get("frame_count") or 879),
                fps=request.POST.get("fps") or 59,
                notes=request.POST.get("notes", "").strip(),
            )
            messages.success(request, "Detection source added.")
        elif action == "update_role":
            if is_admin(request.user):
                target = get_object_or_404(get_user_model(), pk=request.POST.get("user_id"))
                role = request.POST.get("role")
                if role in AppRole.values:
                    UserRole.objects.update_or_create(user=target, defaults={"role": role})
                    messages.success(request, f"{target.email or target.username} role updated.")
            else:
                messages.error(request, "Only admins can update personnel roles.")
        elif action == "update_profile":
            profile = getattr(request.user, "profile", None)
            full_name = request.POST.get("full_name", "").strip()
            email = request.POST.get("email", "").strip().lower()
            if profile:
                profile.full_name = full_name
                profile.email = email or profile.email
                profile.save(update_fields=["full_name", "email"])
            if email:
                request.user.email = email
                request.user.username = email
                request.user.save(update_fields=["email", "username"])
            messages.success(request, "Console settings saved.")
        return redirect(request.path)

    reports = PotholeReport.objects.all()
    devices = list(FleetDevice.objects.all())
    active_live_by_device = {}
    if module == "fleet-cams":
        active_live_analyses = VideoVisualizerAnalysis.objects.filter(
            is_continuous=True,
            source_type=VideoSourceType.LIVE_STREAM,
            status__in=[
                VideoVisualizerStatus.QUEUED,
                VideoVisualizerStatus.RETRYING,
                VideoVisualizerStatus.RUNNING,
            ],
        ).order_by("-created_at")
        for live_analysis in active_live_analyses:
            device_id = str((live_analysis.route_metadata or {}).get("fleet_device_id", ""))
            active_live_by_device.setdefault(device_id, live_analysis)
        for device in devices:
            device.active_live_analysis = active_live_by_device.get(str(device.pk))
    samples = VideoDatasetSample.objects.all()
    analyses = VideoAnalysis.objects.prefetch_related("events", "dataset_sample").all()[:8]
    users = get_user_model().objects.select_related("profile", "console_role").order_by("email", "username")
    open_reports = reports.filter(status=ReportStatus.OPEN)
    critical_reports = reports.filter(severity="critical")
    defect_tracks = (
        VideoPotholeTrack.objects.select_related("analysis")
        .filter(analysis__status=VideoVisualizerStatus.COMPLETE)
        .order_by("-analysis__created_at", "track_id")
    )
    selected_defect_analysis = request.GET.get("analysis", "").strip()
    if selected_defect_analysis.isdigit():
        defect_tracks = defect_tracks.filter(analysis_id=int(selected_defect_analysis))
    defect_analyses = (
        VideoVisualizerAnalysis.objects.filter(status=VideoVisualizerStatus.COMPLETE, tracks__isnull=False)
        .distinct()
        .order_by("-created_at")
    )
    map_reports_json = [
        {
            "id": report.id,
            "lat": report.lat,
            "lng": report.lng,
            "severity": report.severity,
            "status": report.status,
            "city": report.city,
            "device_id": report.device_id,
            "notes": report.notes,
            "image_url": report.image_url,
            "detected_at": report.detected_at.isoformat(),
        }
        for report in reports
    ]

    context = admin_context(request, active) | {
        "module": module,
        "module_title": title,
        "reports": reports,
        "map_reports_json": map_reports_json,
        "devices": devices,
        "samples": samples,
        "analyses": analyses,
        "users": users,
        "roles": AppRole.choices,
        "statuses": ReportStatus.choices,
        "report_count": reports.count(),
        "open_count": open_reports.count(),
        "critical_count": critical_reports.count(),
        "online_count": sum(device.status == "online" for device in devices),
        "analyzer_settings_form": analyzer_settings_form,
        "analyzer_configuration": configuration,
        "model_readiness": model_readiness(configuration.model_session) if configuration else None,
        "dataset_readiness": dataset_readiness() if configuration else None,
        "training_sessions": TrainingSession.objects.filter(status=TrainingSession.Status.COMPLETE, is_validated=True) if configuration else [],
        "personnel_account_form": personnel_account_form,
        "fleet_device_form": fleet_device_form,
        "avg_fps": (sum(float(device.fps or 0) for device in devices) / len(devices)) if devices else 0,
        "latest_analysis": VideoAnalysis.objects.prefetch_related("events").first(),
        "status_counts": reports.values("status").annotate(total=Count("id")).order_by("status"),
        "city_counts": reports.values("city").annotate(total=Count("id")).order_by("-total", "city"),
        "severity_counts": reports.values("severity").annotate(total=Count("id")).order_by("severity"),
        "dispatch_reports": reports.exclude(status=ReportStatus.RESOLVED),
        "defect_tracks": defect_tracks,
        "defect_analyses": defect_analyses,
        "selected_defect_analysis": selected_defect_analysis,
        "defect_count": defect_tracks.count(),
        "unresolved_defect_count": defect_tracks.filter(review_status=VideoTrackReviewStatus.UNRESOLVED).count(),
        "confirmed_defect_count": defect_tracks.filter(review_status=VideoTrackReviewStatus.CONFIRMED).count(),
        "critical_defect_count": defect_tracks.filter(severity="critical").count(),
    }
    return render(request, "console/module_placeholder.html", context)
