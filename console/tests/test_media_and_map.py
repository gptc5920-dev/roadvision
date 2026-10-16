import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from console.models import AppRole, PotholeReport, UserRole, VideoPotholeTrack, VideoVisualizerAnalysis, VideoVisualizerStatus


@override_settings(DEBUG=False, AUTO_START_ANALYSIS_WORKER=False)
class MediaAndMapTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="media-reviewer")
        UserRole.objects.update_or_create(user=self.user, defaults={"role": AppRole.ADMIN})
        self.client.force_login(self.user)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = override_settings(MEDIA_ROOT=self.directory.name)
        self.storage.enable()
        self.addCleanup(self.storage.disable)

    def test_local_images_are_served_without_debug_and_require_console_access(self):
        name = default_storage.save("training_dataset/originals/road.jpg", ContentFile(b"stored-image"))
        response = self.client.get(f"/media/{name}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"stored-image")
        self.assertTrue(response["Cache-Control"].startswith("private"))
        self.client.logout()
        self.assertEqual(self.client.get(f"/media/{name}").status_code, 302)

    def test_storage_route_does_not_expose_model_files_or_traversal(self):
        default_storage.save("training_dataset/best.pt", ContentFile(b"model"))
        self.assertEqual(self.client.get("/media/training_dataset/best.pt").status_code, 404)
        self.assertEqual(self.client.get("/media/../outside.jpg").status_code, 404)
        self.assertEqual(self.client.get("/media/missing.jpg").status_code, 404)

    def test_track_snapshots_use_authenticated_api_urls_and_storage_reads(self):
        analysis = VideoVisualizerAnalysis.objects.create(status=VideoVisualizerStatus.COMPLETE)
        track = VideoPotholeTrack.objects.create(analysis=analysis, track_id=1)
        track.snapshot_frame.save("frame.jpg", ContentFile(b"frame-image"))
        track.snapshot_crop.save("crop.jpg", ContentFile(b"crop-image"))
        # An external/private storage URL must never be needed by the browser.
        with patch.object(type(default_storage), "url", create=True, side_effect=AssertionError("raw storage URL requested")):
            payload = self.client.get(f"/api/analyzer/?analysis={analysis.pk}").json()
            serialized = payload["selected"]["tracks"][0]
            for kind, content in [("frame", b"frame-image"), ("crop", b"crop-image")]:
                url = serialized[f"snapshot{kind.title()}Url"]
                self.assertEqual(url, f"/api/detections/{track.pk}/snapshot/{kind}/")
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "image/jpeg")
                self.assertTrue(response["Cache-Control"].startswith("private"))
                self.assertEqual(b"".join(response.streaming_content), content)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        viewer = get_user_model().objects.create_user(username="snapshot-viewer")
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_track_snapshot_handles_missing_files_and_invalid_requests(self):
        analysis = VideoVisualizerAnalysis.objects.create(status=VideoVisualizerStatus.COMPLETE)
        track = VideoPotholeTrack.objects.create(
            analysis=analysis, track_id=1, snapshot_frame="missing.jpg",
        )
        for kind in ["frame", "crop", "original"]:
            self.assertEqual(self.client.get(f"/api/detections/{track.pk}/snapshot/{kind}/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/detections/{track.pk + 1}/snapshot/frame/").status_code, 404)

    def test_live_preview_is_an_image_and_is_not_cached(self):
        analysis = VideoVisualizerAnalysis.objects.create(status=VideoVisualizerStatus.RUNNING, heartbeat_at=timezone.now())
        analysis.live_preview_frame.save("preview.jpg", ContentFile(b"live-jpeg"))
        payload = self.client.get(f"/api/analyzer/?analysis={analysis.pk}").json()
        url = payload["selected"]["media"]["live"]
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(b"".join(response.streaming_content), b"live-jpeg")
        analysis.heartbeat_at = timezone.now()
        analysis.save(update_fields=["heartbeat_at"])
        self.assertNotEqual(self.client.get(f"/api/analyzer/?analysis={analysis.pk}").json()["selected"]["media"]["live"], url)

    def test_map_contains_only_located_non_rejected_detections_and_report_links_filter(self):
        analysis = VideoVisualizerAnalysis.objects.create(status=VideoVisualizerStatus.COMPLETE, road_section="Route 17")
        located = VideoPotholeTrack.objects.create(analysis=analysis, track_id=1, latitude=14.6, longitude=121.0)
        VideoPotholeTrack.objects.create(analysis=analysis, track_id=2)
        VideoPotholeTrack.objects.create(analysis=analysis, track_id=3, latitude=14.6, longitude=121.0, review_status="rejected")
        report = PotholeReport.objects.create(lat=14.6, lng=121.0, city="Manila", device_id="CAM-01", severity="high", detected_at=timezone.now())
        PotholeReport.objects.create(lat=14.7, lng=121.1, city="Manila", device_id="CAM-02", severity="low", detected_at=timezone.now())
        result = self.client.get(f"/api/reports/?report={report.pk}").json()
        self.assertEqual([row["id"] for row in result["reports"]], [report.pk])
        self.assertEqual([row["id"] for row in result["detections"]], [located.pk])
        self.assertEqual(result["detections"][0]["analysisId"], analysis.pk)
        review = self.client.get(f"/api/detections/?analysis={analysis.pk}&track={located.pk}").json()
        self.assertEqual([row["id"] for row in review["tracks"]], [located.pk])
        self.assertEqual(self.client.get("/api/reports/?report=invalid").status_code, 400)

    def test_storage_probe_reads_back_data_and_removes_only_its_object(self):
        default_storage.save("keep.jpg", ContentFile(b"keep"))
        output = io.StringIO()
        call_command("check_media_storage", probe=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue())["probe"], "passed")
        self.assertEqual([path.name for path in Path(self.directory.name).rglob("*") if path.is_file()], ["keep.jpg"])
