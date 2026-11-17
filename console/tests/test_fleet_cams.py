import hashlib
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from console.models import (
    AnalyzerConfiguration,
    AppRole,
    DeviceStatus,
    FleetDevice,
    PotholeReport,
    TrainingSession,
    UserRole,
    VideoSourceType,
    VideoVisualizerAnalysis,
    VideoVisualizerMode,
    VideoVisualizerStatus,
)
from console.views import read_uploaded_video


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
        self.report = PotholeReport.objects.create(
            lat=14.6, lng=121, city="Cavite", device_id=self.device.pk,
            severity="low", detected_at=timezone.now(),
        )

    def test_fleet_page_exposes_stream_and_live_capture_controls(self):
        response = self.client.get("/api/fleet/")

        self.assertEqual(response.status_code, 200)
        device = response.json()["devices"][0]
        self.assertEqual(device["name"], "Cavite Survey Cam 01")
        self.assertTrue(device["hasStream"])
        self.assertEqual(device["streamUrl"], self.device.stream_url)
        self.assertEqual(device["cameraSource"], "stream")

    def test_camera_source_choices_are_saved_and_returned_by_the_api(self):
        for source in FleetDevice.CameraSource.values:
            with self.subTest(source=source):
                response = self.client.post("/api/actions/modules/fleet-cams/", {
                    "action": "save_fleet_device", "device_id": f"camera-{source}",
                    "name": f"Survey {source}", "city": "Cavite", "status": "online",
                    "fps": "30", "camera_source": source,
                    "stream_url": "https://camera.example.com/feed" if source == "stream" else "",
                })
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(FleetDevice.objects.get(pk=f"camera-{source}").camera_source, source)
                devices = {device["id"]: device for device in self.client.get("/api/fleet/").json()["devices"]}
                self.assertEqual(devices[f"camera-{source}"]["cameraSource"], source)

    def test_invalid_camera_source_cannot_register_a_device(self):
        response = self.client.post("/api/actions/modules/fleet-cams/", {
            "action": "save_fleet_device", "device_id": "bad-camera", "name": "Bad camera",
            "city": "Cavite", "status": "online", "fps": "30", "camera_source": "invalid",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("camera_source", response.json()["fields"])
        self.assertFalse(FleetDevice.objects.filter(pk="bad-camera").exists())

    @patch("console.views.read_uploaded_video")
    def test_laptop_mobile_and_dashcam_clips_keep_their_source(self, metadata_reader):
        metadata_reader.return_value = {
            "file_hash": "a" * 64, "file_size": 15, "file_type": "webm",
            "width": 1280, "height": 720, "fps": 30, "frame_count": 60,
            "duration_seconds": 2, "extension": ".webm",
        }
        for source in ["laptop", "mobile", "dashcam"]:
            with self.subTest(source=source):
                response = self.client.post("/api/actions/modules/fleet-cams/", {
                    "action": "analyze_fleet_capture", "device_id": self.device.pk,
                    "camera_source": source,
                    "video": SimpleUploadedFile(f"{source}.webm", b"recorded-video", content_type="video/webm"),
                })
                self.assertEqual(response.status_code, 200, response.content)
                analysis = VideoVisualizerAnalysis.objects.get(original_filename=f"{source}.webm")
                self.assertEqual(analysis.source_type, "dashcam" if source == "dashcam" else "webcam")
                self.assertEqual(analysis.route_metadata["camera_source"], source)
                self.assertEqual(analysis.route_metadata["fleet_device_id"], self.device.pk)
                self.assertEqual(response.json()["redirect"], f"/app/analyzer?analysis={analysis.pk}")
                self.assertTrue(metadata_reader.call_args.kwargs["recorded_capture"])

    @patch("console.views.read_uploaded_video")
    def test_analyzer_accepts_browser_camera_uploads(self, metadata_reader):
        metadata_reader.return_value = {
            "file_hash": "c" * 64, "file_size": 15, "file_type": "webm",
            "width": 1280, "height": 720, "fps": 30, "frame_count": 60,
            "duration_seconds": 2, "extension": ".webm",
        }
        for source in ["laptop", "mobile", "dashcam"]:
            with self.subTest(source=source):
                response = self.client.post("/api/actions/analyzer/", {
                    "action": "upload_video", "source_type": "dashcam" if source == "dashcam" else "webcam",
                    "report_id": self.report.pk,
                    "camera_source": source, "road_section": "Road 1",
                    "video": SimpleUploadedFile(f"analyzer-{source}.webm", b"recorded-video", content_type="video/webm"),
                })
                self.assertEqual(response.status_code, 200, response.content)
                analysis = VideoVisualizerAnalysis.objects.get(original_filename=f"analyzer-{source}.webm")
                self.assertEqual(analysis.route_metadata["camera_source"], source)
                self.assertTrue(metadata_reader.call_args.kwargs["recorded_capture"])

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

    @patch("console.views.read_video_stream_metadata")
    def test_stream_api_returns_the_selected_analyzer_redirect(self, metadata_reader):
        metadata_reader.return_value = {"file_hash": "f" * 64, "file_size": 0, "file_type": "stream", "width": 320,
                                        "height": 240, "fps": 25, "frame_count": 0, "duration_seconds": 0}
        response = self.client.post("/api/actions/modules/fleet-cams/", {"action": "analyze_fleet_stream", "device_id": self.device.pk})
        self.assertEqual(response.status_code, 200, response.content)
        analysis = VideoVisualizerAnalysis.objects.get()
        self.assertEqual(response.json()["redirect"], f"/app/analyzer?analysis={analysis.pk}")

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

        response = self.client.get(f"/api/analyzer/?analysis={analysis.pk}")

        self.assertEqual(response.status_code, 200)
        selected = response.json()["selected"]
        self.assertTrue(selected["isContinuous"])
        self.assertEqual(selected["status"], VideoVisualizerStatus.RUNNING)
        self.assertEqual(selected["sourceType"], VideoSourceType.LIVE_STREAM)

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


class CameraRecordingMetadataTests(SimpleTestCase):
    def fake_cv2(self, count):
        capture = Mock()
        capture.get.side_effect = lambda prop: {1: 640, 2: 480, 3: 30, 4: count}[prop]
        capture.read.side_effect = [(True, None)] * 60 + [(False, None)]
        return SimpleNamespace(
            VideoCapture=Mock(return_value=capture), CAP_PROP_FRAME_WIDTH=1,
            CAP_PROP_FRAME_HEIGHT=2, CAP_PROP_FPS=3, CAP_PROP_FRAME_COUNT=4,
        ), capture

    def test_browser_webm_without_duration_counts_decodable_frames(self):
        for count in [0, -1, float("nan"), float("inf")]:
            with self.subTest(count=count):
                cv2, capture = self.fake_cv2(count)
                with patch.dict("sys.modules", {"cv2": cv2}):
                    metadata = read_uploaded_video(
                        SimpleUploadedFile("camera.webm", b"recorded-video"), recorded_capture=True,
                    )
                self.assertEqual(metadata["frame_count"], 60)
                self.assertEqual(metadata["duration_seconds"], 2)
                capture.release.assert_called_once()

    def test_duration_fallback_is_bounded_and_releases_the_capture(self):
        cv2, capture = self.fake_cv2(0)
        capture.read.side_effect = None
        capture.read.return_value = (True, None)
        with patch.dict("sys.modules", {"cv2": cv2}):
            with self.assertRaisesRegex(ValueError, "at most 45 seconds"):
                read_uploaded_video(SimpleUploadedFile("camera.webm", b"recorded-video"), recorded_capture=True)
        self.assertEqual(capture.read.call_count, 30 * 46 + 1)
        capture.release.assert_called_once()

    def test_regular_uploads_with_invalid_metadata_are_not_scanned(self):
        cv2, capture = self.fake_cv2(0)
        with patch.dict("sys.modules", {"cv2": cv2}):
            with self.assertRaisesRegex(ValueError, "at least one readable frame"):
                read_uploaded_video(SimpleUploadedFile("camera.webm", b"recorded-video"))
        capture.read.assert_not_called()
        capture.release.assert_called_once()
