import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from console.models import (
    AnalyzerConfiguration,
    AppRole,
    DeviceStatus,
    FleetDevice,
    TrainingSession,
    UserRole,
    VideoSourceType,
    VideoVisualizerAnalysis,
    VideoVisualizerMode,
    VideoVisualizerStatus,
)


@override_settings(AUTO_START_ANALYSIS_WORKER=False)
class FleetCameraAnalyzerTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)

        self.admin = get_user_model().objects.create_user(
            username="fleet-admin@example.com",
            email="fleet-admin@example.com",
            password="Fleet-admin-2026!",
        )
        UserRole.objects.update_or_create(user=self.admin, defaults={"role": AppRole.ADMIN})
        self.client.force_login(self.admin)

        model_path = Path(self.media_directory.name) / "fleet-pothole.pt"
        model_path.write_bytes(b"validated-model")
        self.model = TrainingSession.objects.create(
            model_name="fleet-pothole",
            status=TrainingSession.Status.COMPLETE,
            is_validated=True,
            is_active_video_model=True,
            model_file=str(model_path),
            model_sha256=hashlib.sha256(b"validated-model").hexdigest(),
            map50=0.8,
            model_task="segment",
            local_evaluation_at=timezone.now(),
            local_test_images=10,
            local_map50=0.8,
        )
        AnalyzerConfiguration.objects.update_or_create(
            pk=1,
            defaults={
                "model_session": self.model,
                "confidence_threshold": 50,
                "iou_threshold": 45,
            },
        )
        self.device = FleetDevice.objects.create(
            id="fleet-cam-01",
            name="Cavite Survey Cam 01",
            city="Cavite",
            status=DeviceStatus.ONLINE,
            last_seen_at=timezone.now(),
            fps=30,
            model_version="vehicle-camera",
            stream_url="https://camera.example.com/live",
            road_section="Aguinaldo Highway northbound",
            chainage_station="CH 12+500",
        )

    def test_fleet_page_exposes_stream_and_live_capture_controls(self):
        response = self.client.get("/_authenticated/admin/fleet-cams/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Register or update a fleet camera")
        self.assertContains(response, "Select Cavite Survey Cam 01 stream")
        self.assertContains(response, "data-fleet-stream-select")
        self.assertContains(response, "Phone / device live camera")
        self.assertContains(response, "Start phone camera live")
        self.assertContains(response, "fleet-live-indicator")
        self.assertContains(response, "Available camera")
        self.assertContains(response, 'class="fleet-camera-source"')
        self.assertContains(response, 'class="fleet-camera-flip"')
        self.assertContains(response, "Send captured clip")
        self.assertContains(response, 'data-fleet-capture', html=False)

    def test_admin_can_register_a_fleet_camera(self):
        response = self.client.post(
            "/_authenticated/admin/fleet-cams/",
            {
                "action": "save_fleet_device",
                "device_id": "fleet-cam-02",
                "name": "Laguna Survey Cam 02",
                "city": "Laguna",
                "status": DeviceStatus.ONLINE,
                "stream_url": "rtsp://camera.example.com/road",
                "road_section": "Manila South Road",
                "chainage_station": "CH 3+250",
                "fps": "25",
                "model_version": "roof-rig-v2",
            },
        )

        self.assertEqual(response.status_code, 302)
        device = FleetDevice.objects.get(pk="fleet-cam-02")
        self.assertEqual(device.stream_url, "rtsp://camera.example.com/road")
        self.assertEqual(device.road_section, "Manila South Road")
        self.assertEqual(device.chainage_station, "CH 3+250")

    @patch("console.views.read_video_stream_metadata")
    def test_configured_stream_is_queued_in_video_analyzer(self, metadata_reader):
        metadata_reader.return_value = {
            "file_hash": "b" * 64,
            "file_size": 0,
            "file_type": "stream",
            "width": 1280,
            "height": 720,
            "fps": 30,
            "frame_count": 0,
            "duration_seconds": 0,
        }

        response = self.client.post(
            "/_authenticated/admin/fleet-cams/",
            {
                "action": "analyze_fleet_stream",
                "device_id": self.device.pk,
                "confidence_threshold": "35",
            },
        )

        self.assertEqual(response.status_code, 302)
        analysis = VideoVisualizerAnalysis.objects.get()
        self.assertIn(f"analysis={analysis.pk}", response.url)
        self.assertEqual(analysis.status, VideoVisualizerStatus.QUEUED)
        self.assertEqual(analysis.source_type, VideoSourceType.LIVE_STREAM)
        self.assertTrue(analysis.is_continuous)
        self.assertEqual(analysis.mode, VideoVisualizerMode.REAL_TIME)
        self.assertEqual(analysis.source_url, self.device.stream_url)
        self.assertEqual(analysis.original_filename, "Cavite Survey Cam 01 live stream")
        self.assertEqual(analysis.confidence_threshold, 35)
        self.assertEqual(analysis.road_section, self.device.road_section)
        self.assertEqual(analysis.route_metadata["fleet_device_id"], self.device.pk)
        self.assertEqual(analysis.route_metadata["capture_type"], "live-stream")

    def test_running_continuous_stream_can_be_stopped_gracefully(self):
        analysis = VideoVisualizerAnalysis.objects.create(
            source_url=self.device.stream_url,
            original_filename=f"{self.device.name} live stream",
            file_hash="d" * 64,
            file_type="stream",
            source_type=VideoSourceType.LIVE_STREAM,
            mode=VideoVisualizerMode.REAL_TIME,
            is_continuous=True,
            status=VideoVisualizerStatus.RUNNING,
            worker_id="test-worker",
            model_session=self.model,
            route_metadata={"fleet_device_id": self.device.pk, "capture_type": "live-stream"},
            created_by=self.admin,
        )

        response = self.client.post(
            f"/_authenticated/admin/video-analyzer/?analysis={analysis.pk}",
            {"action": "stop_continuous_analysis", "analysis_id": analysis.pk},
        )

        self.assertEqual(response.status_code, 302)
        analysis.refresh_from_db()
        self.assertEqual(analysis.status, VideoVisualizerStatus.RUNNING)
        self.assertTrue(analysis.stop_requested)
        self.assertEqual(analysis.worker_id, "test-worker")

    def test_live_status_endpoint_returns_continuous_metrics(self):
        analysis = VideoVisualizerAnalysis.objects.create(
            source_url=self.device.stream_url,
            original_filename=f"{self.device.name} live stream",
            file_hash="e" * 64,
            file_type="stream",
            source_type=VideoSourceType.LIVE_STREAM,
            mode=VideoVisualizerMode.REAL_TIME,
            is_continuous=True,
            status=VideoVisualizerStatus.RUNNING,
            current_frame=120,
            frames_processed=40,
            total_unique_potholes=3,
            total_detections=8,
            model_session=self.model,
            created_by=self.admin,
        )

        response = self.client.get(f"/_authenticated/admin/video-analyzer/{analysis.pk}/status/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["is_continuous"])
        self.assertEqual(payload["current_frame"], 120)
        self.assertEqual(payload["total_unique_potholes"], 3)
        self.assertEqual(payload["total_detections"], 8)

    def test_analyzer_renders_continuous_live_controls(self):
        analysis = VideoVisualizerAnalysis.objects.create(
            source_url=self.device.stream_url,
            original_filename=f"{self.device.name} live stream",
            file_hash="f" * 64,
            file_type="stream",
            source_type=VideoSourceType.LIVE_STREAM,
            mode=VideoVisualizerMode.REAL_TIME,
            is_continuous=True,
            status=VideoVisualizerStatus.RUNNING,
            model_session=self.model,
            created_by=self.admin,
        )

        response = self.client.get(f"/_authenticated/admin/video-analyzer/?analysis={analysis.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stop live detection")
        self.assertContains(response, 'data-continuous="true"')
        self.assertContains(response, "LIVE DETECTION")
        self.assertContains(response, f"/_authenticated/admin/video-analyzer/{analysis.pk}/status/")

    @patch("console.views.read_uploaded_video")
    def test_live_captured_clip_is_queued_in_video_analyzer(self, metadata_reader):
        capture = SimpleUploadedFile(
            "cavite-road-capture.webm",
            b"browser-recorded-road-video",
            content_type="video/webm",
        )
        metadata_reader.return_value = {
            "file_hash": "c" * 64,
            "file_size": capture.size,
            "file_type": "webm",
            "width": 1280,
            "height": 720,
            "fps": 30,
            "frame_count": 150,
            "duration_seconds": 5,
            "extension": ".webm",
        }

        response = self.client.post(
            "/_authenticated/admin/fleet-cams/",
            {
                "action": "analyze_fleet_capture",
                "device_id": self.device.pk,
                "confidence_threshold": "25",
                "video": capture,
            },
        )

        self.assertEqual(response.status_code, 302)
        analysis = VideoVisualizerAnalysis.objects.get()
        self.assertIn(f"analysis={analysis.pk}", response.url)
        self.assertEqual(analysis.source_type, VideoSourceType.DASHCAM)
        self.assertEqual(analysis.original_filename, "cavite-road-capture.webm")
        self.assertEqual(analysis.confidence_threshold, 25)
        self.assertTrue(bool(analysis.video))
        self.assertEqual(analysis.route_metadata["fleet_device_name"], self.device.name)
        self.assertEqual(analysis.route_metadata["capture_type"], "captured-clip")

    @patch("console.views.read_video_stream_metadata")
    def test_offline_camera_cannot_queue_a_stream(self, metadata_reader):
        self.device.status = DeviceStatus.OFFLINE
        self.device.save(update_fields=["status"])

        response = self.client.post(
            "/_authenticated/admin/fleet-cams/",
            {
                "action": "analyze_fleet_stream",
                "device_id": self.device.pk,
                "confidence_threshold": "50",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(VideoVisualizerAnalysis.objects.exists())
        metadata_reader.assert_not_called()
