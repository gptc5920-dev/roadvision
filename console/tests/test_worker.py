import os
import gzip
import json
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from django.core.files.base import ContentFile

from django.test import TestCase, override_settings

from console.management.commands.run_video_visualizer_analysis import (
    Command,
    merge_fragmented_tracks,
    transcode_browser_mp4,
)
from console.models import DetectionTest, TrainingSession, VideoVisualizerAnalysis, VideoVisualizerStatus
from console.segmentation import estimate_detection_mask


class WorkerLeaseTests(TestCase):
    @override_settings(DETECTION_MASK_REFINEMENT=True, ALLOW_DETECTION_MODE=True)
    def test_masks_render_and_persist_after_an_empty_video_frame(self):
        import cv2
        import numpy as np
        import torch
        from ultralytics.engine.results import Results

        for task in ["segment", "detect"]:
            with self.subTest(task=task), tempfile.TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
                frame = np.full((160, 240, 3), 190, dtype=np.uint8)
                cv2.ellipse(frame, (120, 90), (55, 25), 0, 0, 360, (35, 35, 35), -1)
                source = Path(root) / "road.avi"
                writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 30, (240, 160))
                self.assertTrue(writer.isOpened())
                for _ in range(9):
                    writer.write(frame)
                writer.release()
                artifact = Path(root) / "model.pt"
                artifact.write_bytes(b"mask-test-artifact")
                session = TrainingSession.objects.create(
                    status=TrainingSession.Status.COMPLETE, model_task=task,
                    model_file=str(artifact), model_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                )
                analysis = VideoVisualizerAnalysis.objects.create(
                    original_filename="road.avi", file_hash="a" * 64, file_type="avi",
                    model_session=session, device="cpu", tracker="iou", calibration_m_per_pixel=.01,
                )
                analysis.video.save("road.avi", ContentFile(source.read_bytes()))
                empty = Results(frame, path=str(source), names={0: "pothole"}, boxes=torch.empty((0, 6)))
                native_masks = None
                if task == "segment":
                    native_masks = np.zeros((1, 160, 240), dtype=np.uint8)
                    cv2.ellipse(native_masks[0], (120, 90), (55, 25), 0, 0, 360, 1, -1)
                positive = Results(
                    frame, path=str(source), names={0: "pothole"},
                    boxes=torch.tensor([[55, 50, 185, 130, .9, 0]]), masks=torch.from_numpy(native_masks) if native_masks is not None else None,
                )
                model = Mock(task=task, names={0: "pothole"})
                model.predict.side_effect = [[empty], [positive], [positive]]
                def transcode(path):
                    output = str(path) + ".browser.mp4"
                    shutil.copyfile(path, output)
                    return output
                command = Command()
                claimed = command.claim_next("mask-test-worker", analysis.pk)
                try:
                    with patch("console.management.commands.run_video_visualizer_analysis.model_readiness", return_value={"ready": True}), patch(
                        "console.management.commands.run_video_visualizer_analysis.transcode_browser_mp4", side_effect=transcode,
                    ):
                        command.process_analysis(claimed, cv2, lambda _path: model)
                finally:
                    for resource in ["_active_capture", "_active_writer"]:
                        handle = getattr(command, resource, None)
                        if handle is not None:
                            handle.release()
                    handle = getattr(command, "_active_detection_stream", None)
                    if handle is not None:
                        handle.close()
                    for resource in ["_active_output_path", "_active_detection_path"]:
                        path = getattr(command, resource, None)
                        if path and Path(path).exists():
                            Path(path).unlink()
                analysis.refresh_from_db()
                self.assertEqual(analysis.status, VideoVisualizerStatus.COMPLETE)
                self.assertEqual(analysis.frames_processed, 3)
                track = analysis.tracks.get()
                self.assertGreaterEqual(len(track.best_segmentation_points), 3)
                with gzip.open(analysis.frame_detections_artifact.path, "rt") as recorded:
                    frames = json.load(recorded)
                self.assertEqual(frames[0]["detections"], [])
                expected_source = "model" if task == "segment" else "estimated"
                self.assertEqual(frames[1]["detections"][0]["mask_source"], expected_source)
                self.assertGreaterEqual(len(frames[1]["detections"][0]["segmentation_points"]), 3)
                if task == "detect":
                    self.assertIsNone(track.estimated_surface_area_sqm)
                else:
                    self.assertGreater(track.estimated_surface_area_sqm, 0)
                capture = cv2.VideoCapture(analysis.processed_video.path)
                try:
                    for _ in range(4):
                        ok, rendered = capture.read()
                    self.assertTrue(ok)
                    blue, green, red = [int(value) for value in rendered[90, 120]]
                    self.assertGreater(red, green + 20)
                    if task == "segment":
                        self.assertGreater(blue, green + 20)
                    else:
                        self.assertGreater(green, blue + 20)
                finally:
                    capture.release()

    def test_empty_segmentation_image_completes_without_a_mask_error(self):
        import cv2
        import numpy as np
        import torch
        from ultralytics.engine.results import Results
        from console.views import run_optional_detection

        with tempfile.TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
            artifact = Path(root) / "model.pt"
            artifact.write_bytes(b"mask-test-artifact")
            session = TrainingSession.objects.create(model_file=str(artifact), model_task="segment")
            test = DetectionTest.objects.create(model_session=session, original_filename="empty.png")
            frame = np.full((160, 240, 3), 190, dtype=np.uint8)
            _, encoded = cv2.imencode(".png", frame)
            test.image.save("empty.png", ContentFile(encoded.tobytes()))
            result = Results(frame, path="empty.png", names={0: "pothole"}, boxes=torch.empty((0, 6)))
            model = Mock(task="segment")
            model.predict.return_value = [result]
            with patch("ultralytics.YOLO", return_value=model):
                run_optional_detection(test)
            test.refresh_from_db()
            self.assertEqual(test.status, "complete")
            self.assertEqual(test.detection_count, 0)
            self.assertTrue(test.result_image)

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
