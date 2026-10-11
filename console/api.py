"""JSON API for the RoadVision React application.

The API intentionally keeps Django's existing forms and domain services as the
write boundary.  React owns all presentation and routing; Django owns sessions,
permissions, validation, persistence, media, and long-running analysis jobs.
"""

import hashlib
import json
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q
from django.http import Http404, JsonResponse
from django.middleware.csrf import get_token
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .authz import is_admin, is_staff_role, user_role
from .forms import SignInForm, DatasetUploadForm, TrainingConfigForm, DetectionTestForm, FleetDeviceForm
from .models import (
    AppRole,
    DatasetImage,
    DatasetVersion,
    DetectionTest,
    FleetDevice,
    PotholeReport,
    ReportStatus,
    TrainingSession,
    VideoAnalysis,
    VideoDatasetSample,
    VideoPotholeTrack,
    VideoTrackReviewStatus,
    VideoVisualizerAnalysis,
    VideoVisualizerStatus,
)
from . import views


def _number(value, default=0):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _date(value):
    return value.isoformat() if value else None


def _file_url(value):
    return views.safe_media_url(value) if value else ""


def _profile_name(user):
    profile = getattr(user, "profile", None)
    return getattr(profile, "full_name", "") or user.email or user.username


def _json_body(request):
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body or b"{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, "Request body contains malformed JSON."
        if not isinstance(data, dict):
            return None, "JSON request body must be an object."
        return data, None
    return request.POST, None


def _query_integer(request, name, *, default, minimum=1, maximum=None):
    raw_value = request.GET.get(name)
    if raw_value in {None, ""}:
        return default, None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None, JsonResponse({"error": f"{name} must be an integer."}, status=400)
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
        return None, JsonResponse({"error": f"{name} must be {bounds}."}, status=400)
    return value, None


