from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from PIL import Image

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
