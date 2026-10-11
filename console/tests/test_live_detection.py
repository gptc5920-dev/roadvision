from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from PIL import Image, ImageDraw

from console.live_detection import LiveDetectionError, detect_live_frame
from console.models import AnalyzerConfiguration


@override_settings(ALLOW_DETECTION_MODE=True)
class LiveInferenceTests(SimpleTestCase):
    def setUp(self):
        buffer = BytesIO()
        Image.new("RGB", (320, 240)).save(buffer, format="JPEG")
        self.frame = buffer.getvalue()
        self.model = Mock(task="detect", names={0: "pothole", 1: "road_damage", 2: "garbage"})
        self.model.predict.return_value = [SimpleNamespace(boxes=None)]

    def test_filters_classes_before_detection_limit_and_uses_auto_device(self):
        for cuda, device in [(False, "cpu"), (True, "0")]:
            with self.subTest(cuda=cuda), patch(
                "console.live_detection._active_model", return_value=self.model
            ), patch("torch.cuda.is_available", return_value=cuda):
                config = AnalyzerConfiguration(device="auto", half_precision=True, include_road_damage=False)
                detect_live_frame(self.frame, config, 30)
                options = self.model.predict.call_args.kwargs
                self.assertEqual(options["device"], device)
                self.assertEqual(options["half"], cuda)
                self.assertEqual(options["classes"], [0])

    def test_includes_road_damage_when_enabled(self):
        with patch("console.live_detection._active_model", return_value=self.model):
            detect_live_frame(self.frame, AnalyzerConfiguration(include_road_damage=True), 30)
        self.assertEqual(self.model.predict.call_args.kwargs["classes"], [0, 1])

    def test_unsupported_model_releases_inference_lock(self):
        with patch("console.live_detection._active_model", return_value=self.model):
            self.model.names = {0: "garbage"}
            with self.assertRaisesMessage(LiveDetectionError, "no supported"):
                detect_live_frame(self.frame, AnalyzerConfiguration(), 30)
            self.model.names = {0: "pothole"}
            self.assertEqual(detect_live_frame(self.frame, AnalyzerConfiguration(), 30)["total_detections"], 0)

    def masked_result(self, task="detect", empty=False):
        import numpy as np
        import torch
        from ultralytics.engine.results import Results

        frame = np.full((240, 320, 3), 190, dtype=np.uint8)
        boxes = np.empty((0, 6), dtype=np.float32) if empty else np.array([[55, 50, 185, 130, .9, 0]], dtype=np.float32)
        masks = None
        if task == "segment" and not empty:
            masks = np.zeros((1, 240, 320), dtype=np.uint8)
            masks[0, 60:120, 65:175] = 1
        return Results(frame, path="frame.jpg", names={0: "pothole"}, boxes=torch.from_numpy(boxes), masks=torch.from_numpy(masks) if masks is not None else None)

    @override_settings(DETECTION_MASK_REFINEMENT=True)
    def test_live_detection_generates_bounded_estimated_masks(self):
        picture = Image.new("RGB", (320, 240), (190, 190, 190))
        ImageDraw.Draw(picture).ellipse((65, 65, 175, 115), fill=(35, 35, 35))
        buffer = BytesIO()
        picture.save(buffer, format="PNG")
        self.model.predict.return_value = [self.masked_result()]
        with patch("console.live_detection._active_model", return_value=self.model):
            result = detect_live_frame(buffer.getvalue(), AnalyzerConfiguration(), 30)
        detection = result["detections"][0]
        self.assertEqual(detection["mask_source"], "estimated")
        self.assertGreaterEqual(len(detection["segmentation_points"]), 3)
        self.assertTrue(all(55/320 <= x < 185/320 and 50/240 <= y < 130/240 for x, y in detection["segmentation_points"]))

    @override_settings(DETECTION_MASK_REFINEMENT=False)
    def test_disabling_refinement_keeps_boxes_only(self):
        self.model.predict.return_value = [self.masked_result()]
        with patch("console.live_detection._active_model", return_value=self.model):
            result = detect_live_frame(self.frame, AnalyzerConfiguration(), 30)
        self.assertEqual(result["detections"][0]["segmentation_points"], [])
        self.assertEqual(result["detections"][0]["mask_source"], "none")

    def test_native_masks_are_returned_and_empty_frames_are_valid(self):
        self.model.task = "segment"
        with patch("console.live_detection._active_model", return_value=self.model):
            self.model.predict.return_value = [self.masked_result("segment")]
            result = detect_live_frame(self.frame, AnalyzerConfiguration(), 30)
            self.assertEqual(result["detections"][0]["mask_source"], "model")
            self.assertGreaterEqual(len(result["detections"][0]["segmentation_points"]), 3)
            self.model.predict.return_value = [self.masked_result("segment", empty=True)]
            self.assertEqual(detect_live_frame(self.frame, AnalyzerConfiguration(), 30)["detections"], [])
            self.model.predict.return_value = [self.masked_result("detect")]
            with self.assertRaisesMessage(LiveDetectionError, "without instance masks"):
                detect_live_frame(self.frame, AnalyzerConfiguration(), 30)
