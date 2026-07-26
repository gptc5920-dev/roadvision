import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from console.models import AnalyzerConfiguration, TrainingSession


class BundledModelBootstrapTests(TestCase):
    @override_settings(ALLOW_DETECTION_MODE=True)
    def test_bootstrap_creates_and_idempotently_activates_bundled_model(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            artifact = (
                base_dir
                / "models"
                / "registered"
                / "f380cd373f61-potholenet-yolo11m-v1.pt"
            )
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"validated-bundled-model")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

            with (
                override_settings(BASE_DIR=base_dir),
                patch(
                    "console.management.commands.bootstrap_bundled_model.EXPECTED_SHA256",
                    digest,
                ),
                patch(
                    "console.management.commands.bootstrap_bundled_model.inspect_model",
                    return_value=("detect", {0: "pothole", 1: "road_damage"}),
                ),
            ):
                call_command("bootstrap_bundled_model", verbosity=0)
                call_command("bootstrap_bundled_model", verbosity=0)

        sessions = TrainingSession.objects.filter(model_sha256=digest)
        self.assertEqual(sessions.count(), 1)
        session = sessions.get()
        self.assertTrue(session.is_active_video_model)
        self.assertTrue(session.is_validated)
        self.assertEqual(session.model_task, "detect")
        self.assertEqual(AnalyzerConfiguration.objects.get(pk=1).model_session, session)
