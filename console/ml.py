from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .models import DetectionEvent, EngineeringPriority, EngineeringRecommendation, Severity, VideoAnalysis


MODEL_VERSION = "opencv-road-surface-detector-1.0"
MAX_DETECTION_EVENTS = 30
VISIBLE_ROAD_AREA_SQM = Decimal("105.00")
MASK_MEASUREMENT_AREA_FACTOR = Decimal("0.18")
REPAIR_AREA_ALLOWANCE_FACTOR = Decimal("1.25")


@dataclass(frozen=True)
class DetectionPrediction:
    road_name: str
    timecode_seconds: Decimal
    severity: str
    confidence: int
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    mask_polygon: str = ""
    mask_centroid_x: int = 50
    mask_centroid_y: int = 50
    damage_length_m: Decimal = Decimal("0.00")
    damage_width_m: Decimal = Decimal("0.00")
    damage_perimeter_m: Decimal = Decimal("0.00")
    damage_surface_area_sqm: Decimal = Decimal("0.000")
    estimated_repair_area_sqm: Decimal = Decimal("0.000")
    snapshot_image: str = ""


@dataclass(frozen=True)
class ModelOutput:
    predictions: list[DetectionPrediction]
    model_version: str
    frames_processed: int
    inference_fps: Decimal
    duration_seconds: int


