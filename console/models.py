from django.conf import settings
from django.db import models
from django.utils import timezone
import uuid


def dataset_image_id():
    return f"DS-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


class AppRole(models.TextChoices):
    ADMIN = "admin", "admin"
    ENGINEER = "engineer", "engineer"
    VIEWER = "viewer", "viewer"


class Severity(models.TextChoices):
    LOW = "low", "low"
    MEDIUM = "medium", "medium"
    HIGH = "high", "high"
    CRITICAL = "critical", "critical"


class ReportStatus(models.TextChoices):
    OPEN = "open", "open"
    IN_PROGRESS = "in-progress", "in-progress"
    RESOLVED = "resolved", "resolved"


class DeviceStatus(models.TextChoices):
    ONLINE = "online", "online"
    OFFLINE = "offline", "offline"


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    full_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.full_name or self.email


class UserRole(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="console_role")
    role = models.CharField(max_length=20, choices=AppRole.choices, default=AppRole.VIEWER)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} ({self.role})"


class PotholeReport(models.Model):
    lat = models.FloatField()
    lng = models.FloatField()
    severity = models.CharField(max_length=20, choices=Severity.choices)
    status = models.CharField(max_length=20, choices=ReportStatus.choices, default=ReportStatus.OPEN)
    image_url = models.URLField(blank=True)
    detected_at = models.DateTimeField()
    city = models.CharField(max_length=120)
    device_id = models.CharField(max_length=80)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-detected_at"]

    def __str__(self):
        return f"{self.city} {self.severity} report"


class FleetDevice(models.Model):
    id = models.CharField(max_length=80, primary_key=True)
    name = models.CharField(max_length=160)
    city = models.CharField(max_length=120)
    status = models.CharField(max_length=20, choices=DeviceStatus.choices)
    last_seen_at = models.DateTimeField()
    fps = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    model_version = models.CharField(max_length=80)
    stream_url = models.CharField(max_length=500, blank=True)
    road_section = models.CharField(max_length=180, blank=True)
    chainage_station = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["city", "id"]

    def __str__(self):
        return self.name


class VideoDatasetSample(models.Model):
    name = models.CharField(max_length=160)
    road_name = models.CharField(max_length=160)
    barangay = models.CharField(max_length=120, blank=True)
    city = models.CharField(max_length=120)
    file_name = models.CharField(max_length=180, blank=True)
    duration_seconds = models.PositiveIntegerField(default=40)
    frame_count = models.PositiveIntegerField(default=879)
    fps = models.DecimalField(max_digits=5, decimal_places=2, default=59)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["road_name", "name"]

    def __str__(self):
        return f"{self.name} - {self.road_name}"


