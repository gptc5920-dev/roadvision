import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from console.management.commands.run_video_visualizer_analysis import (
    Command,
    merge_fragmented_tracks,
    transcode_browser_mp4,
)
from console.models import TrainingSession, VideoVisualizerAnalysis, VideoVisualizerStatus
from console.segmentation import estimate_detection_mask


class WorkerLeaseTests(TestCase):
    def test_processed_video_is_transcoded_to_browser_compatible_h264(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "opencv.mp4"
            source_path.write_bytes(b"opencv-video")

            def encode(command, **_kwargs):
                Path(command[-1]).write_bytes(b"h264-video")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch(
                "console.management.commands.run_video_visualizer_analysis.subprocess.run",
                side_effect=encode,
            ) as runner:
                output_path = transcode_browser_mp4(source_path)

            try:
                command = runner.call_args.args[0]
                self.assertIn("libx264", command)
                self.assertIn("yuv420p", command)
                self.assertIn("+faststart", command)
                self.assertEqual(Path(output_path).read_bytes(), b"h264-video")
            finally:
                if os.path.exists(output_path):
                    os.remove(output_path)

    def test_detection_mask_refinement_returns_bounded_foreground_polygon(self):
        import cv2
        import numpy as np

        cv2.setRNGSeed(7)
        frame = np.full((160, 240, 3), 190, dtype=np.uint8)
        cv2.ellipse(frame, (120, 90), (55, 25), 0, 0, 360, (35, 35, 35), -1)
        points = estimate_detection_mask(frame, (55, 50, 185, 130), cv2)
        self.assertGreaterEqual(len(points), 3)
        self.assertTrue(all(55 <= x < 185 and 50 <= y < 130 for x, y in points))
        self.assertGreater(cv2.contourArea(np.asarray(points, dtype=np.int32)), 2500)

    def test_detection_mask_refinement_rejects_missing_frame(self):
        import cv2

        self.assertEqual(estimate_detection_mask(None, (0, 0, 10, 10), cv2), [])

    def test_fragmented_tracks_are_merged_by_class_gap_and_iou(self):
        base = {
            "label": "pothole",
            "confidences": [0.7],
            "relative_bbox_size": 0.02,
            "best_confidence": 0.7,
            "best_frame": 1,
            "best_bbox_pixels": (0, 0, 10, 10),
            "best_frame_image": None,
            "best_crop_image": None,
            "best_bbox": {},
            "best_polygon": [],
            "lat": None,
            "lng": None,
        }
        states = {
            1: dict(base, confidences=[0.7], first_frame=1, last_frame=3, first_bbox_pixels=(0, 0, 10, 10), last_bbox_pixels=(0, 0, 10, 10)),
            2: dict(base, confidences=[0.7], first_frame=5, last_frame=8, first_bbox_pixels=(1, 1, 11, 11), last_bbox_pixels=(1, 1, 11, 11)),
            3: dict(base, confidences=[0.7], label="road_damage", first_frame=6, last_frame=9, first_bbox_pixels=(1, 1, 11, 11), last_bbox_pixels=(1, 1, 11, 11)),
        }
        merged, duplicates = merge_fragmented_tracks(states, max_gap_frames=5, iou_threshold=0.3)
        self.assertEqual(duplicates, 1)
        self.assertEqual(len(merged), 2)
        self.assertEqual(len(merged[1]["confidences"]), 2)

    @override_settings(ANALYSIS_LEASE_SECONDS=60)
    def test_job_is_claimed_once(self):
        model = TrainingSession.objects.create(
            status=TrainingSession.Status.COMPLETE,
            is_validated=True,
            model_file="models/test.pt",
        )
        analysis = VideoVisualizerAnalysis.objects.create(
            original_filename="road.mp4",
            file_hash="a" * 64,
            file_type="mp4",
            model_session=model,
        )
        command = Command()
        claimed = command.claim_next("worker-one")
        self.assertEqual(claimed.pk, analysis.pk)
        self.assertEqual(claimed.status, VideoVisualizerStatus.RUNNING)
        self.assertEqual(claimed.attempt_count, 1)
        self.assertIsNone(command.claim_next("worker-two"))

    def test_failure_is_retried_then_becomes_terminal(self):
        analysis = VideoVisualizerAnalysis.objects.create(
            original_filename="road.mp4",
            file_hash="b" * 64,
            file_type="mp4",
            status=VideoVisualizerStatus.RUNNING,
            attempt_count=1,
            max_attempts=2,
        )
        command = Command()
        command.mark_failure(analysis.pk, RuntimeError("temporary"))
        analysis.refresh_from_db()
        self.assertEqual(analysis.status, VideoVisualizerStatus.RETRYING)
        analysis.status = VideoVisualizerStatus.RUNNING
        analysis.attempt_count = 2
        analysis.save(update_fields=["status", "attempt_count"])
        command.mark_failure(analysis.pk, RuntimeError("terminal"))
        analysis.refresh_from_db()
        self.assertEqual(analysis.status, VideoVisualizerStatus.FAILED)
        self.assertEqual(len(analysis.error_history), 2)
