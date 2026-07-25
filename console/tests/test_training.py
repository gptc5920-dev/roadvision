from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from console.forms import TrainingConfigForm
from console.management.commands.run_yolo_training import (
    augmentation_options,
    metrics_from_validator,
    session_images,
)
from console.models import (
    AnalyzerConfiguration,
    DatasetImage,
    DatasetImageStatus,
    DatasetSplit,
    DatasetVersion,
    PotholeAnnotation,
    TrainingSession,
)
from console.views import next_balanced_split, parse_annotation_payload, queue_automatic_training


@override_settings(DATASET_MIN_TRAIN_IMAGES=1, DATASET_MIN_VAL_IMAGES=1, DATASET_MIN_TEST_IMAGES=1)
class TrainingPipelineTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="trainer@example.com", password="test-password")
        self.client.force_login(self.user)
        self.version = DatasetVersion.objects.create(version_number=1, created_by=self.user)
        for index, split in enumerate(DatasetSplit.values):
            image = DatasetImage.objects.create(
                image=f"training/{split}.jpg",
                original_filename=f"{split}.jpg",
                file_hash=f"{index + 1:064x}",
                split=split,
                status=DatasetImageStatus.APPROVED,
                dataset_version=self.version,
            )
            PotholeAnnotation.objects.create(
                image=image,
                center_x=0.5,
                center_y=0.5,
                width=0.2,
                height=0.2,
                segmentation_points=[[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]],
            )

    def test_training_session_freezes_dataset_membership(self):
        form = TrainingConfigForm(
            {
                "model_name": "yolo11s-seg",
                "epochs": 100,
                "batch_size": 8,
                "image_size": 640,
                "learning_rate": "0.001",
                "device": "cpu",
                "patience": 30,
                "workers": 0,
                "optimizer": "AdamW",
                "augmentation_profile": "balanced",
                "seed": 42,
                "freeze_layers": 0,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        session = form.save(self.user, self.version)
        self.assertEqual(len(session.dataset_manifest), 3)

        DatasetImage.objects.create(
            image="training/later.jpg",
            original_filename="later.jpg",
            file_hash="f" * 64,
            split=DatasetSplit.TRAIN,
            status=DatasetImageStatus.APPROVED,
        )
        self.assertEqual(len(session_images(session)), 3)

    def test_training_manifest_freezes_annotation_content(self):
        session = queue_automatic_training(self.user, self.version)
        entry = session.dataset_manifest[0]
        original_labels = list(entry["labels"])
        image = DatasetImage.objects.get(pk=entry["id"])
        annotation = image.annotations.get()
        annotation.segmentation_points = [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]
        annotation.save(update_fields=["segmentation_points"])
        session.refresh_from_db()
        self.assertEqual(session.dataset_manifest[0]["labels"], original_labels)

    def test_road_scene_profiles_never_flip_frames_vertically(self):
        self.assertEqual(augmentation_options("balanced")["flipud"], 0.0)
        self.assertGreater(
            augmentation_options("aggressive")["mosaic"],
            augmentation_options("conservative")["mosaic"],
        )

    def test_validator_metrics_are_normalized(self):
        validator = SimpleNamespace(
            results_dict={
                "metrics/precision(B)": 0.81,
                "metrics/recall(B)": 0.72,
                "metrics/mAP50(B)": 0.77,
                "metrics/mAP50-95(B)": 0.44,
            }
        )
        self.assertEqual(
            metrics_from_validator(validator),
            {"precision": 0.81, "recall": 0.72, "map50": 0.77, "map5095": 0.44},
        )

    def test_small_uploads_are_balanced_across_all_splits(self):
        DatasetImage.objects.all().delete()
        selected = []
        for index in range(3):
            split = next_balanced_split()
            selected.append(split)
            DatasetImage.objects.create(
                image=f"training/auto-{index}.jpg",
                original_filename=f"auto-{index}.jpg",
                file_hash=f"{index + 10:064x}",
                split=split,
                status=DatasetImageStatus.APPROVED,
            )
        self.assertEqual(selected, [DatasetSplit.TRAIN, DatasetSplit.VAL, DatasetSplit.TEST])

    def test_ready_upload_queues_training_without_user_configuration(self):
        session = queue_automatic_training(self.user, self.version)
        self.assertIsNotNone(session)
        self.assertEqual(session.status, TrainingSession.Status.QUEUED)
        self.assertEqual(session.model_name, "yolo11s-seg")
        self.assertEqual(session.augmentation_profile, "balanced")
        self.assertEqual(len(session.dataset_manifest), 3)
        self.assertIsNone(queue_automatic_training(self.user, self.version))

    def test_settings_can_queue_yolo26_on_the_same_dataset(self):
        first = queue_automatic_training(self.user, self.version)
        self.assertEqual(first.model_name, "yolo11s-seg")
        configuration = AnalyzerConfiguration.objects.get(pk=1)
        configuration.training_model = "yolo26s-seg"
        configuration.save(update_fields=["training_model"])

        second = queue_automatic_training(self.user, self.version)

        self.assertIsNotNone(second)
        self.assertEqual(second.model_name, "yolo26s-seg")

    def test_manual_training_form_accepts_yolo26_segmentation(self):
        form = TrainingConfigForm(
            {
                "model_name": "yolo26n-seg",
                "epochs": 10,
                "batch_size": 4,
                "image_size": 640,
                "learning_rate": "0.001",
                "device": "cpu",
                "patience": 5,
                "workers": 0,
                "optimizer": "AdamW",
                "augmentation_profile": "balanced",
                "seed": 42,
                "freeze_layers": 0,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_yolo_labels_use_segmentation_polygons(self):
        image = DatasetImage.objects.get(split=DatasetSplit.TRAIN)
        annotation = image.annotations.get()
        annotation.segmentation_points = [[0.4, 0.4], [0.6, 0.4], [0.55, 0.6], [0.42, 0.58]]
        annotation.save(update_fields=["segmentation_points"])
        values = annotation.yolo_line.split()
        self.assertEqual(values[0], "0")
        self.assertEqual(len(values), 9)

    def test_manual_annotation_payload_requires_a_real_polygon(self):
        with self.assertRaisesRegex(ValueError, "polygon mask"):
            parse_annotation_payload('[{"center_x": 0.5, "center_y": 0.5, "width": 0.2, "height": 0.2}]')
        parsed = parse_annotation_payload(
            '[{"segmentation_points": [[0.4, 0.4], [0.6, 0.4], [0.55, 0.6]]}]'
        )
        self.assertEqual(len(parsed[0]["segmentation_points"]), 3)
        self.assertGreater(parsed[0]["width"], 0)

    def test_freehand_editor_renders_and_saves_mask_points(self):
        image = DatasetImage.objects.get(split=DatasetSplit.TRAIN)
        response = self.client.get(f"/_authenticated/admin/training-dataset/?tab=annotate&image={image.pk}")
        self.assertContains(response, "Draw mask")
        self.assertContains(response, "press and drag around the visible pothole boundary")
        self.assertContains(response, 'id="undo-last-mask"')
        self.assertContains(response, "Save masks to dataset")
        response = self.client.post(
            "/_authenticated/admin/training-dataset/",
            {
                "action": "save_annotations",
                "image_id": image.pk,
                "annotations_json": '[{"segmentation_points": [[0.3, 0.4], [0.6, 0.4], [0.55, 0.7]]}]',
            },
        )
        self.assertEqual(response.status_code, 302)
        image.refresh_from_db()
        self.assertEqual(len(image.annotations.get().segmentation_points), 3)
        self.assertEqual(image.status, DatasetImageStatus.FULL)

    def test_review_moves_an_entire_source_group_between_splits(self):
        grouped = DatasetImage.objects.filter(split__in=[DatasetSplit.TRAIN, DatasetSplit.VAL])
        grouped.update(source_group="route-17")
        response = self.client.post(
            "/_authenticated/admin/training-dataset/",
            {"action": "move_source_group", "source_group": "route-17", "split": DatasetSplit.TEST},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(DatasetImage.objects.filter(source_group="route-17").exclude(split=DatasetSplit.TEST).exists())
