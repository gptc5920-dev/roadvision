from django import forms
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q
from django.utils import timezone
from urllib.parse import urlparse

from .models import (
    AnalyzerConfiguration,
    AppRole,
    DeviceStatus,
    FleetDevice,
    ReportStatus,
    TrainingModelArchitecture,
    TrainingSession,
    VideoAnalysis,
    VideoDatasetSample,
    VideoSourceType,
    VideoVisualizerMode,
)
from .readiness import training_dataset_manifest


class SignInForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        password = cleaned.get("password")
        if email and password:
            user = authenticate(username=email, password=password)
            if user is None:
                raise forms.ValidationError("Invalid email or password.")
            cleaned["user"] = user
        return cleaned


class ReportStatusForm(forms.Form):
    status = forms.ChoiceField(choices=ReportStatus.choices)


class RoleForm(forms.Form):
    role = forms.ChoiceField(choices=AppRole.choices)


class PersonnelAccountForm(forms.Form):
    full_name = forms.CharField(max_length=255)
    email = forms.EmailField(max_length=254)
    role = forms.ChoiceField(choices=AppRole.choices, initial=AppRole.ENGINEER)
    temporary_password = forms.CharField(
        min_length=8,
        max_length=128,
        widget=forms.PasswordInput(render_value=False),
        help_text="The new user can sign in immediately with this temporary password.",
    )
    confirm_password = forms.CharField(
        max_length=128,
        widget=forms.PasswordInput(render_value=False),
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        user_model = get_user_model()
        if user_model.objects.filter(username__iexact=email).exists() or user_model.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("temporary_password")
        confirmation = cleaned.get("confirm_password")
        if password and confirmation and password != confirmation:
            self.add_error("confirm_password", "Passwords do not match.")
        if password:
            candidate = get_user_model()(username=cleaned.get("email", ""), email=cleaned.get("email", ""))
            validate_password(password, user=candidate)
        return cleaned

    def save(self):
        user_model = get_user_model()
        email = self.cleaned_data["email"]
        full_name = self.cleaned_data["full_name"].strip()
        name_parts = full_name.split(None, 1)
        user = user_model.objects.create_user(
            username=email,
            email=email,
            password=self.cleaned_data["temporary_password"],
            first_name=name_parts[0] if name_parts else "",
            last_name=name_parts[1] if len(name_parts) > 1 else "",
        )
        profile = user.profile
        profile.full_name = full_name
        profile.email = email
        profile.save(update_fields=["full_name", "email"])
        user.console_role.role = self.cleaned_data["role"]
        user.console_role.save(update_fields=["role"])
        return user


class FleetDeviceForm(forms.Form):
    device_id = forms.SlugField(max_length=80, help_text="Stable identifier, for example CAM-CAV-01.")
    name = forms.CharField(max_length=160)
    city = forms.CharField(max_length=120)
    status = forms.ChoiceField(choices=DeviceStatus.choices, initial=DeviceStatus.ONLINE)
    stream_url = forms.CharField(
        max_length=500,
        required=False,
        help_text="RTSP, HTTP, or HTTPS camera feed. Credentials must not be included in the URL.",
    )
    road_section = forms.CharField(max_length=180, required=False)
    chainage_station = forms.CharField(max_length=80, required=False)
    fps = forms.DecimalField(min_value=0, max_digits=5, decimal_places=2, initial=30)
    model_version = forms.CharField(max_length=80, required=False, initial="fleet-camera")

    def clean_stream_url(self):
        stream_url = self.cleaned_data.get("stream_url", "").strip()
        if not stream_url:
            return ""
        parsed = urlparse(stream_url)
        if parsed.scheme.lower() not in {"rtsp", "http", "https"} or not parsed.hostname:
            raise forms.ValidationError("Stream URL must start with rtsp://, http://, or https://.")
        if parsed.username or parsed.password:
            raise forms.ValidationError("Do not include camera credentials in the stream URL.")
        return stream_url

    def save(self):
        device, _created = FleetDevice.objects.update_or_create(
            pk=self.cleaned_data["device_id"],
            defaults={
                "name": self.cleaned_data["name"].strip(),
                "city": self.cleaned_data["city"].strip(),
                "status": self.cleaned_data["status"],
                "last_seen_at": timezone.now(),
                "fps": self.cleaned_data["fps"],
                "model_version": self.cleaned_data.get("model_version", "").strip() or "fleet-camera",
                "stream_url": self.cleaned_data.get("stream_url", ""),
                "road_section": self.cleaned_data.get("road_section", "").strip(),
                "chainage_station": self.cleaned_data.get("chainage_station", "").strip(),
            },
        )
        return device


class VideoAnalysisForm(forms.Form):
    dataset_sample = forms.ModelChoiceField(
        queryset=VideoDatasetSample.objects.all(),
        required=False,
        empty_label="Use uploaded footage",
    )
    video = forms.FileField(required=False)
    road_name = forms.CharField(max_length=160, required=True)
    barangay = forms.CharField(max_length=120, required=False)
    city = forms.CharField(max_length=120, required=False)
    route_start = forms.CharField(max_length=180, required=True)
    route_end = forms.CharField(max_length=180, required=True)
    chainage_station = forms.CharField(max_length=80, required=False)
    min_confidence = forms.IntegerField(min_value=50, max_value=99, initial=85)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholders = {
            "road_name": "e.g. Governor's Drive",
            "barangay": "Barangay",
            "city": "City / municipality",
            "route_start": "Start station, address, or lat,lng",
            "route_end": "End station, address, or lat,lng",
            "chainage_station": "e.g. CH 1+240 to CH 1+680",
        }
        for field_name, placeholder in placeholders.items():
            self.fields[field_name].widget.attrs["placeholder"] = placeholder

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("dataset_sample") and not cleaned.get("video"):
            raise forms.ValidationError("Select a dataset clip or upload a video file.")
        if cleaned.get("video") and (not cleaned.get("route_start") or not cleaned.get("route_end")):
            raise forms.ValidationError("Upload route must include start and end points.")
        return cleaned

    def save(self, user):
        upload = self.cleaned_data.get("video")
        sample = self.cleaned_data.get("dataset_sample")
        road_name = self.cleaned_data.get("road_name") or (sample.road_name if sample else "")
        analysis = VideoAnalysis.objects.create(
            source_type=VideoAnalysis.SourceType.UPLOAD if upload else VideoAnalysis.SourceType.DATASET,
            dataset_sample=sample,
            uploaded_video=upload or "",
            original_filename=getattr(upload, "name", "") or (sample.file_name if sample else ""),
            road_name=road_name,
            barangay=self.cleaned_data.get("barangay", ""),
            city=self.cleaned_data.get("city") or (sample.city if sample else ""),
            route_start=self.cleaned_data.get("route_start", ""),
            route_end=self.cleaned_data.get("route_end", ""),
            chainage_station=self.cleaned_data.get("chainage_station", ""),
            min_confidence=self.cleaned_data["min_confidence"],
            created_by=user,
        )
        return analysis


class DatasetUploadForm(forms.Form):
    train_percent = forms.IntegerField(min_value=1, max_value=98, initial=70)
    val_percent = forms.IntegerField(min_value=1, max_value=98, initial=20)
    test_percent = forms.IntegerField(min_value=1, max_value=98, initial=10)
    notes = forms.CharField(max_length=220, required=False)
    source_group = forms.SlugField(
        max_length=120,
        required=True,
        help_text="Required route, survey, or source-video ID. Upload different surveys separately; one group stays in one split.",
    )

    def clean(self):
        cleaned = super().clean()
        total = sum(int(cleaned.get(name) or 0) for name in ["train_percent", "val_percent", "test_percent"])
        if total != 100:
            raise forms.ValidationError("Training, validation, and testing split percentages must total 100%.")
        return cleaned


class TrainingConfigForm(forms.Form):
    MODEL_CHOICES = TrainingModelArchitecture.choices

    OPTIMIZER_CHOICES = [
        ("AdamW", "AdamW (recommended)"),
        ("auto", "Ultralytics auto"),
        ("SGD", "SGD"),
    ]
    AUGMENTATION_CHOICES = [
        ("balanced", "Balanced road scenes"),
        ("conservative", "Conservative"),
        ("aggressive", "Aggressive / small dataset"),
    ]

    model_name = forms.ChoiceField(choices=MODEL_CHOICES, initial="yolo11s-seg")
    epochs = forms.IntegerField(min_value=1, max_value=1000, initial=100)
    batch_size = forms.IntegerField(min_value=1, max_value=256, initial=16)
    image_size = forms.IntegerField(min_value=128, max_value=2048, initial=512)
    learning_rate = forms.DecimalField(min_value=0.000001, max_digits=8, decimal_places=6, initial=0.001)
    device = forms.CharField(max_length=60, initial="cpu")
    patience = forms.IntegerField(min_value=0, max_value=300, initial=30)
    workers = forms.IntegerField(min_value=0, max_value=32, initial=2)
    optimizer = forms.ChoiceField(choices=OPTIMIZER_CHOICES, initial="AdamW")
    augmentation_profile = forms.ChoiceField(choices=AUGMENTATION_CHOICES, initial="balanced")
    seed = forms.IntegerField(min_value=0, max_value=2_147_483_647, initial=42)
    freeze_layers = forms.IntegerField(
        min_value=0,
        max_value=24,
        initial=0,
        help_text="Freeze early backbone layers for very small datasets; 0 trains the full model.",
    )

    def save(self, user, dataset_version):
        return TrainingSession.objects.create(
            model_name=self.cleaned_data["model_name"],
            dataset_version=dataset_version,
            epochs=self.cleaned_data["epochs"],
            batch_size=self.cleaned_data["batch_size"],
            image_size=self.cleaned_data["image_size"],
            learning_rate=self.cleaned_data["learning_rate"],
            device=self.cleaned_data["device"],
            patience=self.cleaned_data["patience"],
            workers=self.cleaned_data["workers"],
            optimizer=self.cleaned_data["optimizer"],
            augmentation_profile=self.cleaned_data["augmentation_profile"],
            seed=self.cleaned_data["seed"],
            freeze_layers=self.cleaned_data["freeze_layers"],
            dataset_manifest=training_dataset_manifest(),
            created_by=user,
        )


class DetectionTestForm(forms.Form):
    test_image = forms.FileField(required=False)
    confidence_threshold = forms.IntegerField(min_value=1, max_value=99, initial=50)
    iou_threshold = forms.IntegerField(min_value=1, max_value=99, initial=45)


class VideoVisualizerUploadForm(forms.Form):
    SENSITIVITY_CHOICES = [
        (30, "Standard review - 30%"),
        (25, "High recall - 25% (manual review required)"),
        (35, "Conservative - 35%"),
        (50, "Strict - 50% (lower recall)"),
    ]

    video = forms.FileField(required=False)
    gps_file = forms.FileField(required=False)
    stream_url = forms.CharField(max_length=500, required=False)
    source_type = forms.ChoiceField(choices=VideoSourceType.choices, initial=VideoSourceType.UPLOAD)
    confidence_threshold = forms.TypedChoiceField(
        choices=SENSITIVITY_CHOICES,
        coerce=int,
        required=False,
        help_text="Higher sensitivity keeps lower-confidence candidates but can increase false detections.",
    )
    road_section = forms.CharField(max_length=180, required=False)
    chainage_station = forms.CharField(max_length=80, required=False)
    calibration_m_per_pixel = forms.DecimalField(min_value=0.000001, max_value=1, decimal_places=6, required=False)
    calibration_notes = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, default_confidence=50, **kwargs):
        super().__init__(*args, **kwargs)
        choices = list(self.SENSITIVITY_CHOICES)
        known_values = {value for value, _label in choices}
        if default_confidence not in known_values:
            choices.insert(0, (default_confidence, f"System default - {default_confidence}%"))
        self.fields["confidence_threshold"].choices = choices
        self.fields["confidence_threshold"].initial = default_confidence


