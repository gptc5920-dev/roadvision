"""Opt-in real CPU training: RUN_YOLO_SMOKE=1 python manage.py test console.tests.test_yolo_smoke --settings=config.settings_test.

Synthetic images verify training mechanics only, never deployment accuracy.
The test uses an isolated database/media directory and downloads no weights.
"""
import hashlib
import io
import os
import tempfile
from pathlib import Path
from unittest import skipUnless

from PIL import Image, ImageDraw
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from console.management.commands.run_yolo_training import Command
from console.models import DatasetImage, DatasetImageStatus, PotholeAnnotation, TrainingSession
from console.readiness import training_dataset_manifest
from console.storage_paths import resolve_model_artifact


@skipUnless(os.environ.get("RUN_YOLO_SMOKE") == "1", "Set RUN_YOLO_SMOKE=1 for real CPU training")
@override_settings(DATASET_MIN_TRAIN_IMAGES=1, DATASET_MIN_VAL_IMAGES=1, DATASET_MIN_TEST_IMAGES=1,
                   MODEL_MIN_LOCAL_TEST_IMAGES=1, MODEL_MIN_MAP50=1.0)
class YoloCpuSmokeTests(TestCase):
    def test_worker_trains_exports_and_evaluates_real_segmentation_weights(self):
        import torch
        from ultralytics import YOLO

        torch.set_num_threads(2)
        with tempfile.TemporaryDirectory(prefix="roadvision-yolo-") as media, override_settings(MEDIA_ROOT=media):
            for index, split in enumerate(["train", "train", "val", "test"]):
                picture = Image.new("RGB", (128, 128), (100 + index * 10,) * 3)
                ImageDraw.Draw(picture).polygon([(32, 32), (96, 32), (96, 96), (32, 96)], fill=(30, 30, 30))
                buffer = io.BytesIO()
                picture.save(buffer, "PNG")
                data = buffer.getvalue()
                image = DatasetImage(file_hash=hashlib.sha256(data).hexdigest(), split=split,
                    original_filename=f"synthetic-{index}.png", source_group=f"synthetic-{index}",
                    width=128, height=128, status=DatasetImageStatus.APPROVED)
                image.image.save(image.original_filename, ContentFile(data))
                PotholeAnnotation.objects.create(image=image, center_x=.5, center_y=.5, width=.5, height=.5,
                    segmentation_points=[[.25, .25], [.75, .25], [.75, .75], [.25, .75]])
            session = TrainingSession.objects.create(model_name="yolo11n-seg", epochs=2, batch_size=2,
                image_size=128, workers=0, device="cpu", dataset_manifest=training_dataset_manifest())
            evidence = {}

            def factory(path):
                model = YOLO(path if Path(path).is_file() else "yolo11n-seg.yaml")
                if not Path(path).is_file():
                    def before(trainer):
                        evidence["before"] = next(trainer.model.parameters()).detach().clone()

                    def after(trainer):
                        evidence["changed"] = not torch.equal(evidence["before"], next(trainer.model.parameters()).detach())

                    model.add_callback("on_train_start", before)
                    model.add_callback("on_train_end", after)
                    original_train = model.train

                    def train(**options):
                        options.update(pretrained=False, plots=False, amp=False, mosaic=0, mixup=0,
                                       warmup_epochs=0, nbs=2)
                        return original_train(**options)

                    model.train = train
                original_val = model.val

                def val(**options):
                    options["plots"] = False
                    return original_val(**options)

                model.val = val
                return model

            command = Command()
            claimed = command.claim_next(session.pk)
            command.process_session(claimed, factory)
            session.refresh_from_db()
            self.assertEqual(session.status, TrainingSession.Status.COMPLETE, session.error_message)
            self.assertTrue(evidence.get("changed"), "Training must update backbone weights")
            self.assertTrue(resolve_model_artifact(session.model_file).is_file())
            self.assertEqual(session.model_task, "segment")
            self.assertEqual(session.local_test_images, 1)
            self.assertIsNotNone(session.local_map50)
            self.assertFalse(session.is_active_video_model)
            self.assertEqual(session.worker_id, "")
            self.assertIsNone(session.lease_expires_at)