class VideoAnalysis(models.Model):
    class SourceType(models.TextChoices):
        DATASET = "dataset", "dataset"
        UPLOAD = "upload", "upload"
        LIVE = "live", "live"

    class Status(models.TextChoices):
        READY = "ready", "ready"
        COMPLETE = "complete", "complete"
        FAILED = "failed", "failed"

    source_type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.DATASET)
    dataset_sample = models.ForeignKey(VideoDatasetSample, on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_video = models.FileField(upload_to="videos/", blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    road_name = models.CharField(max_length=160, blank=True)
    barangay = models.CharField(max_length=120, blank=True)
    city = models.CharField(max_length=120, blank=True)
    route_start = models.CharField(max_length=180, blank=True)
    route_end = models.CharField(max_length=180, blank=True)
    chainage_station = models.CharField(max_length=80, blank=True)
    min_confidence = models.PositiveSmallIntegerField(default=85)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.READY)
    model_version = models.CharField(max_length=80, default="opencv-pending")
    frames_processed = models.PositiveIntegerField(default=0)
    inference_fps = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    duration_seconds = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    analyzed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def critical_count(self):
        return self.events.filter(severity=Severity.CRITICAL).count()

    def __str__(self):
        source = self.original_filename or self.dataset_sample or self.source_type
        return f"Analysis {self.pk} - {source}"


class DetectionEvent(models.Model):
    analysis = models.ForeignKey(VideoAnalysis, on_delete=models.CASCADE, related_name="events")
    event_code = models.CharField(max_length=12)
    road_name = models.CharField(max_length=160)
    timecode_seconds = models.DecimalField(max_digits=6, decimal_places=2)
    severity = models.CharField(max_length=20, choices=Severity.choices)
    confidence = models.PositiveSmallIntegerField()
    bbox_x = models.PositiveSmallIntegerField()
    bbox_y = models.PositiveSmallIntegerField()
    bbox_w = models.PositiveSmallIntegerField()
    bbox_h = models.PositiveSmallIntegerField()
    mask_polygon = models.TextField(blank=True)
    mask_centroid_x = models.PositiveSmallIntegerField(default=50)
    mask_centroid_y = models.PositiveSmallIntegerField(default=50)
    damage_length_m = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    damage_width_m = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    damage_perimeter_m = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    damage_surface_area_sqm = models.DecimalField(max_digits=9, decimal_places=3, default=0)
    estimated_repair_area_sqm = models.DecimalField(max_digits=9, decimal_places=3, default=0)
    snapshot_image = models.ImageField(upload_to="detections/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timecode_seconds", "-confidence"]

    @property
    def timecode_label(self):
        return f"t={self.timecode_seconds}s"

    def __str__(self):
        return f"{self.event_code} {self.road_name} {self.confidence}%"


class EngineeringPriority(models.TextChoices):
    ROUTINE = "routine", "routine"
    SCHEDULED = "scheduled", "scheduled"
    URGENT = "urgent", "urgent"
    EMERGENCY = "emergency", "emergency"


class EngineeringRecommendation(models.Model):
    analysis = models.OneToOneField(VideoAnalysis, on_delete=models.CASCADE, related_name="engineering_recommendation")
    priority = models.CharField(max_length=20, choices=EngineeringPriority.choices)
    recommended_action = models.CharField(max_length=180)
    repair_method = models.CharField(max_length=180)
    response_window = models.CharField(max_length=80)
    crew_type = models.CharField(max_length=120)
    detection_count = models.PositiveIntegerField(default=0)
    critical_count = models.PositiveIntegerField(default=0)
    high_count = models.PositiveIntegerField(default=0)
    medium_count = models.PositiveIntegerField(default=0)
    low_count = models.PositiveIntegerField(default=0)
    average_confidence = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    estimated_affected_area_sqm = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    estimated_material_volume_cum = models.DecimalField(max_digits=9, decimal_places=3, default=0)
    estimated_length_m = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    estimated_width_m = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    estimated_perimeter_m = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    estimated_repair_area_sqm = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    estimated_depth_mm = models.DecimalField(max_digits=7, decimal_places=1, default=0)
    road_name = models.CharField(max_length=160, blank=True)
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    chainage_station = models.CharField(max_length=80, blank=True)
    photo_before_count = models.PositiveIntegerField(default=0)
    photo_during_count = models.PositiveIntegerField(default=0)
    photo_after_count = models.PositiveIntegerField(default=0)
    lanes_affected = models.PositiveSmallIntegerField(default=0)
    traffic_volume_note = models.CharField(max_length=160, blank=True)
    work_zone_requirements = models.CharField(max_length=220, blank=True)
    weather_constraints = models.CharField(max_length=220, blank=True)
    pavement_temperature_note = models.CharField(max_length=160, blank=True)
    asphalt_quantity_tons = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    aggregate_quantity_tons = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    equipment_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    fuel_liters = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    labor_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    traffic_control_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    estimated_cost_min = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    estimated_cost_max = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    engineering_notes = models.TextField(blank=True)
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"Engineering recommendation for analysis {self.analysis_id}"


class DatasetSplit(models.TextChoices):
    TRAIN = "train", "train"
    VAL = "val", "val"
    TEST = "test", "test"


class DatasetImageStatus(models.TextChoices):
    UNANNOTATED = "unannotated", "unannotated"
    PARTIAL = "partial", "partially annotated"
    FULL = "full", "fully annotated"
    APPROVED = "approved", "approved"
    REJECTED = "rejected", "rejected"


class DatasetImageSource(models.TextChoices):
    UPLOAD = "upload", "upload"
    DETECTION = "detection", "detection"
    AUGMENTED = "augmented", "augmented"


class DatasetVersion(models.Model):
    version_number = models.PositiveIntegerField(unique=True)
    train_percent = models.PositiveSmallIntegerField(default=70)
    val_percent = models.PositiveSmallIntegerField(default=20)
    test_percent = models.PositiveSmallIntegerField(default=10)
    total_images = models.PositiveIntegerField(default=0)
    total_annotations = models.PositiveIntegerField(default=0)
    notes = models.CharField(max_length=220, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version_number"]

    def __str__(self):
        return f"Dataset v{self.version_number}"


class DatasetImage(models.Model):
    dataset_id = models.CharField(max_length=32, unique=True, default=dataset_image_id)
    image = models.ImageField(upload_to="training_dataset/originals/%Y/%m/%d/")
    labeled_preview = models.ImageField(upload_to="training_dataset/labeled/%Y/%m/%d/", blank=True)
    original_filename = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64, unique=True)
    file_size = models.PositiveIntegerField(default=0)
    file_type = models.CharField(max_length=12)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    split = models.CharField(max_length=12, choices=DatasetSplit.choices, default=DatasetSplit.TRAIN)
    status = models.CharField(max_length=24, choices=DatasetImageStatus.choices, default=DatasetImageStatus.UNANNOTATED)
    source = models.CharField(max_length=20, choices=DatasetImageSource.choices, default=DatasetImageSource.UPLOAD)
    source_group = models.CharField(
        max_length=120,
        blank=True,
        db_index=True,
        help_text="Stable route, survey, or source-video identifier used to prevent train/test leakage.",
    )
    dataset_version = models.ForeignKey(DatasetVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name="images")
    parent_image = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="augmented_images")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="dataset_uploads")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="dataset_reviews")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["-uploaded_at"]

    @property
    def pothole_count(self):
        return self.annotations.count()

    def __str__(self):
        return f"{self.dataset_id} - {self.original_filename}"


