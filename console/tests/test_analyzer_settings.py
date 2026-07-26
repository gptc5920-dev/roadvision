import hashlib
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from console.forms import VideoVisualizerUploadForm
from console.models import (
    AnalyzerConfiguration,
    DatasetImage,
    TrainingSession,
    VideoPotholeTrack,
    VideoTrackReviewStatus,
    VideoVisualizerAnalysis,
    VideoVisualizerStatus,
)
from console.views import save_visualizer_video


class AnalyzerSettingsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="admin@example.com", email="admin@example.com", password="password"
        )
        self.client.force_login(self.user)
        self.model = TrainingSession.objects.create(
            model_name="pothole-test",
            status=TrainingSession.Status.COMPLETE,
            is_validated=True,
            is_active_video_model=True,
            model_file="models/pothole.pt",
            model_sha256="a" * 64,
            map50=0.8,
            model_task="segment",
            local_evaluation_at=timezone.now(),
            local_test_images=10,
            local_map50=0.8,
        )
        self.configuration, _ = AnalyzerConfiguration.objects.update_or_create(
            pk=1,
            defaults={
                "model_session": self.model,
                "confidence_threshold": 72,
                "iou_threshold": 41,
                "tracker": "botsort.yaml",
                "max_attempts": 4,
            },
        )

    def test_settings_are_not_rendered_on_analyzer_upload(self):
        analyzer = self.client.get("/_authenticated/admin/video-analyzer/")
        settings_page = self.client.get("/_authenticated/admin/settings/")
        self.assertContains(analyzer, "Upload and Analyze")
        self.assertContains(analyzer, 'id="open-upload-modal"')
        self.assertContains(analyzer, 'id="upload-analysis-modal"')
        self.assertContains(analyzer, 'id="visualizer-upload-form"')
        self.assertContains(analyzer, 'id="processing-modal"')
        self.assertContains(analyzer, 'id="analysis-ready-notification"')
        self.assertNotContains(analyzer, "Model and Analysis Settings")
        self.assertNotContains(analyzer, "Confidence threshold")
        self.assertContains(settings_page, "Model and Analysis Settings")
        self.assertContains(settings_page, "Confidence threshold")
        self.assertContains(settings_page, "Default training architecture")
        self.assertContains(settings_page, "YOLO26s segmentation")

    def test_new_analysis_copies_saved_configuration(self):
        form = VideoVisualizerUploadForm(
            {"source_type": "upload", "road_section": "Test Road", "chainage_station": "CH 1+000"}
        )
        self.assertTrue(form.is_valid(), form.errors)
        upload = SimpleUploadedFile("road.mp4", b"video-placeholder", content_type="video/mp4")
        metadata = {
            "file_hash": "b" * 64,
            "file_size": upload.size,
            "file_type": "mp4",
            "width": 640,
            "height": 480,
            "fps": 30,
            "frame_count": 30,
            "duration_seconds": 1,
            "extension": ".mp4",
        }
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            analysis = save_visualizer_video(upload, upload.name, metadata, form, self.configuration, self.user)
        self.assertEqual(analysis.model_session, self.model)
        self.assertEqual(analysis.confidence_threshold, 72)
        self.assertEqual(analysis.iou_threshold, 41)
        self.assertEqual(analysis.tracker, "botsort.yaml")
        self.assertEqual(analysis.max_attempts, 4)

    def test_upload_can_use_higher_per_video_sensitivity(self):
        form = VideoVisualizerUploadForm(
            {
                "source_type": "upload",
                "road_section": "Test Road",
                "confidence_threshold": 25,
            },
            default_confidence=self.configuration.confidence_threshold,
        )
        self.assertTrue(form.is_valid(), form.errors)
        upload = SimpleUploadedFile("sensitive-road.mp4", b"video-placeholder", content_type="video/mp4")
        metadata = {
            "file_hash": "9" * 64,
            "file_size": upload.size,
            "file_type": "mp4",
            "width": 640,
            "height": 480,
            "fps": 30,
            "frame_count": 30,
            "duration_seconds": 1,
            "extension": ".mp4",
        }
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            analysis = save_visualizer_video(upload, upload.name, metadata, form, self.configuration, self.user)
        self.assertEqual(analysis.confidence_threshold, 25)

    @override_settings(AUTO_START_ANALYSIS_WORKER=False)
    def test_zero_detection_analysis_can_be_requeued_with_higher_sensitivity(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            model_path = Path(media_root) / "model.pt"
            model_path.write_bytes(b"model")
            self.model.model_file = str(model_path)
            self.model.model_sha256 = hashlib.sha256(b"model").hexdigest()
            self.model.save(update_fields=["model_file", "model_sha256"])
            analysis = VideoVisualizerAnalysis.objects.create(
                video=SimpleUploadedFile("zero-road.mp4", b"raw-video", content_type="video/mp4"),
                processed_video=SimpleUploadedFile("zero-processed.mp4", b"processed", content_type="video/mp4"),
                original_filename="zero-road.mp4",
                file_hash="8" * 64,
                file_type="mp4",
                status=VideoVisualizerStatus.COMPLETE,
                total_detections=0,
                total_unique_potholes=0,
                confidence_threshold=50,
                frames_processed=120,
                model_session=self.model,
                created_by=self.user,
            )
            response = self.client.post(
                "/_authenticated/admin/video-analyzer/",
                {
                    "action": "reanalyze_with_sensitivity",
                    "analysis_id": analysis.pk,
                    "confidence_threshold": 25,
                },
            )
            self.assertEqual(response.status_code, 302)
            analysis.refresh_from_db()
            self.assertEqual(analysis.status, VideoVisualizerStatus.QUEUED)
            self.assertEqual(analysis.confidence_threshold, 25)
            self.assertEqual(analysis.frames_processed, 0)
            self.assertFalse(bool(analysis.processed_video))

    @override_settings(AUTO_START_ANALYSIS_WORKER=False)
    def test_completed_analysis_can_restart_with_current_settings(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            model_path = Path(media_root) / "restart-model.pt"
            model_path.write_bytes(b"restart-model")
            self.model.model_file = str(model_path)
            self.model.model_sha256 = hashlib.sha256(b"restart-model").hexdigest()
            self.model.save(update_fields=["model_file", "model_sha256"])
            self.configuration.confidence_threshold = 38
            self.configuration.iou_threshold = 52
            self.configuration.input_resolution = 768
            self.configuration.save(update_fields=["confidence_threshold", "iou_threshold", "input_resolution"])
            analysis = VideoVisualizerAnalysis.objects.create(
                video=SimpleUploadedFile("restart-road.mp4", b"original-video", content_type="video/mp4"),
                processed_video=SimpleUploadedFile("restart-processed.mp4", b"processed-video", content_type="video/mp4"),
                original_filename="restart-road.mp4",
                file_hash="a" * 64,
                file_type="mp4",
                status=VideoVisualizerStatus.COMPLETE,
                confidence_threshold=80,
                iou_threshold=20,
                input_resolution=320,
                total_detections=14,
                total_unique_potholes=3,
                frames_processed=240,
                model_session=self.model,
                created_by=self.user,
            )
            VideoPotholeTrack.objects.create(analysis=analysis, track_id=1, label="Pothole")
            source_name = analysis.video.name

            page = self.client.get(f"/_authenticated/admin/video-analyzer/?analysis={analysis.pk}")
            self.assertContains(page, "Restart pothole analysis")
            self.assertContains(page, 'class="inline-form restart-analysis-form"')

            with patch("console.views.ensure_analysis_worker") as starter:
                response = self.client.post(
                    "/_authenticated/admin/video-analyzer/",
                    {"action": "restart_analysis", "analysis_id": analysis.pk},
                )

            self.assertRedirects(
                response,
                f"/_authenticated/admin/video-analyzer/?analysis={analysis.pk}",
                fetch_redirect_response=False,
            )
            starter.assert_called_once()
            analysis.refresh_from_db()
            self.assertEqual(analysis.status, VideoVisualizerStatus.QUEUED)
            self.assertEqual(analysis.video.name, source_name)
            self.assertFalse(bool(analysis.processed_video))
            self.assertEqual(analysis.tracks.count(), 0)
            self.assertEqual(analysis.total_detections, 0)
            self.assertEqual(analysis.frames_processed, 0)
            self.assertEqual(analysis.confidence_threshold, 38)
            self.assertEqual(analysis.iou_threshold, 52)
            self.assertEqual(analysis.input_resolution, 768)

    def test_completed_analyzer_plays_original_with_live_mask_overlay(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            analysis = VideoVisualizerAnalysis.objects.create(
                video=SimpleUploadedFile("road.mp4", b"original-video", content_type="video/mp4"),
                processed_video=SimpleUploadedFile("road-processed.mp4", b"processed-video", content_type="video/mp4"),
                original_filename="road.mp4",
                file_hash="c" * 64,
                file_type="mp4",
                fps=30,
                status=VideoVisualizerStatus.COMPLETE,
                model_session=self.model,
                created_by=self.user,
            )
            response = self.client.get(f"/_authenticated/admin/video-analyzer/?analysis={analysis.pk}")
            self.assertContains(response, analysis.video.url)
            self.assertContains(response, 'id="viz-toggle-masks"')
            self.assertContains(response, '<option value="0.1">0.1x</option>', html=True)
            self.assertContains(response, '<option value="0.25">0.25x</option>', html=True)

    @override_settings(
        AUTO_START_ANALYSIS_WORKER=False,
        ALLOW_DETECTION_MODE=True,
        DETECTION_MASK_REFINEMENT=True,
        MODEL_REQUIRE_LOCAL_EVALUATION=True,
    )
    def test_completed_detection_can_be_requeued_for_estimated_masks(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            model_path = Path(media_root) / "detect.pt"
            model_path.write_bytes(b"detection-model")
            self.model.model_task = "detect"
            self.model.model_file = str(model_path)
            self.model.model_sha256 = hashlib.sha256(b"detection-model").hexdigest()
            self.model.save(update_fields=["model_task", "model_file", "model_sha256"])
            analysis = VideoVisualizerAnalysis.objects.create(
                video=SimpleUploadedFile("detect-road.mp4", b"raw-video", content_type="video/mp4"),
                processed_video=SimpleUploadedFile("detect-road-processed.mp4", b"processed", content_type="video/mp4"),
                original_filename="detect-road.mp4",
                file_hash="6" * 64,
                file_type="mp4",
                status=VideoVisualizerStatus.COMPLETE,
                total_detections=3,
                total_unique_potholes=1,
                model_session=self.model,
                created_by=self.user,
            )
            analyzer = self.client.get(f"/_authenticated/admin/video-analyzer/?analysis={analysis.pk}")
            self.assertContains(analyzer, "visual-only estimated masks")
            self.assertContains(analyzer, "Generate estimated masks")
            report = self.client.get(f"/_authenticated/admin/video-visualizer/{analysis.pk}/report/")
            self.assertContains(report, "box-derived visual estimates only")

            response = self.client.post(
                "/_authenticated/admin/video-analyzer/",
                {"action": "reanalyze_with_estimated_masks", "analysis_id": analysis.pk},
            )
            self.assertEqual(response.status_code, 302)
            analysis.refresh_from_db()
            self.assertEqual(analysis.status, VideoVisualizerStatus.QUEUED)
            self.assertEqual(analysis.total_detections, 0)
            self.assertFalse(bool(analysis.processed_video))

    @override_settings(AUTO_START_ANALYSIS_WORKER=False)
    def test_queued_analyzer_shows_raw_video_preview(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            analysis = VideoVisualizerAnalysis.objects.create(
                video=SimpleUploadedFile("queued-road.mp4", b"raw-video", content_type="video/mp4"),
                original_filename="queued-road.mp4",
                file_hash="d" * 64,
                file_type="mp4",
                fps=30,
                frame_count=300,
                status=VideoVisualizerStatus.QUEUED,
                model_session=self.model,
                created_by=self.user,
            )
            response = self.client.get(f"/_authenticated/admin/video-analyzer/?analysis={analysis.pk}")
            self.assertContains(response, 'id="visualizer-video"')
            self.assertContains(response, analysis.video.url)
            self.assertContains(response, "raw video preview is available")
            self.assertContains(response, f'data-status-url="/_authenticated/admin/video-analyzer/{analysis.pk}/status/"')
            self.assertContains(response, 'id="processing-modal"')

    @override_settings(AUTO_START_ANALYSIS_WORKER=True)
    def test_queued_analyzer_recovers_worker_when_opened(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            analysis = VideoVisualizerAnalysis.objects.create(
                video=SimpleUploadedFile("stalled-road.mp4", b"raw-video", content_type="video/mp4"),
                original_filename="stalled-road.mp4",
                file_hash="f" * 64,
                file_type="mp4",
                status=VideoVisualizerStatus.QUEUED,
                model_session=self.model,
                created_by=self.user,
            )
            with patch("console.views.start_analysis_worker") as starter:
                response = self.client.get(f"/_authenticated/admin/video-analyzer/?analysis={analysis.pk}")
            self.assertEqual(response.status_code, 200)
            starter.assert_called_once_with(analysis.pk)

    def test_default_analyzer_prefers_latest_completed_video(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            completed = VideoVisualizerAnalysis.objects.create(
                video=SimpleUploadedFile("completed.mp4", b"completed", content_type="video/mp4"),
                original_filename="completed.mp4",
                file_hash="1" * 64,
                file_type="mp4",
                status=VideoVisualizerStatus.COMPLETE,
                model_session=self.model,
                created_by=self.user,
            )
            VideoVisualizerAnalysis.objects.create(
                video=SimpleUploadedFile("new-running.mp4", b"running", content_type="video/mp4"),
                original_filename="new-running.mp4",
                file_hash="2" * 64,
                file_type="mp4",
                status=VideoVisualizerStatus.RUNNING,
                model_session=self.model,
                created_by=self.user,
            )
            response = self.client.get("/_authenticated/admin/video-analyzer/")
            self.assertContains(response, completed.video.url)

    def test_detection_results_are_managed_in_defect_inventory(self):
        analysis = VideoVisualizerAnalysis.objects.create(
            original_filename="inventory-road.mp4",
            file_hash="e" * 64,
            file_type="mp4",
            fps=30,
            status=VideoVisualizerStatus.COMPLETE,
            model_session=self.model,
            created_by=self.user,
        )
        track = VideoPotholeTrack.objects.create(
            analysis=analysis,
            track_id=3,
            average_confidence=0.81,
            highest_confidence=0.9,
            lowest_confidence=0.7,
            appearance_count=4,
            severity="high",
        )
        analyzer = self.client.get(f"/_authenticated/admin/video-analyzer/?analysis={analysis.pk}")
        inventory = self.client.get(f"/_authenticated/admin/detections/?analysis={analysis.pk}")
        self.assertNotContains(analyzer, "<h2>Detection Results</h2>", html=True)
        self.assertNotContains(analyzer, "<h2>Defect Inventory</h2>", html=True)
        self.assertContains(analyzer, "Defect Inventory")
        self.assertContains(inventory, f"A{analysis.pk}-P3")
        self.assertContains(inventory, "inventory-road.mp4")

        response = self.client.post(
            "/_authenticated/admin/detections/",
            {
                "action": "review_track",
                "track_id": track.pk,
                "review_status": "confirmed",
                "severity": "critical",
                "road_section": "Northbound lane",
                "remarks": "Verified during inventory review",
            },
        )
        self.assertEqual(response.status_code, 302)
        track.refresh_from_db()
        self.assertEqual(track.review_status, VideoTrackReviewStatus.CONFIRMED)
        self.assertEqual(track.severity, "critical")
        self.assertEqual(track.road_section, "Northbound lane")

    def test_inventory_add_to_training_opens_mask_editor(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (320, 240), "gray").save(image_buffer, format="JPEG")
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            analysis = VideoVisualizerAnalysis.objects.create(
                original_filename="draw-mask-road.mp4",
                file_hash="5" * 64,
                file_type="mp4",
                status=VideoVisualizerStatus.COMPLETE,
                model_session=self.model,
                created_by=self.user,
            )
            track = VideoPotholeTrack.objects.create(
                analysis=analysis,
                track_id=8,
                label="Pothole",
                best_bbox={"center_x": 0.5, "center_y": 0.5, "width": 0.25, "height": 0.2},
                best_segmentation_points=[[0.4, 0.4], [0.6, 0.42], [0.58, 0.6], [0.42, 0.58]],
                snapshot_frame=SimpleUploadedFile(
                    "draw-mask-frame.jpg",
                    image_buffer.getvalue(),
                    content_type="image/jpeg",
                ),
            )

            response = self.client.post(
                "/_authenticated/admin/detections/",
                {"action": "add_track_to_dataset", "track_id": track.pk},
            )

            record = DatasetImage.objects.get(source_group=f"video-analysis-{analysis.pk}")
            editor_url = f"/_authenticated/admin/training-dataset/?tab=annotate&image={record.pk}"
            self.assertRedirects(response, editor_url, fetch_redirect_response=False)
            editor = self.client.get(editor_url)
            self.assertContains(editor, "Draw mask")
            self.assertContains(editor, "Save masks to dataset")

    def test_keyframe_timeline_uses_best_frame_and_live_review_status(self):
        analysis = VideoVisualizerAnalysis.objects.create(
            original_filename="timeline-road.mp4",
            file_hash="7" * 64,
            file_type="mp4",
            fps=30,
            duration_seconds=10,
            status=VideoVisualizerStatus.COMPLETE,
            timeline_markers=[{"track_id": 4, "status": "unresolved", "timestamp": 1}],
            model_session=self.model,
            created_by=self.user,
        )
        VideoPotholeTrack.objects.create(
            analysis=analysis,
            track_id=4,
            label="Road damage",
            best_frame=150,
            review_status=VideoTrackReviewStatus.CONFIRMED,
            average_confidence=0.8,
            highest_confidence=0.9,
            lowest_confidence=0.7,
        )
        response = self.client.get(f"/_authenticated/admin/video-analyzer/?analysis={analysis.pk}")
        self.assertContains(response, 'id="video-timeline-scrubber"')
        self.assertContains(response, 'class="video-marker confirmed road_damage"')
        self.assertContains(response, 'style="--marker-percent: 50.0%;"')
        self.assertContains(response, "arrow keys move between keyframes")

    def test_pothole_snapshot_thumbnail_opens_full_detection_frame(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            analysis = VideoVisualizerAnalysis.objects.create(
                original_filename="snapshot-road.mp4",
                file_hash="3" * 64,
                file_type="mp4",
                status=VideoVisualizerStatus.COMPLETE,
                model_session=self.model,
                created_by=self.user,
            )
            track = VideoPotholeTrack.objects.create(
                analysis=analysis,
                track_id=7,
                label="Pothole",
                snapshot_crop=SimpleUploadedFile("p7-crop.jpg", b"crop", content_type="image/jpeg"),
                snapshot_frame=SimpleUploadedFile("p7-frame.jpg", b"frame", content_type="image/jpeg"),
            )

            response = self.client.get(f"/_authenticated/admin/video-analyzer/?analysis={analysis.pk}")

            self.assertContains(response, 'class="snapshot-preview-link"')
            self.assertContains(response, f'href="{track.snapshot_frame.url}"')
            self.assertContains(response, f'src="{track.snapshot_crop.url}"')
            self.assertContains(response, 'aria-label="View pothole P7 in full size"')
            self.assertContains(response, "View pothole")

    def test_analysis_summary_uses_compact_defect_composition_chart(self):
        analysis = VideoVisualizerAnalysis.objects.create(
            original_filename="summary-road.mp4",
            file_hash="4" * 64,
            file_type="mp4",
            status=VideoVisualizerStatus.COMPLETE,
            total_unique_potholes=10,
            total_detections=121,
            frames_processed=1332,
            model_session=self.model,
            created_by=self.user,
        )
        VideoPotholeTrack.objects.create(
            analysis=analysis,
            track_id=11,
            label="Road damage",
        )

        response = self.client.get(f"/_authenticated/admin/video-analyzer/?analysis={analysis.pk}")

        self.assertContains(response, 'class="analysis-summary-panel"')
        self.assertContains(response, 'aria-label="Defect composition: 10 potholes and 1 road damage tracks"')
        self.assertContains(response, "11 total")
        self.assertContains(response, "Total detections")
        self.assertContains(response, "121")
        self.assertNotContains(response, "video-stat-grid")
