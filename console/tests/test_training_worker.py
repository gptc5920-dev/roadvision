from datetime import timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from console.management.commands.run_yolo_training import Command, TrainingLeaseLost
from console.models import AnalyzerConfiguration, TrainingSession


@override_settings(TRAINING_LEASE_SECONDS=60)
class TrainingWorkerTests(TestCase):
    def test_explicit_failed_run_returns_a_command_error(self):
        session = TrainingSession.objects.create(model_name="yolo11n-seg")
        def fail(claimed, model_factory):
            TrainingSession.objects.filter(pk=claimed.pk).update(
                status=TrainingSession.Status.FAILED, error_message="Training dependency failed",
            )
        command = Command()
        with patch.object(command, "process_session", side_effect=fail):
            with self.assertRaisesRegex(CommandError, "Training dependency failed"):
                command.handle(session_id=session.pk, watch=False, poll_interval=5)

    def test_claim_reserves_a_session_and_other_workers_cannot_claim_it(self):
        session = TrainingSession.objects.create(model_name="yolo11n-seg")
        claimed = Command().claim_next()
        self.assertEqual(claimed.pk, session.pk)
        self.assertTrue(claimed.worker_id)
        self.assertGreater(claimed.lease_expires_at, timezone.now())
        self.assertIsNone(Command().claim_next())

    def test_stopped_workers_and_legacy_running_jobs_become_retryable(self):
        for lease in (None, timezone.now() - timedelta(seconds=1)):
            session = TrainingSession.objects.create(
                status=TrainingSession.Status.RUNNING,
                started_at=timezone.now() - timedelta(minutes=2),
                worker_id="stopped-worker", lease_expires_at=lease,
            )
            self.assertIsNone(Command().claim_next())
            session.refresh_from_db()
            self.assertEqual(session.status, TrainingSession.Status.FAILED)
            self.assertIn("Retry", session.error_message)
            self.assertEqual(session.worker_id, "")

    def test_heartbeats_extend_the_lease_and_fence_an_old_worker(self):
        session = TrainingSession.objects.create(model_name="yolo11n-seg")
        worker = Command()
        claimed = worker.claim_next(session.pk)
        TrainingSession.objects.filter(pk=session.pk).update(lease_expires_at=timezone.now())
        worker.renew_lease(claimed, current_epoch=2, progress=20)
        session.refresh_from_db()
        self.assertEqual(session.current_epoch, 2)
        self.assertGreater(session.lease_expires_at, timezone.now())
        TrainingSession.objects.filter(pk=session.pk).update(worker_id="replacement")
        with self.assertRaises(TrainingLeaseLost):
            worker.renew_lease(claimed, current_epoch=3)
        session.refresh_from_db()
        self.assertEqual(session.current_epoch, 2)

    def test_failed_model_loading_releases_the_job_and_preserves_active_model(self):
        active = TrainingSession.objects.create(
            status=TrainingSession.Status.COMPLETE, is_validated=True,
            is_active_video_model=True,
        )
        AnalyzerConfiguration.objects.update_or_create(pk=1, defaults={"model_session": active})
        session = TrainingSession.objects.create(model_name="yolo11n-seg", dataset_manifest=[{"id": 1}])
        command = Command()
        claimed = command.claim_next(session.pk)
        with patch("console.management.commands.run_yolo_training.write_dataset_for_session",
                   return_value=(Path("unused"), Path("unused/data.yaml"))):
            command.process_session(claimed, lambda _: (_ for _ in ()).throw(RuntimeError("Weights unavailable")))
        session.refresh_from_db()
        self.assertEqual(session.status, TrainingSession.Status.FAILED)
        self.assertEqual(session.error_message, "Weights unavailable")
        self.assertIsNone(session.lease_expires_at)
        self.assertEqual(AnalyzerConfiguration.objects.get(pk=1).model_session_id, active.pk)

    def test_api_retry_preserves_the_frozen_dataset_and_cannot_be_queued_twice(self):
        user = get_user_model().objects.create_user(username="training-worker-review")
        self.client.force_login(user)
        manifest = [{"id": 17, "labels": ["0 .1 .1 .2 .1 .2 .2"]}]
        session = TrainingSession.objects.create(
            status=TrainingSession.Status.FAILED, model_name="yolo11n-seg",
            dataset_manifest=manifest, error_message="Interrupted", current_epoch=5,
            progress=50, local_map50="0.1000",
        )
        response = self.client.post("/api/actions/dataset/", {"action": "retry_training", "session_id": session.pk})
        self.assertEqual(response.status_code, 200, response.content)
        session.refresh_from_db()
        self.assertEqual(session.status, TrainingSession.Status.QUEUED)
        self.assertEqual(session.dataset_manifest, manifest)
        self.assertEqual(session.current_epoch, 0)
        self.assertIsNone(session.local_map50)
        self.assertEqual(session.error_message, "")
        response = self.client.post("/api/actions/dataset/", {"action": "retry_training", "session_id": session.pk})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/actions/dataset/", {"action": "retry_training", "session_id": "bad"})
        self.assertEqual(response.status_code, 400)

    def test_failed_stale_worker_cannot_overwrite_a_replacement(self):
        session = TrainingSession.objects.create(dataset_manifest=[{"id": 1}])
        command = Command()
        claimed = command.claim_next(session.pk)
        TrainingSession.objects.filter(pk=session.pk).update(worker_id="replacement", progress=75)
        command.process_session(claimed, None)
        session.refresh_from_db()
        self.assertEqual(session.status, TrainingSession.Status.RUNNING)
        self.assertEqual(session.worker_id, "replacement")
        self.assertEqual(session.progress, 75)

    @override_settings(MODEL_MIN_MAP50=0.5, MODEL_MIN_LOCAL_TEST_IMAGES=1, MODEL_REQUIRE_LOCAL_EVALUATION=True)
    def test_successful_held_out_evaluation_promotes_the_saved_checkpoint(self):
        old = TrainingSession.objects.create(status=TrainingSession.Status.COMPLETE, is_active_video_model=True)
        AnalyzerConfiguration.objects.update_or_create(pk=1, defaults={"model_session": old})
        session = TrainingSession.objects.create(
            model_name="yolo11n-seg", epochs=1,
            dataset_manifest=[{"id": 1, "split": "test"}],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_dir = root / "runs" / "train"
            class Model:
                task = "segment"

                def __init__(self):
                    self.callbacks = {}

                def add_callback(self, event, callback):
                    self.callbacks[event] = callback

                def train(self, **options):
                    (save_dir / "weights").mkdir(parents=True)
                    (save_dir / "weights" / "best.pt").write_bytes(b"evaluated-checkpoint")
                    self.callbacks["on_train_epoch_end"](SimpleNamespace(epoch=0))
                    return SimpleNamespace(save_dir=save_dir)

                def val(self, **options):
                    if options["split"] != "test":
                        raise AssertionError("Promotion must use held-out test data")
                    return SimpleNamespace(results_dict={
                        "metrics/precision(M)": 0.8, "metrics/recall(M)": 0.75,
                        "metrics/mAP50(M)": 0.7, "metrics/mAP50-95(M)": 0.4,
                    })

            with patch("console.management.commands.run_yolo_training.write_dataset_for_session",
                       return_value=(root, root / "data.yaml")):
                command = Command()
                command.process_session(command.claim_next(session.pk), lambda _: Model())
            session.refresh_from_db()
            old.refresh_from_db()
            self.assertEqual(session.status, TrainingSession.Status.COMPLETE, session.error_message)
            self.assertTrue(session.is_validated)
            self.assertTrue(session.is_active_video_model)
            self.assertFalse(old.is_active_video_model)
            self.assertEqual(AnalyzerConfiguration.objects.get(pk=1).model_session_id, session.pk)
            self.assertAlmostEqual(float(session.local_map50), 0.7)