class AnalyzerSettingsForm(forms.ModelForm):
    model_session = forms.ModelChoiceField(queryset=TrainingSession.objects.none(), required=True)
    training_model = forms.ChoiceField(
        choices=TrainingModelArchitecture.choices,
        help_text=(
            "Architecture used for the next automatic training session. "
            "A trained model appears in the active-model list only after held-out validation passes."
        ),
    )
    confidence_threshold = forms.IntegerField(min_value=1, max_value=99)
    iou_threshold = forms.IntegerField(min_value=1, max_value=99)
    input_resolution = forms.IntegerField(min_value=160, max_value=2048)
    frame_skip = forms.IntegerField(min_value=1, max_value=120)
    max_detections = forms.IntegerField(min_value=1, max_value=500)
    max_attempts = forms.IntegerField(min_value=1, max_value=10)
    min_track_appearances = forms.IntegerField(min_value=1, max_value=30)
    dedup_iou_threshold = forms.DecimalField(min_value=0.05, max_value=0.95, decimal_places=3)
    dedup_max_gap_frames = forms.IntegerField(min_value=1, max_value=1000)
    device = forms.ChoiceField(
        choices=[("auto", "Auto (CUDA when available)"), ("cpu", "CPU"), ("0", "CUDA GPU 0")]
    )
    tracker = forms.ChoiceField(
        choices=[
            ("bytetrack.yaml", "ByteTrack (recommended)"),
            ("botsort.yaml", "BoT-SORT"),
            ("iou", "Simple IoU fallback"),
        ]
    )

    class Meta:
        model = AnalyzerConfiguration
        fields = [
            "model_session", "training_model", "mode", "confidence_threshold", "iou_threshold", "device",
            "input_resolution", "frame_skip", "half_precision", "tracker", "max_detections",
            "max_attempts", "include_road_damage", "min_track_appearances", "dedup_iou_threshold",
            "dedup_max_gap_frames", "show_labels", "show_confidence", "show_tracking_ids", "show_boxes",
            "show_gps_overlay",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = TrainingSession.objects.filter(
            status=TrainingSession.Status.COMPLETE,
            is_validated=True,
        ).exclude(model_file="")
        queryset = queryset.filter(
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
            queryset = queryset.filter(local_gate)
        self.fields["model_session"].queryset = queryset.order_by("-is_active_video_model", "-created_at")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("device") == "cpu":
            cleaned["half_precision"] = False
        return cleaned


class GroundTruthCountForm(forms.Form):
    ground_truth_pothole_count = forms.IntegerField(min_value=0, max_value=100000)
    ground_truth_notes = forms.CharField(max_length=2000, required=False, widget=forms.Textarea)
