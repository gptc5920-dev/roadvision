import hashlib
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from console.management.commands.run_yolo_training import metrics_from_validator, validate_session_images
from console.models import DatasetImage, DatasetImageStatus, PotholeAnnotation


class TrainingIntegrityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="integrity@example.com", email="integrity@example.com")
        self.client.force_login(self.user)

    def test_box_metrics_cannot_pass_a_segmentation_gate(self):
        result = metrics_from_validator(SimpleNamespace(results_dict={"metrics/mAP50(B)": .99}))
        self.assertIsNone(result["map50"])
        for value in [float("nan"), float("inf"), -1, 1.1]:
            with self.subTest(value=value), self.assertRaises(CommandError):
                metrics_from_validator(SimpleNamespace(results_dict={"metrics/mAP50(M)": value}))

    def test_duplicate_files_in_one_request_create_one_image_and_one_file(self):
        buffer = io.BytesIO()
        Image.new("RGB", (320, 240), "gray").save(buffer, format="PNG")
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), patch(
            "console.views.model_readiness", return_value={"ready": False, "session": None}
        ):
            response = self.client.post("/api/actions/dataset/", {
                "action": "upload_images", "source_group": "survey-a",
                "train_percent": 70, "val_percent": 20, "test_percent": 10,
                "images": [SimpleUploadedFile(name, buffer.getvalue(), "image/png") for name in ["one.png", "two.png"]],
            })
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(DatasetImage.objects.count(), 1)
            self.assertEqual(len(list(Path(media).rglob("*.png"))), 1)
            self.assertIn("duplicate", str(response.json()))

    def test_invalid_forms_and_identifiers_return_json_details(self):
        response = self.client.post("/api/actions/dataset/", {"action": "start_training", "device": "whatever"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("device", response.json()["fields"])
        response = self.client.post("/api/actions/dataset/", {"action": "review_image", "image_id": "bad"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("image_id", response.json()["fields"])

    def test_saved_annotations_reset_review_and_invalid_payload_preserves_labels(self):
        image = DatasetImage.objects.create(image="example.png", file_hash="a" * 64,
            status=DatasetImageStatus.APPROVED, reviewed_by=self.user, reviewed_at=timezone.now())
        mask = [[.2, .2], [.4, .2], [.3, .4]]
        payload = [{"segmentation_points": mask}, {"segmentation_points": [[.6, .6], [.8, .6], [.7, .8]]}]
        response = self.client.post("/api/actions/dataset/", {"action": "save_annotations", "image_id": image.pk, "annotations_json": json.dumps(payload)})
        self.assertEqual(response.status_code, 200, response.content)
        image.refresh_from_db()
        self.assertEqual(image.annotations.count(), 2)
        self.assertEqual(image.status, DatasetImageStatus.FULL)
        self.assertIsNone(image.reviewed_at)
        self.assertIsNone(image.reviewed_by)
        response = self.client.post("/api/actions/dataset/", {"action": "save_annotations", "image_id": image.pk, "annotations_json": '[{"segmentation_points": 123}]'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(image.annotations.count(), 2)

    def test_profile_email_uses_the_user_record(self):
        self.user.email = "changed@example.com"
        self.user.save(update_fields=["email"])
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.email, "changed@example.com")

    @override_settings(DATASET_MIN_TRAIN_IMAGES=1, DATASET_MIN_VAL_IMAGES=1, DATASET_MIN_TEST_IMAGES=1)
    def test_snapshot_checks_hashes_and_source_group_leakage(self):
        images, manifest = [], {}
        for index, split in enumerate(["train", "val", "test"]):
            image = DatasetImage.objects.create(image=f"{split}.png", file_hash=str(index) * 64, split=split)
            images.append(image)
            labels = ["0 0.2 0.2 0.4 0.2 0.3 0.4"]
            manifest[image.pk] = {"labels": labels, "label_sha256": hashlib.sha256(json.dumps(labels, separators=(",", ":")).encode()).hexdigest(),
                "source_group": f"survey-{index}", "annotation_count": 1, "segmentation_annotation_count": 1}
        validate_session_images(images, manifest)
        manifest[images[0].pk]["labels"] = ["tampered"]
        with self.assertRaisesRegex(CommandError, "hash mismatch"):
            validate_session_images(images, manifest)
        manifest[images[0].pk]["source_group"] = "survey-1"
        with self.assertRaisesRegex(CommandError, "multiple splits"):
            validate_session_images(images, manifest)
