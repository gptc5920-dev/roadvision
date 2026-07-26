import hashlib
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from console.models import (
    DatasetImage,
    DatasetImageStatus,
    DatasetSplit,
    PotholeAnnotation,
    TrainingSession,
)
from console.readiness import dataset_readiness, model_readiness
from console.storage_paths import resolve_model_artifact


@override_settings(DATASET_MIN_TRAIN_IMAGES=1, DATASET_MIN_VAL_IMAGES=1, DATASET_MIN_TEST_IMAGES=1)
class ReadinessTests(TestCase):
    def test_windows_model_record_resolves_to_deployment_model_volume(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            base_dir = Path(temp_directory)
            registered = base_dir / "models" / "registered"
            registered.mkdir(parents=True)
            artifact = registered / "portable-model.pt"
            artifact.write_bytes(b"model")
            with override_settings(BASE_DIR=base_dir, MEDIA_ROOT=base_dir / "media"):
                resolved = resolve_model_artifact(
                    r"C:\Users\operator\roadvision\models\registered\portable-model.pt"
                )
            self.assertEqual(resolved, artifact)

    def test_dataset_requires_approved_annotations_in_every_split(self):
        self.assertFalse(dataset_readiness()["ready"])
        for index, split in enumerate(DatasetSplit.values):
            image = DatasetImage.objects.create(
                image=f"test/{split}.jpg",
                original_filename=f"{split}.jpg",
                file_hash=str(index) * 64,
                split=split,
                status=DatasetImageStatus.APPROVED,
            )
            PotholeAnnotation.objects.create(
                image=image,
                center_x=0.5,
                center_y=0.5,
                width=0.2,
                height=0.2,
                segmentation_points=[[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]],
            )
        self.assertTrue(dataset_readiness()["ready"])

    def test_approved_negative_examples_count_but_each_split_needs_a_positive(self):
        for index, split in enumerate(DatasetSplit.values):
            DatasetImage.objects.create(
                image=f"test/negative-{split}.jpg",
                original_filename=f"negative-{split}.jpg",
                file_hash=f"{index + 20:064x}",
                split=split,
                status=DatasetImageStatus.APPROVED,
            )
        readiness = dataset_readiness()
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["counts"], {split: 1 for split in DatasetSplit.values})
        self.assertEqual(readiness["positive_counts"], {split: 0 for split in DatasetSplit.values})

    def test_source_group_cannot_span_train_and_test(self):
        for index, split in enumerate(DatasetSplit.values):
            image = DatasetImage.objects.create(
                image=f"test/group-{split}.jpg",
                original_filename=f"group-{split}.jpg",
                file_hash=f"{index + 40:064x}",
                split=split,
                source_group="same-survey" if split != DatasetSplit.VAL else "validation-survey",
                status=DatasetImageStatus.APPROVED,
            )
            PotholeAnnotation.objects.create(
                image=image,
                center_x=0.5,
                center_y=0.5,
                width=0.2,
                height=0.2,
                segmentation_points=[[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]],
            )
        readiness = dataset_readiness()
        self.assertFalse(readiness["ready"])
        self.assertTrue(any("same-survey" in error for error in readiness["errors"]))

    def test_box_only_positive_is_not_training_ready(self):
        for index, split in enumerate(DatasetSplit.values):
            image = DatasetImage.objects.create(
                image=f"test/box-{split}.jpg",
                original_filename=f"box-{split}.jpg",
                file_hash=f"{index + 50:064x}",
                split=split,
                source_group=f"box-{split}",
                status=DatasetImageStatus.APPROVED,
            )
            PotholeAnnotation.objects.create(image=image, center_x=0.5, center_y=0.5, width=0.2, height=0.2)
        self.assertFalse(dataset_readiness()["ready"])

    @override_settings(MODEL_MIN_MAP50=0.5, MODEL_REQUIRE_LOCAL_EVALUATION=False)
    def test_model_requires_artifact_hash_and_metric(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            artifact = Path(temp_directory) / "model.pt"
            artifact.write_bytes(b"artifact")
            session = TrainingSession.objects.create(
                status=TrainingSession.Status.COMPLETE,
                is_validated=True,
                is_active_video_model=True,
                model_file=str(artifact),
                model_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                map50=0.75,
                model_task="segment",
            )
            self.assertTrue(model_readiness(session)["ready"])
            artifact.write_bytes(b"artifact changed")
            self.assertFalse(model_readiness(session)["ready"])
            session.model_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
            session.save(update_fields=["model_sha256"])
            self.assertTrue(model_readiness(session)["ready"])
            session.model_sha256 = ""
            session.save(update_fields=["model_sha256"])
            self.assertFalse(model_readiness(session)["ready"])

    @override_settings(
        MODEL_MIN_MAP50=0.5,
        MODEL_REQUIRE_LOCAL_EVALUATION=True,
        ALLOW_DETECTION_MODE=True,
        DETECTION_MASK_REFINEMENT=True,
    )
    def test_validated_detection_model_can_run_in_standard_detection_mode(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            artifact = Path(temp_directory) / "detect.pt"
            artifact.write_bytes(b"detection-model")
            session = TrainingSession.objects.create(
                status=TrainingSession.Status.COMPLETE,
                is_validated=True,
                model_file=str(artifact),
                model_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                map50=0.86,
                model_task="detect",
            )
            readiness = model_readiness(session)
            self.assertTrue(readiness["ready"])
            self.assertIn("visual-only estimated masks", readiness["warnings"][0])