def source_video_path(analysis: VideoAnalysis):
    if analysis.uploaded_video:
        try:
            return Path(analysis.uploaded_video.path)
        except ValueError:
            return None

    sample = analysis.dataset_sample
    if not sample or not sample.file_name:
        return None

    candidates = [
        Path(settings.BASE_DIR) / sample.file_name,
        Path(settings.MEDIA_ROOT) / sample.file_name,
        Path(settings.STATIC_ROOT or "") / sample.file_name if getattr(settings, "STATIC_ROOT", None) else None,
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def analysis_road_name(analysis: VideoAnalysis):
    if analysis.road_name:
        return analysis.road_name
    if analysis.dataset_sample:
        return analysis.dataset_sample.road_name
    return "Unassigned road section"


def severity_from_detection(confidence, width_percent, height_percent):
    area = width_percent * height_percent
    if confidence >= 90 or area >= 850:
        return Severity.CRITICAL
    if confidence >= 82 or area >= 420:
        return Severity.HIGH
    if confidence >= 70:
        return Severity.MEDIUM
    return Severity.LOW


class OpenCVRoadSurfaceDetector:
    version = MODEL_VERSION

    def predict(self, analysis: VideoAnalysis):
        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("Install opencv-python-headless and numpy to enable video analysis.") from exc

        video_path = source_video_path(analysis)
        if not video_path:
            raise RuntimeError("No local video file is available for analysis.")

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise RuntimeError(f"OpenCV could not read {video_path}.")

        fps_value = capture.get(cv2.CAP_PROP_FPS) or 30
        fps = Decimal(str(round(fps_value, 2)))
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = int(round(total_frames / fps_value)) if total_frames and fps_value else 0
        frame_step = max(1, int(fps_value // 2) or 1)
        predictions = []
        seen = set()
        processed = 0
        frame_index = 0

        while capture.isOpened():
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % frame_step:
                frame_index += 1
                continue

            processed += 1
            predictions.extend(
                self.detect_frame(
                    frame=frame,
                    frame_index=frame_index,
                    fps_value=fps_value,
                    analysis=analysis,
                    seen=seen,
                    cv2=cv2,
                    np=np,
                )
            )
            frame_index += 1

        capture.release()
        predictions = select_detection_predictions(predictions, duration or analysis.duration_seconds or 0)
        return ModelOutput(
            predictions=predictions,
            model_version=self.version,
            frames_processed=total_frames or processed,
            inference_fps=fps,
            duration_seconds=duration or analysis.duration_seconds or 0,
        )

    def detect_frame(self, frame, frame_index, fps_value, analysis, seen, cv2, np):
        height, width = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (7, 7), 0)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
        blackhat = cv2.morphologyEx(blur, cv2.MORPH_BLACKHAT, kernel)
        _, blackhat_mask = cv2.threshold(blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        mean, stddev = cv2.meanStdDev(blur)
        dark_limit = max(35, float(mean[0][0] - stddev[0][0] * 0.35))
        dark_mask = cv2.inRange(blur, 0, int(dark_limit))
        mask = cv2.bitwise_or(blackhat_mask, dark_mask)

        road_mask = np.zeros_like(mask)
        road_mask[int(height * 0.25) :, :] = 255
        mask = cv2.bitwise_and(mask, road_mask)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))

        bright_limit = int(float(mean[0][0] + stddev[0][0] * 0.5))
        bright_mask = cv2.inRange(blur, bright_limit, 255)
        bright_mask = cv2.bitwise_and(bright_mask, road_mask)
        bright_mask = cv2.morphologyEx(bright_mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        bright_mask = cv2.morphologyEx(bright_mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))

        dark_contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        bright_contours, _ = cv2.findContours(bright_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidate_contours = [(contour, "dark") for contour in dark_contours] + [
            (contour, "bright") for contour in bright_contours
        ]
        frame_area = width * height
        frame_predictions = []

        for contour, mask_source in candidate_contours:
            area = cv2.contourArea(contour)
            max_area_ratio = 0.11 if mask_source == "bright" else 0.12
            if area < frame_area * 0.00045 or area > frame_area * max_area_ratio:
                continue

            x, y, w, h = cv2.boundingRect(contour)
            if y < height * 0.22 or w < 8 or h < 8:
                continue
            if mask_source == "bright" and y + h > height * 0.96:
                continue

            aspect = w / max(h, 1)
            if aspect < 0.25 or aspect > 5.5:
                continue

            extent = area / max(w * h, 1)
            if extent < 0.18:
                continue

            pad = max(6, int(max(w, h) * 0.2))
            x1, y1 = max(0, x - pad), max(0, y - pad)
            x2, y2 = min(width, x + w + pad), min(height, y + h + pad)
            inner_mean = float(np.mean(blur[y : y + h, x : x + w]))
            outer = blur[y1:y2, x1:x2]
            outer_mean = float(np.mean(outer)) if outer.size else inner_mean
            contrast = max(0.0, outer_mean - inner_mean)
            if mask_source == "bright":
                contrast = max(contrast, inner_mean - outer_mean)
            area_ratio = area / frame_area
            confidence = int(min(98, max(45, 58 + contrast * 0.8 + min(24, area_ratio * 2400) + min(10, extent * 10))))
            if confidence < analysis.min_confidence:
                continue

            bbox_x = max(0, min(99, int(round((x / width) * 100))))
            bbox_y = max(0, min(99, int(round((y / height) * 100))))
            bbox_w = max(1, min(100 - bbox_x, int(round((w / width) * 100))))
            bbox_h = max(1, min(100 - bbox_y, int(round((h / height) * 100))))
            timecode = Decimal(str(round(frame_index / fps_value, 2))) if fps_value else Decimal("0.00")
            dedupe_key = (int(float(timecode) * 2), bbox_x // 8, bbox_y // 8)
            if dedupe_key in seen:
                continue

            seen.add(dedupe_key)
            frame_predictions.append(
                DetectionPrediction(
                    road_name=analysis_road_name(analysis),
                    timecode_seconds=timecode,
                    severity=severity_from_detection(confidence, bbox_w, bbox_h),
                    confidence=confidence,
                    bbox_x=bbox_x,
                    bbox_y=bbox_y,
                    bbox_w=bbox_w,
                    bbox_h=bbox_h,
                    **mask_geometry_from_contour(contour, x, y, w, h, frame_area, cv2),
                    snapshot_image=save_detection_snapshot(
                        analysis=analysis,
                        frame=frame,
                        x=x,
                        y=y,
                        w=w,
                        h=h,
                        contour=contour,
                        confidence=confidence,
                        sequence=len(seen),
                        cv2=cv2,
                    ),
                )
            )

        return frame_predictions


def get_model():
    return OpenCVRoadSurfaceDetector()


def select_detection_predictions(predictions, duration_seconds):
    if len(predictions) <= MAX_DETECTION_EVENTS:
        return sorted(predictions, key=lambda item: (item.timecode_seconds, -item.confidence))

    duration = Decimal(str(duration_seconds or 0))
    if duration <= 0:
        duration = max((item.timecode_seconds for item in predictions), default=Decimal("1.00"))

    bucket_count = max(1, MAX_DETECTION_EVENTS // 2)
    bucket_width = max(Decimal("1.00"), duration / Decimal(bucket_count))
    buckets = {}
    for prediction in predictions:
        bucket = int(prediction.timecode_seconds / bucket_width)
        current = buckets.get(bucket)
        if current is None or prediction.confidence > current.confidence:
            buckets[bucket] = prediction

    selected = sorted(buckets.values(), key=lambda item: item.timecode_seconds)
    for prediction in sorted(predictions, key=lambda item: (-item.confidence, item.timecode_seconds)):
        if len(selected) >= MAX_DETECTION_EVENTS:
            break
        if prediction not in selected:
            selected.append(prediction)

    return sorted(selected[:MAX_DETECTION_EVENTS], key=lambda item: (item.timecode_seconds, -item.confidence))


def mask_geometry_from_contour(contour, x, y, w, h, frame_area, cv2):
    if contour is None or w <= 0 or h <= 0:
        return {}

    perimeter = cv2.arcLength(contour, True)
    epsilon = max(1.0, perimeter * 0.012)
    simplified = cv2.approxPolyDP(contour, epsilon, True)
    if len(simplified) < 3:
        simplified = contour

    points = []
    for point in simplified.reshape(-1, 2):
        px = max(0.0, min(100.0, ((float(point[0]) - x) / w) * 100))
        py = max(0.0, min(100.0, ((float(point[1]) - y) / h) * 100))
        points.append(f"{px:.1f}% {py:.1f}%")

    moments = cv2.moments(contour)
    if moments["m00"]:
        centroid_x = int(round(max(0.0, min(100.0, (((moments["m10"] / moments["m00"]) - x) / w) * 100))))
        centroid_y = int(round(max(0.0, min(100.0, (((moments["m01"] / moments["m00"]) - y) / h) * 100))))
    else:
        centroid_x = 50
        centroid_y = 50

    calibrated_frame_area_sqm = VISIBLE_ROAD_AREA_SQM * MASK_MEASUREMENT_AREA_FACTOR
    scale_m_per_px = (calibrated_frame_area_sqm / Decimal(frame_area)).sqrt() if frame_area else Decimal("0")
    surface_area = Decimal(str(max(0.0, cv2.contourArea(contour)))) * scale_m_per_px * scale_m_per_px
    rect_width_px, rect_height_px = cv2.minAreaRect(contour)[1]
    damage_length = Decimal(str(max(rect_width_px, rect_height_px))) * scale_m_per_px
    damage_width = Decimal(str(min(rect_width_px, rect_height_px))) * scale_m_per_px
    damage_perimeter = Decimal(str(max(0.0, perimeter))) * scale_m_per_px
    repair_area = surface_area * REPAIR_AREA_ALLOWANCE_FACTOR

    return {
        "mask_polygon": ", ".join(points[:48]),
        "mask_centroid_x": centroid_x,
        "mask_centroid_y": centroid_y,
        "damage_length_m": decimalize(damage_length),
        "damage_width_m": decimalize(damage_width),
        "damage_perimeter_m": decimalize(damage_perimeter),
        "damage_surface_area_sqm": decimalize(surface_area, "0.001"),
        "estimated_repair_area_sqm": decimalize(repair_area, "0.001"),
    }


def save_detection_snapshot(analysis, frame, x, y, w, h, contour, confidence, sequence, cv2):
    height, width = frame.shape[:2]
    pad = max(12, int(max(w, h) * 0.35))
    x1, y1 = max(0, x - pad), max(0, y - pad)
    x2, y2 = min(width, x + w + pad), min(height, y + h + pad)
    crop = frame[y1:y2, x1:x2].copy()
    if crop.size == 0:
        return ""

    rect_x1, rect_y1 = x - x1, y - y1
    rect_x2, rect_y2 = rect_x1 + w, rect_y1 + h
    purple = (210, 48, 180)
    orange = (0, 140, 255)
    blue = (255, 90, 0)
    green = (80, 210, 70)
    white = (255, 255, 255)

    if contour is not None:
        shifted_contour = contour.copy()
        shifted_contour[:, :, 0] -= x1
        shifted_contour[:, :, 1] -= y1
        overlay = crop.copy()
        cv2.fillPoly(overlay, [shifted_contour], orange)
        crop = cv2.addWeighted(overlay, 0.36, crop, 0.64, 0)
        cv2.drawContours(crop, [shifted_contour], -1, orange, 2)
        moments = cv2.moments(shifted_contour)
        if moments["m00"]:
            centroid_x = int(moments["m10"] / moments["m00"])
            centroid_y = int(moments["m01"] / moments["m00"])
        else:
            centroid_x = rect_x1 + w // 2
            centroid_y = rect_y1 + h // 2
    else:
        centroid_x = rect_x1 + w // 2
        centroid_y = rect_y1 + h // 2

    cv2.rectangle(crop, (rect_x1, rect_y1), (rect_x2, rect_y2), purple, 3)
    for vertex_x, vertex_y in (
        (rect_x1, rect_y1),
        (rect_x2, rect_y1),
        (rect_x2, rect_y2),
        (rect_x1, rect_y2),
    ):
        cv2.circle(crop, (vertex_x, vertex_y), 7, white, -1)
        cv2.circle(crop, (vertex_x, vertex_y), 5, green, -1)
    cv2.circle(crop, (centroid_x, centroid_y), 7, white, -1)
    cv2.circle(crop, (centroid_x, centroid_y), 5, blue, -1)

    label = f"{confidence}% Pothole"
    font = cv2.FONT_HERSHEY_SIMPLEX
    text_size, baseline = cv2.getTextSize(label, font, 0.58, 2)
    text_width, text_height = text_size
    label_x = min(max(rect_x1, 0), max(0, crop.shape[1] - text_width - 10))
    label_y = max(text_height + 8, rect_y1 - 8)
    cv2.rectangle(
        crop,
        (label_x, label_y - text_height - 8),
        (label_x + text_width + 10, label_y + baseline),
        blue,
        -1,
    )
    cv2.putText(crop, label, (label_x + 5, label_y - 4), font, 0.58, white, 2, cv2.LINE_AA)

    relative_path = Path("detections") / f"analysis-{analysis.pk}-event-{sequence:03d}.jpg"
    absolute_path = Path(settings.MEDIA_ROOT) / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(absolute_path), crop)
    return str(relative_path).replace("\\", "/")


def run_analysis(analysis: VideoAnalysis):
    output = get_model().predict(analysis)
    analysis.model_version = output.model_version
    analysis.frames_processed = output.frames_processed
    analysis.inference_fps = output.inference_fps
    analysis.duration_seconds = output.duration_seconds
    analysis.status = VideoAnalysis.Status.COMPLETE
    analysis.analyzed_at = timezone.now()
    analysis.save(
        update_fields=[
            "model_version",
            "frames_processed",
            "inference_fps",
            "duration_seconds",
            "status",
            "analyzed_at",
        ]
    )

    analysis.events.all().delete()
    events = [
        DetectionEvent(
            analysis=analysis,
            event_code=f"#{38 - index:04d}",
            road_name=prediction.road_name,
            timecode_seconds=prediction.timecode_seconds,
            severity=prediction.severity,
            confidence=prediction.confidence,
            bbox_x=prediction.bbox_x,
            bbox_y=prediction.bbox_y,
            bbox_w=prediction.bbox_w,
            bbox_h=prediction.bbox_h,
            mask_polygon=prediction.mask_polygon,
            mask_centroid_x=prediction.mask_centroid_x,
            mask_centroid_y=prediction.mask_centroid_y,
            damage_length_m=prediction.damage_length_m,
            damage_width_m=prediction.damage_width_m,
            damage_perimeter_m=prediction.damage_perimeter_m,
            damage_surface_area_sqm=prediction.damage_surface_area_sqm,
            estimated_repair_area_sqm=prediction.estimated_repair_area_sqm,
            snapshot_image=prediction.snapshot_image,
        )
        for index, prediction in enumerate(output.predictions)
    ]
    DetectionEvent.objects.bulk_create(events)
    generate_engineering_recommendation(analysis)
    return analysis


def decimalize(value, places="0.01"):
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def depth_from_severity(severity):
    return {
        Severity.CRITICAL: Decimal("90.0"),
        Severity.HIGH: Decimal("65.0"),
        Severity.MEDIUM: Decimal("38.0"),
        Severity.LOW: Decimal("20.0"),
    }.get(severity, Decimal("0.0"))


def generate_engineering_recommendation(analysis: VideoAnalysis):
    events = list(analysis.events.all())
    counts = {
        Severity.CRITICAL: 0,
        Severity.HIGH: 0,
        Severity.MEDIUM: 0,
        Severity.LOW: 0,
    }
    for event in events:
        counts[event.severity] = counts.get(event.severity, 0) + 1

    detection_count = len(events)
    if not detection_count:
        EngineeringRecommendation.objects.update_or_create(
            analysis=analysis,
            defaults={
                "priority": EngineeringPriority.ROUTINE,
                "recommended_action": "No repair action recommended from this analysis",
                "repair_method": "Continue scheduled monitoring",
                "response_window": "Next routine inspection",
                "crew_type": "Inspection team",
                "detection_count": 0,
                "critical_count": 0,
                "high_count": 0,
                "medium_count": 0,
                "low_count": 0,
                "average_confidence": Decimal("0.00"),
                "estimated_affected_area_sqm": Decimal("0.00"),
                "estimated_material_volume_cum": Decimal("0.000"),
                "estimated_length_m": Decimal("0.00"),
                "estimated_width_m": Decimal("0.00"),
                "estimated_perimeter_m": Decimal("0.00"),
                "estimated_repair_area_sqm": Decimal("0.00"),
                "estimated_depth_mm": Decimal("0.0"),
                "road_name": analysis_road_name(analysis) if analysis_road_name(analysis) != "Unassigned road section" else "",
                "latitude": None,
                "longitude": None,
                "chainage_station": analysis.chainage_station,
                "photo_before_count": 0,
                "photo_during_count": 0,
                "photo_after_count": 0,
                "lanes_affected": 0,
                "traffic_volume_note": f"Field traffic count required for {analysis.route_start or 'route start'} to {analysis.route_end or 'route end'}.",
                "work_zone_requirements": "No work zone required from this analysis.",
                "weather_constraints": "Check live weather before scheduling hot mix repairs; avoid heavy rain.",
                "pavement_temperature_note": "Pavement temperature was not captured by video analysis.",
                "asphalt_quantity_tons": Decimal("0.00"),
                "aggregate_quantity_tons": Decimal("0.00"),
                "equipment_hours": Decimal("0.00"),
                "fuel_liters": Decimal("0.00"),
                "labor_hours": Decimal("0.00"),
                "traffic_control_cost": Decimal("0.00"),
                "estimated_cost_min": Decimal("0.00"),
                "estimated_cost_max": Decimal("0.00"),
                "engineering_notes": "No pavement defects met the configured confidence threshold.",
            },
        )
        return

    average_confidence = sum(event.confidence for event in events) / detection_count
    bbox_overestimate_factor = Decimal("0.18")

    def decimal_field(value):
        return Decimal(str(value or 0))

    def fallback_event_area(event):
        return Decimal(event.bbox_w * event.bbox_h) / Decimal("10000") * VISIBLE_ROAD_AREA_SQM * bbox_overestimate_factor

    surface_area = sum(decimal_field(event.damage_surface_area_sqm) for event in events)
    repair_area = sum(decimal_field(event.estimated_repair_area_sqm) for event in events)
    mask_lengths = [decimal_field(event.damage_length_m) for event in events if decimal_field(event.damage_length_m) > 0]
    mask_widths = [decimal_field(event.damage_width_m) for event in events if decimal_field(event.damage_width_m) > 0]
    estimated_perimeter = sum(decimal_field(event.damage_perimeter_m) for event in events)

    if repair_area > 0:
        surface_area = max(Decimal("0.05"), surface_area)
        repair_area = max(Decimal("0.10"), repair_area)
        estimated_length = max(mask_lengths) if mask_lengths else Decimal("0.00")
        estimated_width = max(mask_widths) if mask_widths else Decimal("0.00")
    else:
        surface_area = sum(fallback_event_area(event) for event in events)
        repair_area = max(Decimal("0.10"), surface_area)
        average_bbox_width = sum(Decimal(event.bbox_w) for event in events) / detection_count
        average_bbox_height = sum(Decimal(event.bbox_h) for event in events) / detection_count
        aspect_ratio = max(Decimal("0.35"), average_bbox_width / max(average_bbox_height, Decimal("1")))
        estimated_length = (repair_area * aspect_ratio).sqrt()
        estimated_width = max(Decimal("0.10"), repair_area / max(estimated_length, Decimal("0.01")))
        estimated_perimeter = Decimal("2.00") * (estimated_length + estimated_width)

    severity_unit_cost = {
        Severity.CRITICAL: Decimal("3800"),
        Severity.HIGH: Decimal("2800"),
        Severity.MEDIUM: Decimal("1900"),
        Severity.LOW: Decimal("1200"),
    }
    weighted_cost = sum(
        (decimal_field(event.estimated_repair_area_sqm) or fallback_event_area(event)) * severity_unit_cost[event.severity]
        for event in events
    )
    base_cost = max(Decimal("2500"), weighted_cost)
    cost_min = base_cost * Decimal("0.85")
    cost_max = base_cost * Decimal("1.35")

    critical_count = counts[Severity.CRITICAL]
    high_count = counts[Severity.HIGH]
    medium_count = counts[Severity.MEDIUM]
    low_count = counts[Severity.LOW]
    dominant_severity = max(
        (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW),
        key=lambda severity: (counts[severity], depth_from_severity(severity)),
    )
    estimated_depth_mm = depth_from_severity(dominant_severity)
    material_volume = repair_area * (estimated_depth_mm / Decimal("1000"))
    lanes = {
        min(2, max(0, int((event.bbox_x + event.bbox_w / 2) // 34)))
        for event in events
    }
    lanes_affected = max(1, len(lanes))
    road_names = sorted({event.road_name for event in events if event.road_name})
    road_name = analysis_road_name(analysis)
    if road_name == "Unassigned road section" and road_names:
        road_name = ", ".join(road_names[:2])
    snapshot_count = sum(1 for event in events if event.snapshot_image)

    asphalt_quantity_tons = material_volume * Decimal("2.35")
    aggregate_quantity_tons = material_volume * Decimal("1.85")
    equipment_hours = max(Decimal("1.00"), repair_area * Decimal("0.45") + Decimal(lanes_affected) * Decimal("0.50"))
    fuel_liters = equipment_hours * Decimal("7.50")
    labor_hours = equipment_hours * Decimal("4.00")

    if critical_count >= 5 or repair_area >= Decimal("8.00"):
        priority = EngineeringPriority.EMERGENCY
        action = "Immediate field validation and lane safety control"
        method = "Full-depth patching with traffic management"
        window = "Within 24 hours"
        crew = "Road maintenance crew with traffic control"
        traffic_control_cost = Decimal("8500.00")
        work_zone = "Full lane closure, advance warning signs, cones, flagger, and night visibility controls."
    elif critical_count or high_count >= 5 or repair_area >= Decimal("4.00"):
        priority = EngineeringPriority.URGENT
        action = "Schedule corrective repair and confirm defect dimensions on site"
        method = "Saw-cut patching or deep cold-mix repair"
        window = "1-3 days"
        crew = "Pavement repair crew"
        traffic_control_cost = Decimal("5200.00")
        work_zone = "Partial lane closure with cones, warning signs, and spotter or flagger."
    elif high_count or medium_count >= 4:
        priority = EngineeringPriority.SCHEDULED
        action = "Bundle defects into the next maintenance work order"
        method = "Localized patching and surface sealing"
        window = "7-14 days"
        crew = "Routine maintenance crew"
        traffic_control_cost = Decimal("3000.00")
        work_zone = "Shoulder or lane-side cone taper with warning signs."
    else:
        priority = EngineeringPriority.ROUTINE
        action = "Monitor and validate during routine inspection"
        method = "Surface patching if field inspection confirms progression"
        window = "Next maintenance cycle"
        crew = "Inspection team"
        traffic_control_cost = Decimal("1200.00")
        work_zone = "Short-duration mobile work zone if repair is confirmed."

    notes = (
        f"Computed from {detection_count} detections. "
        f"Severity mix: {critical_count} critical, {high_count} high, {medium_count} medium, {low_count} low. "
        "Length, width, perimeter, surface area, and repair area are planning estimates derived from segmentation masks and should be field-verified. "
        "GPS coordinates, traffic volume, weather, and pavement temperature require field capture or live sensor integration."
    )

    EngineeringRecommendation.objects.update_or_create(
        analysis=analysis,
        defaults={
            "priority": priority,
            "recommended_action": action,
            "repair_method": method,
            "response_window": window,
            "crew_type": crew,
            "detection_count": detection_count,
            "critical_count": critical_count,
            "high_count": high_count,
            "medium_count": medium_count,
            "low_count": low_count,
            "average_confidence": decimalize(average_confidence),
            "estimated_affected_area_sqm": decimalize(surface_area),
            "estimated_material_volume_cum": decimalize(material_volume, "0.001"),
            "estimated_length_m": decimalize(estimated_length),
            "estimated_width_m": decimalize(estimated_width),
            "estimated_perimeter_m": decimalize(estimated_perimeter),
            "estimated_repair_area_sqm": decimalize(repair_area),
            "estimated_depth_mm": decimalize(estimated_depth_mm, "0.1"),
            "road_name": road_name,
            "latitude": None,
            "longitude": None,
            "chainage_station": analysis.chainage_station,
            "photo_before_count": snapshot_count,
            "photo_during_count": 0,
            "photo_after_count": 0,
            "lanes_affected": lanes_affected,
            "traffic_volume_note": f"Field traffic count required for {analysis.route_start or 'route start'} to {analysis.route_end or 'route end'}.",
            "work_zone_requirements": work_zone,
            "weather_constraints": "Check live weather before scheduling hot mix repairs; avoid heavy rain for hot mix asphalt.",
            "pavement_temperature_note": "Pavement temperature was not captured by video analysis.",
            "asphalt_quantity_tons": decimalize(asphalt_quantity_tons),
            "aggregate_quantity_tons": decimalize(aggregate_quantity_tons),
            "equipment_hours": decimalize(equipment_hours),
            "fuel_liters": decimalize(fuel_liters),
            "labor_hours": decimalize(labor_hours),
            "traffic_control_cost": decimalize(traffic_control_cost),
            "estimated_cost_min": decimalize(cost_min),
            "estimated_cost_max": decimalize(cost_max),
            "engineering_notes": notes,
        },
    )