def api_staff_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            response = JsonResponse({"error": "Authentication required."}, status=401)
        elif not is_staff_role(request.user):
            response = JsonResponse(
                {"error": "Console access is pending administrator approval."},
                status=403,
            )
        else:
            response = view_func(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response

    return wrapped


def _messages(request):
    return [
        {"level": message.tags or "info", "text": str(message)}
        for message in get_messages(request)
    ]


@ensure_csrf_cookie
@never_cache
@require_GET
def session_api(request):
    payload = {
        "authenticated": bool(request.user.is_authenticated),
        "csrfToken": get_token(request),
    }
    if request.user.is_authenticated:
        role = user_role(request.user) or AppRole.VIEWER
        payload["user"] = {
            "id": request.user.pk,
            "name": _profile_name(request.user),
            "email": request.user.email or request.user.username,
            "initials": _profile_name(request.user)[:2].upper(),
            "role": role,
            "isStaff": is_staff_role(request.user),
            "isAdmin": is_admin(request.user),
        }
    return JsonResponse(payload)


@require_POST
@never_cache
def login_api(request):
    data, body_error = _json_body(request)
    if body_error:
        return JsonResponse({"error": body_error}, status=400)
    email = str(data.get("email", "")).strip().lower()
    remote_address = request.META.get("REMOTE_ADDR", "unknown")
    throttle_key = "login:" + hashlib.sha256(f"{remote_address}:{email}".encode()).hexdigest()
    attempts = cache.get(throttle_key, 0)
    if attempts >= settings.LOGIN_MAX_ATTEMPTS:
        return JsonResponse({"error": "Too many sign-in attempts. Try again in a few minutes."}, status=429)

    form = SignInForm({"email": email, "password": data.get("password", "")})
    if not form.is_valid():
        cache.set(throttle_key, attempts + 1, settings.LOGIN_LOCKOUT_SECONDS)
        error = form.non_field_errors()[0] if form.non_field_errors() else "Invalid email or password."
        return JsonResponse({"error": str(error)}, status=400)

    cache.delete(throttle_key)
    login(request, form.cleaned_data["user"])
    return JsonResponse({"ok": True, "redirect": "/app/analyzer", "csrfToken": get_token(request)})


@require_POST
@never_cache
def logout_api(request):
    logout(request)
    return JsonResponse({"ok": True})


def _report(report):
    return {
        "id": report.pk,
        "lat": report.lat,
        "lng": report.lng,
        "severity": report.severity,
        "status": report.status,
        "imageUrl": report.image_url,
        "detectedAt": _date(report.detected_at),
        "city": report.city,
        "deviceId": report.device_id,
        "notes": report.notes,
    }


def _device(device):
    return {
        "id": device.pk,
        "name": device.name,
        "city": device.city,
        "status": device.status,
        "lastSeenAt": _date(device.last_seen_at),
        "fps": _number(device.fps),
        "modelVersion": device.model_version,
        "hasStream": bool(device.stream_url),
        "streamUrl": device.stream_url,
        "roadSection": device.road_section,
        "chainageStation": device.chainage_station,
    }


def _track(track):
    return {
        "id": track.pk,
        "analysisId": track.analysis_id,
        "trackId": track.track_id,
        "code": f"A{track.analysis_id}-P{track.track_id}",
        "label": track.label,
        "firstTimestamp": _number(track.first_timestamp),
        "lastTimestamp": _number(track.last_timestamp),
        "bestFrame": track.best_frame,
        "appearances": track.appearance_count,
        "averageConfidence": round(_number(track.average_confidence) * 100, 1),
        "highestConfidence": round(_number(track.highest_confidence) * 100, 1),
        "severity": track.severity,
        "measurementBasis": track.measurement_basis,
        "estimatedLengthM": _number(track.estimated_length_m, None),
        "estimatedWidthM": _number(track.estimated_width_m, None),
        "estimatedAreaSqm": _number(track.estimated_surface_area_sqm, None),
        "measuredDepthMm": _number(track.measured_depth_mm, None),
        "latitude": _number(track.latitude, None),
        "longitude": _number(track.longitude, None),
        "roadSection": track.road_section,
        "reviewStatus": track.review_status,
        "remarks": track.remarks,
        "snapshotCropUrl": _file_url(track.snapshot_crop),
        "snapshotFrameUrl": _file_url(track.snapshot_frame),
    }


def _analysis(analysis, include_tracks=False):
    progress, remaining = views.video_analysis_progress(analysis)
    item = {
        "id": analysis.pk,
        "name": analysis.original_filename,
        "status": analysis.status,
        "sourceType": analysis.source_type,
        "roadSection": analysis.road_section,
        "chainageStation": analysis.chainage_station,
        "createdAt": _date(analysis.created_at),
        "startedAt": _date(analysis.started_at),
        "finishedAt": _date(analysis.finished_at),
        "durationSeconds": _number(analysis.duration_seconds),
        "framesProcessed": analysis.frames_processed,
        "frameCount": analysis.frame_count,
        "progress": progress,
        "remaining": remaining,
        "processingFps": _number(analysis.average_processing_fps),
        "sourceFps": _number(analysis.source_processing_fps),
        "realtimeFactor": _number(analysis.realtime_factor),
        "uniquePotholes": analysis.total_unique_potholes,
        "totalDetections": analysis.total_detections,
        "averageConfidence": round(_number(analysis.average_confidence) * 100, 1) if analysis.average_confidence is not None else None,
        "isContinuous": analysis.is_continuous,
        "stopRequested": analysis.stop_requested,
        "error": analysis.error_message,
        "model": analysis.model_session.model_name if analysis.model_session else None,
        "timeline": analysis.timeline_markers or [],
        "media": {
            "processed": reverse("admin_video_visualizer_media", args=[analysis.pk, "processed"]) if analysis.processed_video else "",
            "processedType": "video/mp4" if analysis.processed_video else "",
            "original": reverse("admin_video_visualizer_media", args=[analysis.pk, "original"]) if analysis.video else "",
            "originalType": views.VIDEO_MIME_TYPES.get((analysis.file_type or "").lower(), "application/octet-stream") if analysis.video else "",
            "live": reverse("admin_video_visualizer_media", args=[analysis.pk, "live-preview"]) if analysis.live_preview_frame else "",
            "csv": reverse("admin_video_visualizer_report", args=[analysis.pk]),
        },
    }
    if include_tracks:
        tracks = list(analysis.tracks.all())
        duration = _number(analysis.duration_seconds)
        fps = _number(analysis.fps)
        if duration <= 0 and fps > 0 and analysis.frame_count:
            duration = analysis.frame_count / fps
        item["timeline"] = [
            {
                "trackId": track.track_id,
                "timestamp": round(track.best_frame / fps, 3) if fps > 0 else _number(track.first_timestamp),
                "frame": track.best_frame,
                "percent": round(max(0, min(100, ((track.best_frame / fps) / duration) * 100)), 4) if fps > 0 and duration else 0,
                "status": track.review_status,
                "severity": track.severity,
                "confidence": _number(track.highest_confidence),
                "label": "road_damage" if track.label.lower() == "road damage" else "pothole",
            }
            for track in tracks
        ]
        item["tracks"] = [_track(track) for track in tracks]
    return item


@api_staff_required
@require_GET
def overview_api(request):
    reports = PotholeReport.objects.all()
    devices = FleetDevice.objects.all()
    tracks = VideoPotholeTrack.objects.filter(analysis__status=VideoVisualizerStatus.COMPLETE)
    latest_analyses = VideoVisualizerAnalysis.objects.select_related("model_session").all()[:6]
    report_metrics = reports.aggregate(
        reports=Count("id"),
        open_reports=Count("id", filter=Q(status=ReportStatus.OPEN)),
        critical_reports=Count("id", filter=Q(severity="critical")),
    )
    device_metrics = devices.aggregate(
        online_vehicles=Count("id", filter=Q(status="online")),
        average_fleet_fps=Avg("fps"),
    )
    track_metrics = tracks.aggregate(
        confirmed_defects=Count("id", filter=Q(review_status=VideoTrackReviewStatus.CONFIRMED)),
        unresolved_defects=Count("id", filter=Q(review_status=VideoTrackReviewStatus.UNRESOLVED)),
    )
    return JsonResponse({
        "metrics": {
            "reports": report_metrics["reports"],
            "openReports": report_metrics["open_reports"],
            "criticalReports": report_metrics["critical_reports"],
            "onlineVehicles": device_metrics["online_vehicles"],
            "confirmedDefects": track_metrics["confirmed_defects"],
            "unresolvedDefects": track_metrics["unresolved_defects"],
            "averageFleetFps": round(_number(device_metrics["average_fleet_fps"]), 1),
        },
        "cityCounts": list(reports.values("city").annotate(total=Count("id")).order_by("-total", "city")[:8]),
        "statusCounts": list(reports.values("status").annotate(total=Count("id")).order_by("status")),
        "recentReports": [_report(report) for report in reports[:6]],
        "recentAnalyses": [_analysis(analysis) for analysis in latest_analyses],
    })


@api_staff_required
@require_GET
def reports_api(request):
    limit, error = _query_integer(request, "limit", default=500, maximum=1000)
    if error:
        return error
    reports = PotholeReport.objects.all()
    counts = reports.aggregate(
        total=Count("id"),
        open=Count("id", filter=Q(status=ReportStatus.OPEN)),
        critical=Count("id", filter=Q(severity="critical")),
    )
    report_items = [_report(report) for report in reports[:limit]]
    return JsonResponse({
        "reports": report_items,
        "counts": counts,
        "pagination": {
            "limit": limit,
            "returned": len(report_items),
            "total": counts["total"],
            "truncated": counts["total"] > len(report_items),
        },
        "statusOptions": [{"value": value, "label": label} for value, label in ReportStatus.choices],
    })


@api_staff_required
@require_GET
def fleet_api(request):
    devices = list(FleetDevice.objects.all())
    active = VideoVisualizerAnalysis.objects.select_related("model_session").filter(
        is_continuous=True,
        status__in=[VideoVisualizerStatus.QUEUED, VideoVisualizerStatus.RETRYING, VideoVisualizerStatus.RUNNING],
    )
    active_by_device = {
        str((analysis.route_metadata or {}).get("fleet_device_id", "")): analysis
        for analysis in active
    }
    payload = []
    for device in devices:
        item = _device(device)
        analysis = active_by_device.get(str(device.pk))
        item["activeAnalysis"] = _analysis(analysis) if analysis else None
        payload.append(item)
    return JsonResponse({"devices": payload})


@api_staff_required
@require_GET
def detections_api(request):
    tracks = (
        VideoPotholeTrack.objects.select_related("analysis")
        .filter(analysis__status=VideoVisualizerStatus.COMPLETE)
        .order_by("-analysis__created_at", "track_id")
    )
    analysis_id, error = _query_integer(request, "analysis", default=None)
    if error:
        return error
    if analysis_id is not None:
        tracks = tracks.filter(analysis_id=analysis_id)
    counts = tracks.aggregate(
        total=Count("id"),
        unresolved=Count("id", filter=Q(review_status=VideoTrackReviewStatus.UNRESOLVED)),
        confirmed=Count("id", filter=Q(review_status=VideoTrackReviewStatus.CONFIRMED)),
        critical=Count("id", filter=Q(severity="critical")),
    )
    analyses = VideoVisualizerAnalysis.objects.filter(
        status=VideoVisualizerStatus.COMPLETE,
        tracks__isnull=False,
    ).order_by("-created_at").values("id", "original_filename").distinct()[:30]
    return JsonResponse({
        "tracks": [_track(track) for track in tracks[:250]],
        "analyses": [{"id": analysis["id"], "name": analysis["original_filename"]} for analysis in analyses],
        "counts": counts,
    })


@api_staff_required
@require_GET
def analyzer_api(request):
    analyses = list(VideoVisualizerAnalysis.objects.select_related("model_session").all()[:30])
    selected_id, error = _query_integer(request, "analysis", default=None)
    if error:
        return error
    selected = None
    if selected_id is not None:
        selected = VideoVisualizerAnalysis.objects.select_related("model_session").prefetch_related("tracks").filter(pk=selected_id).first()
    if selected is None and analyses:
        # Keep the latest completed review visible while a newer job is still
        # processing. Operators can explicitly select the active job from the
        # queue when they want its live progress.
        default_analysis = next(
            (analysis for analysis in analyses if analysis.status == VideoVisualizerStatus.COMPLETE),
            analyses[0],
        )
        selected = (
            VideoVisualizerAnalysis.objects.select_related("model_session")
            .prefetch_related("tracks")
            .filter(pk=default_analysis.pk)
            .first()
        )
    if selected and selected.status in {
        VideoVisualizerStatus.QUEUED,
        VideoVisualizerStatus.RETRYING,
        VideoVisualizerStatus.RUNNING,
    }:
        views.ensure_analysis_worker(selected)
    configuration = views.analyzer_configuration()
    readiness = views.model_readiness(configuration.model_session)
    return JsonResponse({
        "analyses": [_analysis(analysis) for analysis in analyses],
        "selected": _analysis(selected, include_tracks=True) if selected else None,
        "configuration": {
            "confidenceThreshold": configuration.confidence_threshold,
            "iouThreshold": configuration.iou_threshold,
            "inputResolution": configuration.input_resolution,
            "mode": configuration.mode,
            "tracker": configuration.tracker,
            "model": configuration.model_session.model_name if configuration.model_session else None,
            "ready": readiness["ready"],
            "errors": readiness["errors"],
        },
        "liveDetectionUrl": reverse("admin_live_camera_detection"),
    })


def _dataset_image(image):
    return {
        "id": image.pk,
        "datasetId": image.dataset_id,
        "filename": image.original_filename,
        "url": _file_url(image.image),
        "previewUrl": _file_url(image.labeled_preview),
        "width": image.width,
        "height": image.height,
        "split": image.split,
        "status": image.status,
        "source": image.source,
        "sourceGroup": image.source_group,
        "version": image.dataset_version.version_number if image.dataset_version else None,
        "potholeCount": image.pothole_count,
        "reviewNotes": image.review_notes,
        "uploadedAt": _date(image.uploaded_at),
        "annotations": [
            {
                "id": annotation.pk,
                "centerX": _number(annotation.center_x),
                "centerY": _number(annotation.center_y),
                "width": _number(annotation.width),
                "height": _number(annotation.height),
                "points": annotation.segmentation_points,
                "source": annotation.source,
            }
            for annotation in image.annotations.all()
        ],
    }


@api_staff_required
@require_GET
def dataset_api(request):
    images = DatasetImage.objects.filter(is_archived=False).select_related("dataset_version").prefetch_related("annotations")[:100]
    versions = DatasetVersion.objects.all()[:12]
    sessions = TrainingSession.objects.select_related("dataset_version").all()[:15]
    tests = DetectionTest.objects.select_related("model_session").all()[:12]
    readiness = views.dataset_readiness()
    return JsonResponse({
        "summary": views.dataset_summary(),
        "readiness": {"ready": readiness["ready"], "errors": readiness["errors"]},
        "images": [_dataset_image(image) for image in images],
        "versions": [
            {
                "id": version.pk,
                "version": version.version_number,
                "images": version.total_images,
                "annotations": version.total_annotations,
                "split": [version.train_percent, version.val_percent, version.test_percent],
                "notes": version.notes,
                "createdAt": _date(version.created_at),
            }
            for version in versions
        ],
        "sessions": [
            {
                "id": session.pk,
                "model": session.model_name,
                "status": session.status,
                "progress": session.progress,
                "epoch": session.current_epoch,
                "epochs": session.epochs,
                "map50": _number(session.local_map50 if session.local_map50 is not None else session.map50, None),
                "precision": _number(session.local_precision if session.local_precision is not None else session.precision, None),
                "recall": _number(session.local_recall if session.local_recall is not None else session.recall, None),
                "validated": session.is_validated,
                "active": session.is_active_video_model,
                "error": session.error_message,
                "validationNotes": session.validation_notes,
                "task": session.model_task,
                "createdAt": _date(session.created_at),
            }
            for session in sessions
        ],
        "tests": [
            {
                "id": test.pk,
                "filename": test.original_filename,
                "status": test.status,
                "detections": test.detection_count,
                "processingMs": test.processing_time_ms,
                "imageUrl": _file_url(test.image),
                "resultUrl": _file_url(test.result_image),
                "createdAt": _date(test.created_at),
            }
            for test in tests
        ],
    })


@api_staff_required
@require_GET
def sources_api(request):
    samples = VideoDatasetSample.objects.all()
    analyses = VideoAnalysis.objects.annotate(detection_count=Count("events")).all()[:12]
    return JsonResponse({
        "sources": [
            {
                "id": sample.pk,
                "name": sample.name,
                "road": sample.road_name,
                "barangay": sample.barangay,
                "city": sample.city,
                "file": sample.file_name,
                "duration": sample.duration_seconds,
                "frames": sample.frame_count,
                "fps": _number(sample.fps),
                "notes": sample.notes,
            }
            for sample in samples
        ],
        "recentAnalyses": [
            {
                "id": analysis.pk,
                "name": analysis.original_filename,
                "road": analysis.road_name,
                "status": analysis.status,
                "detections": analysis.detection_count,
                "createdAt": _date(analysis.created_at),
            }
            for analysis in analyses
        ],
    })


@api_staff_required
@require_GET
def personnel_api(request):
    users = get_user_model().objects.select_related("profile", "console_role").order_by("email", "username")
    return JsonResponse({
        "users": [
            {
                "id": user.pk,
                "name": _profile_name(user),
                "email": user.email or user.username,
                "role": user_role(user) or AppRole.VIEWER,
                "active": user.is_active,
                "joinedAt": _date(user.date_joined),
            }
            for user in users
        ],
        "roles": [{"value": value, "label": label} for value, label in AppRole.choices],
    })


@api_staff_required
@require_GET
def settings_api(request):
    configuration = views.analyzer_configuration()
    model_state = views.model_readiness(configuration.model_session)
    data_state = views.dataset_readiness()
    models = TrainingSession.objects.filter(status=TrainingSession.Status.COMPLETE, is_validated=True)
    profile = getattr(request.user, "profile", None)
    return JsonResponse({
        "profile": {
            "name": _profile_name(request.user),
            "email": request.user.email or getattr(profile, "email", ""),
        },
        "configuration": {
            "modelSession": configuration.model_session_id,
            "trainingModel": configuration.training_model,
            "mode": configuration.mode,
            "confidenceThreshold": configuration.confidence_threshold,
            "iouThreshold": configuration.iou_threshold,
            "device": configuration.device,
            "inputResolution": configuration.input_resolution,
            "frameSkip": configuration.frame_skip,
            "halfPrecision": configuration.half_precision,
            "tracker": configuration.tracker,
            "maxDetections": configuration.max_detections,
            "maxAttempts": configuration.max_attempts,
            "includeRoadDamage": configuration.include_road_damage,
            "minTrackAppearances": configuration.min_track_appearances,
            "dedupIouThreshold": _number(configuration.dedup_iou_threshold),
            "dedupMaxGapFrames": configuration.dedup_max_gap_frames,
            "showLabels": configuration.show_labels,
            "showConfidence": configuration.show_confidence,
            "showTrackingIds": configuration.show_tracking_ids,
            "showBoxes": configuration.show_boxes,
            "showGpsOverlay": configuration.show_gps_overlay,
        },
        "models": [
            {
                "id": model.pk,
                "name": model.model_name,
                "map50": _number(model.local_map50 if model.local_map50 is not None else model.map50),
            }
            for model in models
        ],
        "modelReadiness": {"ready": model_state["ready"], "errors": model_state["errors"]},
        "datasetReadiness": {"ready": data_state["ready"], "errors": data_state["errors"]},
    })


def _action_result(request, response):
    notices = _messages(request)
    response_payload = {}
    if isinstance(response, JsonResponse):
        try:
            response_payload = json.loads(response.content)
        except (TypeError, ValueError, json.JSONDecodeError):
            response_payload = {}
    has_error_notice = any("error" in notice["level"].split() for notice in notices)
    ok = response.status_code < 400 and not has_error_notice
    payload = {"ok": ok, "messages": notices}
    for key in ("error", "fields"):
        if key in response_payload:
            payload[key] = response_payload[key]
    if not notices:
        payload["messages"] = [{
            "level": "success" if ok else "error",
            "text": "Changes saved." if ok else response_payload.get("error", "The request could not be completed."),
        }]
    if not ok and "error" not in payload:
        payload["error"] = " ".join(
            notice["text"] for notice in payload["messages"] if "error" in notice["level"].split()
        ) or "The request could not be completed."
    status = 200 if ok else (response.status_code if response.status_code >= 400 else 400)
    return JsonResponse(payload, status=status)


ANALYZER_ACTIONS = {
    "stop_continuous_analysis", "cancel_analysis", "retry_analysis", "restart_analysis",
    "reanalyze_with_sensitivity", "reanalyze_with_estimated_masks", "upload_video",
    "set_ground_truth", "review_track", "merge_tracks", "remove_track", "add_track_to_dataset",
}
DATASET_ACTIONS = {
    "upload_images", "save_annotations", "move_source_group", "review_image", "archive_image",
    "run_augmentation", "start_training", "test_detection", "add_detection_to_dataset",
}
MODULE_ACTIONS = {
    "overview": set(),
    "live-map": {"update_report_status"},
    "detections": {"review_track", "remove_track", "add_track_to_dataset", "merge_tracks"},
    "dispatch": {"update_report_status"},
    "fleet-cams": {
        "save_fleet_device", "stop_fleet_stream", "analyze_fleet_stream",
        "analyze_fleet_capture", "toggle_device_status",
    },
    "detection-sources": {"analyze_sample", "add_sample"},
    "personnel": {"create_personnel_account", "update_role"},
    "settings": {"update_analyzer_settings", "update_profile"},
}
ADMIN_ACTIONS = {
    "create_personnel_account", "update_role", "update_analyzer_settings", "save_fleet_device",
}


def _validate_action_request(request, allowed_actions):
    if request.content_type not in {"application/x-www-form-urlencoded", "multipart/form-data"}:
        return None, JsonResponse({"error": "Actions require form data."}, status=415)
    action = request.POST.get("action", "").strip()
    if not action:
        return None, JsonResponse({"error": "Action is required."}, status=400)
    if action not in allowed_actions:
        return None, JsonResponse({"error": "Unknown action for this API route."}, status=400)
    if action in ADMIN_ACTIONS and not is_admin(request.user):
        return None, JsonResponse({"error": "Administrator access is required for this action."}, status=403)
    return action, None


def _run_action(request, allowed_actions, handler):
    action, error = _validate_action_request(request, allowed_actions)
    if error:
        return error
    form_class = {
        "upload_images": DatasetUploadForm,
        "start_training": TrainingConfigForm,
        "test_detection": DetectionTestForm,
        "save_fleet_device": FleetDeviceForm,
    }.get(action)
    if form_class:
        form = form_class(request.POST, request.FILES)
        if not form.is_valid():
            return JsonResponse({"error": "Correct the highlighted fields.", "fields": form.errors}, status=400)
    # Device IDs are slugs; the other referenced primary keys are positive integers.
    for name in ("image_id", "analysis_id", "track_id", "test_id", "sample_id", "user_id"):
        value = request.POST.get(name)
        if value is not None and (len(value) > 18 or not value.isascii() or not value.isdecimal() or int(value) < 1):
            return JsonResponse({"error": "Invalid record identifier.", "fields": {name: ["Enter a valid record ID."]}}, status=400)
    try:
        with transaction.atomic():
            response = handler()
    except Http404:
        return JsonResponse({"error": "The requested record was not found."}, status=404)
    except IntegrityError:
        return JsonResponse({"error": "This record already exists or was changed by another request. Refresh and try again."}, status=409)
    return _action_result(request, response)


@api_staff_required
@require_POST
def analyzer_action_api(request):
    return _run_action(request, ANALYZER_ACTIONS, lambda: views.video_visualizer(request))


@api_staff_required
@require_POST
def dataset_action_api(request):
    return _run_action(request, DATASET_ACTIONS, lambda: views.training_dataset_module(request))


@api_staff_required
@require_POST
def module_action_api(request, module):
    if module not in MODULE_ACTIONS:
        return JsonResponse({"error": "Unknown action module."}, status=404)
    return _run_action(
        request,
        MODULE_ACTIONS[module],
        lambda: views.module_placeholder(request, module),
    )