class PotholeAnnotation(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "manual"
        PREDICTED = "predicted", "predicted"
        AUGMENTED = "augmented", "augmented"

    image = models.ForeignKey(DatasetImage, on_delete=models.CASCADE, related_name="annotations")
    class_id = models.PositiveSmallIntegerField(default=0)
    label = models.CharField(max_length=32, default="pothole")
    center_x = models.DecimalField(max_digits=9, decimal_places=6)
    center_y = models.DecimalField(max_digits=9, decimal_places=6)
    width = models.DecimalField(max_digits=9, decimal_places=6)
    height = models.DecimalField(max_digits=9, decimal_places=6)
    segmentation_points = models.JSONField(default=list, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["image_id", "id"]

    @property
    def yolo_line(self):
        points = self.segmentation_points or [
            [float(self.center_x - self.width / 2), float(self.center_y - self.height / 2)],
            [float(self.center_x + self.width / 2), float(self.center_y - self.height / 2)],
            [float(self.center_x + self.width / 2), float(self.center_y + self.height / 2)],
            [float(self.center_x - self.width / 2), float(self.center_y + self.height / 2)],
        ]
        values = [self.class_id]
        for x, y in points:
            values.extend([round(max(0, min(1, float(x))), 6), round(max(0, min(1, float(y))), 6)])
        return " ".join(str(value) for value in values)

    def __str__(self):
        return f"{self.image.dataset_id} {self.label}"


class DatasetAugmentationJob(models.Model):
    source_version = models.ForeignKey(DatasetVersion, on_delete=models.SET_NULL, null=True, blank=True)
    options = models.JSONField(default=dict, blank=True)
    generated_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, default="ready")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Augmentation job {self.pk}"


class TrainingSession(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "queued"
        RUNNING = "running", "running"
        COMPLETE = "complete", "complete"
        FAILED = "failed", "failed"

    model_name = models.CharField(max_length=20, default="yolo11n")
    dataset_version = models.ForeignKey(DatasetVersion, on_delete=models.SET_NULL, null=True, blank=True)
    epochs = models.PositiveIntegerField(default=50)
    batch_size = models.PositiveIntegerField(default=16)
    image_size = models.PositiveIntegerField(default=640)
    learning_rate = models.DecimalField(max_digits=8, decimal_places=6, default=0.01)
    device = models.CharField(max_length=60, default="cpu")
    patience = models.PositiveIntegerField(default=20)
    workers = models.PositiveIntegerField(default=2)
    optimizer = models.CharField(max_length=20, default="AdamW")
    augmentation_profile = models.CharField(max_length=20, default="balanced")
    seed = models.PositiveIntegerField(default=42)
    freeze_layers = models.PositiveSmallIntegerField(default=0)
    dataset_manifest = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    progress = models.PositiveSmallIntegerField(default=0)
    current_epoch = models.PositiveIntegerField(default=0)
    train_loss = models.DecimalField(max_digits=10, decimal_places=5, null=True, blank=True)
    val_loss = models.DecimalField(max_digits=10, decimal_places=5, null=True, blank=True)
    precision = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    recall = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    map50 = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    map5095 = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    model_file = models.CharField(max_length=255, blank=True)
    model_task = models.CharField(max_length=20, blank=True)
    results_dir = models.CharField(max_length=255, blank=True)
    is_active_video_model = models.BooleanField(default=False)
    is_validated = models.BooleanField(default=False)
    model_sha256 = models.CharField(max_length=64, blank=True)
    validation_notes = models.TextField(blank=True)
    local_evaluation_at = models.DateTimeField(null=True, blank=True)
    local_test_images = models.PositiveIntegerField(default=0)
    local_precision = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    local_recall = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    local_map50 = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    local_map5095 = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    local_metrics = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.model_name} on {self.dataset_version or 'dataset'}"


class VideoVisualizerStatus(models.TextChoices):
    QUEUED = "queued", "queued"
    RUNNING = "running", "running"
    RETRYING = "retrying", "retrying"
    COMPLETE = "complete", "complete"
    FAILED = "failed", "failed"
    CANCELLED = "cancelled", "cancelled"


class VideoVisualizerMode(models.TextChoices):
    REAL_TIME = "real-time", "real-time"
    ACCURATE = "accurate", "accurate"


class VideoSourceType(models.TextChoices):
    UPLOAD = "upload", "upload"
    WEBCAM = "webcam", "webcam"
    DASHCAM = "dashcam", "dashcam"
    LIVE_STREAM = "live-stream", "live-stream"


class VideoTrackReviewStatus(models.TextChoices):
    UNRESOLVED = "unresolved", "unresolved"
    CONFIRMED = "confirmed", "confirmed"
    REJECTED = "rejected", "rejected"


class VideoDetectionSeverity(models.TextChoices):
    LOW = "low", "low"
    MODERATE = "moderate", "moderate"
    HIGH = "high", "high"
    CRITICAL = "critical", "critical"


class TrainingModelArchitecture(models.TextChoices):
    YOLO11N_SEG = "yolo11n-seg", "YOLO11n segmentation"
    YOLO11S_SEG = "yolo11s-seg", "YOLO11s segmentation"
    YOLO11M_SEG = "yolo11m-seg", "YOLO11m segmentation"
    YOLO11L_SEG = "yolo11l-seg", "YOLO11l segmentation"
    YOLO11X_SEG = "yolo11x-seg", "YOLO11x segmentation"
    YOLO26N_SEG = "yolo26n-seg", "YOLO26n segmentation"
    YOLO26S_SEG = "yolo26s-seg", "YOLO26s segmentation"
    YOLO26M_SEG = "yolo26m-seg", "YOLO26m segmentation"
    YOLO26L_SEG = "yolo26l-seg", "YOLO26l segmentation"
    YOLO26X_SEG = "yolo26x-seg", "YOLO26x segmentation"


class AnalyzerConfiguration(models.Model):
    """Singleton configuration for the operational Video Analyzer."""

    model_session = models.ForeignKey(TrainingSession, on_delete=models.SET_NULL, null=True, blank=True)
    training_model = models.CharField(
        max_length=20,
        choices=TrainingModelArchitecture.choices,
        default=TrainingModelArchitecture.YOLO11S_SEG,
    )
    mode = models.CharField(max_length=20, choices=VideoVisualizerMode.choices, default=VideoVisualizerMode.ACCURATE)
    confidence_threshold = models.PositiveSmallIntegerField(default=30)
    iou_threshold = models.PositiveSmallIntegerField(default=45)
    device = models.CharField(max_length=60, default="cpu")
    input_resolution = models.PositiveIntegerField(default=512)
    frame_skip = models.PositiveSmallIntegerField(default=1)
    half_precision = models.BooleanField(default=False)
    tracker = models.CharField(max_length=60, default="bytetrack.yaml")
    max_detections = models.PositiveIntegerField(default=100)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    include_road_damage = models.BooleanField(default=True)
    min_track_appearances = models.PositiveSmallIntegerField(default=2)
    dedup_iou_threshold = models.DecimalField(max_digits=4, decimal_places=3, default=0.35)
    dedup_max_gap_frames = models.PositiveIntegerField(default=90)
    show_labels = models.BooleanField(default=True)
    show_confidence = models.BooleanField(default=True)
    show_tracking_ids = models.BooleanField(default=True)
    show_boxes = models.BooleanField(default=True)
    show_gps_overlay = models.BooleanField(default=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Video Analyzer settings"


class VideoVisualizerAnalysis(models.Model):
    legacy_analysis = models.OneToOneField(
        VideoAnalysis,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="unified_analysis",
    )
    video = models.FileField(upload_to="video_visualizer/uploads/%Y/%m/%d/", blank=True)
    processed_video = models.FileField(upload_to="video_visualizer/processed/%Y/%m/%d/", blank=True)
    gps_file = models.FileField(upload_to="video_visualizer/gps/%Y/%m/%d/", blank=True)
    source_url = models.CharField(max_length=500, blank=True)
    original_filename = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64, db_index=True)
    file_size = models.PositiveIntegerField(default=0)
    file_type = models.CharField(max_length=12)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    fps = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    duration_seconds = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    frame_count = models.PositiveIntegerField(default=0)
    source_type = models.CharField(max_length=20, choices=VideoSourceType.choices, default=VideoSourceType.UPLOAD)
    mode = models.CharField(max_length=20, choices=VideoVisualizerMode.choices, default=VideoVisualizerMode.ACCURATE)
    model_session = models.ForeignKey(TrainingSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="video_analyses")
    confidence_threshold = models.PositiveSmallIntegerField(default=50)
    iou_threshold = models.PositiveSmallIntegerField(default=45)
    device = models.CharField(max_length=60, default="cpu")
    input_resolution = models.PositiveIntegerField(default=640)
    frame_skip = models.PositiveSmallIntegerField(default=1)
    batch_size = models.PositiveIntegerField(default=1)
    half_precision = models.BooleanField(default=False)
    tracker = models.CharField(max_length=60, default="iou")
    output_quality = models.PositiveSmallIntegerField(default=85)
    max_detections = models.PositiveIntegerField(default=100)
    include_road_damage = models.BooleanField(default=True)
    min_track_appearances = models.PositiveSmallIntegerField(default=2)
    dedup_iou_threshold = models.DecimalField(max_digits=4, decimal_places=3, default=0.35)
    dedup_max_gap_frames = models.PositiveIntegerField(default=90)
    show_labels = models.BooleanField(default=True)
    show_confidence = models.BooleanField(default=True)
    show_tracking_ids = models.BooleanField(default=True)
    show_boxes = models.BooleanField(default=True)
    show_gps_overlay = models.BooleanField(default=True)
    road_section = models.CharField(max_length=180, blank=True)
    chainage_station = models.CharField(max_length=80, blank=True)
    calibration_m_per_pixel = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    calibration_notes = models.CharField(max_length=255, blank=True)
    route_metadata = models.JSONField(default=dict, blank=True)
    gps_points = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=VideoVisualizerStatus.choices, default=VideoVisualizerStatus.QUEUED)
    current_frame = models.PositiveIntegerField(default=0)
    frames_processed = models.PositiveIntegerField(default=0)
    processing_time_ms = models.PositiveIntegerField(default=0)
    average_processing_fps = models.DecimalField(max_digits=8, decimal_places=3, default=0)
    source_processing_fps = models.DecimalField(max_digits=8, decimal_places=3, default=0)
    realtime_factor = models.DecimalField(max_digits=8, decimal_places=3, default=0)
    effective_frame_skip = models.PositiveSmallIntegerField(default=1)
    raw_track_count = models.PositiveIntegerField(default=0)
    discarded_short_tracks = models.PositiveIntegerField(default=0)
    duplicate_tracks_merged = models.PositiveIntegerField(default=0)
    total_unique_potholes = models.PositiveIntegerField(default=0)
    total_detections = models.PositiveIntegerField(default=0)
    average_confidence = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    highest_confidence = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    lowest_confidence = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    timeline_markers = models.JSONField(default=list, blank=True)
    frame_detections = models.JSONField(default=list, blank=True)
    frame_detections_artifact = models.FileField(upload_to="video_visualizer/detections/%Y/%m/%d/", blank=True)
    is_continuous = models.BooleanField(default=False)
    stop_requested = models.BooleanField(default=False)
    live_preview_frame = models.ImageField(upload_to="video_visualizer/live/%Y/%m/%d/", blank=True)
    error_message = models.TextField(blank=True)
    error_history = models.JSONField(default=list, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    worker_id = models.CharField(max_length=120, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    ground_truth_pothole_count = models.PositiveIntegerField(null=True, blank=True)
    ground_truth_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def has_model_file(self):
        return bool(self.model_session and self.model_session.model_file)

    def __str__(self):
        return f"Video visualizer {self.pk} - {self.original_filename}"


class VideoPotholeTrack(models.Model):
    class MeasurementBasis(models.TextChoices):
        VISUAL_ESTIMATE = "visual-estimate", "visual estimate only"
        CALIBRATED = "calibrated", "camera-calibrated estimate"
        FIELD_MEASURED = "field-measured", "field measured"

    analysis = models.ForeignKey(VideoVisualizerAnalysis, on_delete=models.CASCADE, related_name="tracks")
    track_id = models.PositiveIntegerField()
    label = models.CharField(max_length=32, default="Pothole")
    first_frame = models.PositiveIntegerField(default=0)
    last_frame = models.PositiveIntegerField(default=0)
    first_timestamp = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    last_timestamp = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    appearance_count = models.PositiveIntegerField(default=0)
    average_confidence = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    highest_confidence = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    lowest_confidence = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    best_frame = models.PositiveIntegerField(default=0)
    best_bbox = models.JSONField(default=dict, blank=True)
    best_segmentation_points = models.JSONField(default=list, blank=True)
    severity = models.CharField(max_length=20, choices=VideoDetectionSeverity.choices, default=VideoDetectionSeverity.LOW)
    relative_bbox_size = models.DecimalField(max_digits=8, decimal_places=5, default=0)
    measurement_basis = models.CharField(
        max_length=24,
        choices=MeasurementBasis.choices,
        default=MeasurementBasis.VISUAL_ESTIMATE,
    )
    estimated_length_m = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    estimated_width_m = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    estimated_surface_area_sqm = models.DecimalField(max_digits=9, decimal_places=3, null=True, blank=True)
    measured_depth_mm = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    road_section = models.CharField(max_length=180, blank=True)
    review_status = models.CharField(max_length=20, choices=VideoTrackReviewStatus.choices, default=VideoTrackReviewStatus.UNRESOLVED)
    remarks = models.TextField(blank=True)
    snapshot_crop = models.ImageField(upload_to="video_visualizer/snapshots/crops/%Y/%m/%d/", blank=True)
    snapshot_frame = models.ImageField(upload_to="video_visualizer/snapshots/frames/%Y/%m/%d/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["track_id"]
        constraints = [
            models.UniqueConstraint(fields=["analysis", "track_id"], name="unique_video_track_per_analysis"),
        ]

    def __str__(self):
        return f"{self.analysis_id} T{self.track_id}"


class DetectionTest(models.Model):
    image = models.ImageField(upload_to="training_dataset/tests/%Y/%m/%d/")
    result_image = models.ImageField(upload_to="training_dataset/test_results/%Y/%m/%d/", blank=True)
    original_filename = models.CharField(max_length=255)
    model_session = models.ForeignKey(TrainingSession, on_delete=models.SET_NULL, null=True, blank=True)
    confidence_threshold = models.PositiveSmallIntegerField(default=50)
    iou_threshold = models.PositiveSmallIntegerField(default=45)
    detections = models.JSONField(default=list, blank=True)
    detection_count = models.PositiveIntegerField(default=0)
    processing_time_ms = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, default="pending")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Detection test {self.pk} - {self.original_filename}"


class DatasetAuditLog(models.Model):
    action = models.CharField(max_length=60)
    message = models.CharField(max_length=255)
    dataset_image = models.ForeignKey(DatasetImage, on_delete=models.SET_NULL, null=True, blank=True)
    dataset_version = models.ForeignKey(DatasetVersion, on_delete=models.SET_NULL, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.message
