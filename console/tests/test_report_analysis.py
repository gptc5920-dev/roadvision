import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from console.models import (
    AnalyzerConfiguration, AppRole, PotholeReport, TrainingSession, UserRole,
    VideoPotholeTrack, VideoVisualizerAnalysis,
)


@override_settings(AUTO_START_ANALYSIS_WORKER=False)
class ReportAnalysisTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="report-engineer", password="test-password")
        UserRole.objects.filter(user=self.user).update(role=AppRole.ENGINEER)
        self.client.force_login(self.user)
        self.first = self.make_report("Manila", "CAM-01")
        self.second = self.make_report("Cavite", "CAM-02")
        self.first_run = self.make_analysis(self.first, "manila.mp4")
        self.second_run = self.make_analysis(self.second, "cavite.mp4")
        self.legacy = self.make_analysis(None, "unassigned.mp4")
        self.first_track = VideoPotholeTrack.objects.create(analysis=self.first_run, track_id=1, latitude=14.6, longitude=121)
        VideoPotholeTrack.objects.create(analysis=self.second_run, track_id=1, latitude=14.7, longitude=121.1)

    def make_report(self, city, device):
        return PotholeReport.objects.create(
            lat=14.6, lng=121, city=city, device_id=device, severity="high", detected_at=timezone.now(),
        )

    def make_analysis(self, report, name):
        return VideoVisualizerAnalysis.objects.create(
            report=report, original_filename=name, file_hash="a" * 64, file_type="mp4", status="complete",
        )

    def test_report_queue_contains_only_its_own_analyses_and_tracks(self):
        response = self.client.get(f"/api/analyzer/?report={self.first.pk}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["report"]["id"], self.first.pk)
        self.assertEqual([item["id"] for item in data["analyses"]], [self.first_run.pk])
        self.assertEqual(data["selected"]["reportId"], self.first.pk)
        self.assertEqual([item["id"] for item in data["selected"]["tracks"]], [self.first_track.pk])
        self.assertEqual(data["unassignedAnalyses"], [])

    def test_report_scope_rejects_an_analysis_from_another_report(self):
        response = self.client.get(f"/api/analyzer/?report={self.first.pk}&analysis={self.second_run.pk}")
        self.assertEqual(response.status_code, 404)
        for report, expected in [("invalid", 400), (0, 400), (self.second.pk + 100, 404)]:
            with self.subTest(report=report):
                self.assertEqual(self.client.get(f"/api/analyzer/?report={report}").status_code, expected)

    def test_empty_report_never_falls_back_to_another_reports_analysis(self):
        report = self.make_report("Laguna", "CAM-03")
        data = self.client.get(f"/api/analyzer/?report={report.pk}").json()
        self.assertIsNone(data["selected"])
        self.assertEqual(data["analyses"], [])
        self.assertEqual(data["unassignedAnalyses"], [])

    def test_direct_analysis_link_infers_the_report_context(self):
        data = self.client.get(f"/api/analyzer/?analysis={self.first_run.pk}").json()
        self.assertEqual(data["report"]["id"], self.first.pk)
        self.assertEqual([item["id"] for item in data["analyses"]], [self.first_run.pk])

    def test_index_lists_reports_without_selecting_an_unrelated_run(self):
        data = self.client.get("/api/analyzer/").json()
        self.assertIsNone(data["report"])
        self.assertIsNone(data["selected"])
        self.assertEqual(data["analyses"], [])
        self.assertEqual({item["id"] for item in data["reports"]}, {self.first.pk, self.second.pk})
        self.assertEqual([item["id"] for item in data["unassignedAnalyses"]], [self.legacy.pk])
        legacy = self.client.get(f"/api/analyzer/?analysis={self.legacy.pk}").json()
        self.assertIsNone(legacy["report"])
        self.assertEqual([item["id"] for item in legacy["analyses"]], [self.legacy.pk])

    def test_cross_report_write_cannot_remove_or_cancel_another_run(self):
        for action in ["remove_analysis", "cancel_analysis", "retry_analysis"]:
            with self.subTest(action=action):
                response = self.client.post("/api/actions/analyzer/", {
                    "action": action, "report_id": self.first.pk, "analysis_id": self.second_run.pk,
                })
                self.assertEqual(response.status_code, 404)
                self.second_run.refresh_from_db()
                self.assertEqual(self.second_run.status, "complete")
        response = self.client.post("/api/actions/analyzer/", {
            "action": "remove_analysis", "report_id": self.first.pk, "analysis_id": self.first_run.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VideoVisualizerAnalysis.objects.filter(pk=self.first_run.pk).exists())
        self.assertTrue(VideoVisualizerAnalysis.objects.filter(pk=self.second_run.pk).exists())

    def test_report_map_filter_excludes_other_report_detections(self):
        data = self.client.get(f"/api/reports/?report={self.first.pk}").json()
        self.assertEqual([item["id"] for item in data["detections"]], [self.first_track.pk])

    def test_create_report_validates_location_and_opens_its_analyzer(self):
        fields = {"action": "create_report", "city": "Laguna", "device_id": "PHONE-01", "lat": 14.4, "lng": 121.2, "severity": "high", "notes": "Road survey"}
        invalid = self.client.post("/api/actions/analyzer/", fields | {"lat": 91})
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("lat", invalid.json()["fields"])
        response = self.client.post("/api/actions/analyzer/", fields)
        self.assertEqual(response.status_code, 200)
        report = PotholeReport.objects.get(device_id="PHONE-01")
        self.assertEqual(response.json()["redirect"], f"/app/analyzer?report={report.pk}")
        self.assertEqual(report.status, "open")

    @patch("console.views.model_readiness", return_value={"ready": True, "errors": []})
    @patch("console.views.read_uploaded_video")
    def test_uploads_and_camera_clips_are_attached_to_the_selected_report(self, reader, readiness):
        model = TrainingSession.objects.create(model_name="test", status="complete")
        AnalyzerConfiguration.objects.update_or_create(pk=1, defaults={"model_session": model})
        reader.return_value = {"file_hash": "b" * 64, "file_size": 4, "file_type": "webm", "extension": ".webm", "width": 640, "height": 480, "fps": 30, "frame_count": 30, "duration_seconds": 1}
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            for source in ["upload", "laptop", "mobile", "dashcam"]:
                with self.subTest(source=source):
                    response = self.client.post("/api/actions/analyzer/", {
                        "action": "upload_video", "report_id": self.first.pk,
                        "source_type": "upload" if source == "upload" else "dashcam" if source == "dashcam" else "webcam",
                        "camera_source": "" if source == "upload" else source,
                        "video": SimpleUploadedFile(f"{source}.webm", b"clip", content_type="video/webm"),
                    })
                    self.assertEqual(response.status_code, 200, response.content)
                    analysis = VideoVisualizerAnalysis.objects.get(original_filename=f"{source}.webm")
                    self.assertEqual(analysis.report_id, self.first.pk)
                    self.assertIn(f"report={self.first.pk}", response.json()["redirect"])
        missing = self.client.post("/api/actions/analyzer/", {"action": "upload_video", "source_type": "upload"})
        self.assertEqual(missing.status_code, 400)
        self.assertIn("report_id", missing.json()["fields"])

    @patch("console.views.model_readiness", return_value={"ready": True, "errors": []})
    @patch("console.views.read_video_stream_metadata")
    def test_stream_source_is_report_specific_and_remains_continuous(self, reader, readiness):
        model = TrainingSession.objects.create(model_name="test", status="complete")
        AnalyzerConfiguration.objects.update_or_create(pk=1, defaults={"model_session": model})
        reader.return_value = {"file_hash": "c" * 64, "file_size": 0, "file_type": "stream", "width": 640, "height": 480, "fps": 30, "frame_count": 0, "duration_seconds": 0}
        response = self.client.post("/api/actions/analyzer/", {
            "action": "upload_video", "report_id": self.second.pk, "source_type": "live-stream",
            "camera_source": "stream", "stream_url": "https://camera.example.com/feed",
        })
        self.assertEqual(response.status_code, 200, response.content)
        analysis = VideoVisualizerAnalysis.objects.get(source_url="https://camera.example.com/feed")
        self.assertEqual(analysis.report_id, self.second.pk)
        self.assertTrue(analysis.is_continuous)

    def test_csv_identifies_the_report(self):
        response = self.client.get(f"/_authenticated/admin/video-visualizer/{self.first_run.pk}/report/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"Report ID,{self.first.pk}", response.content.decode())
