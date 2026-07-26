import statistics
import time

from django.core.management.base import BaseCommand, CommandError

from console.models import TrainingSession
from console.readiness import model_artifact_matches
from console.storage_paths import resolve_model_artifact


class Command(BaseCommand):
    help = "Benchmark a registered model on evenly sampled video frames without changing application data."

    def add_arguments(self, parser):
        parser.add_argument("video_path")
        parser.add_argument("--session-id", type=int)
        parser.add_argument("--frames", type=int, default=20)
        parser.add_argument("--sizes", default="512,640,768")
        parser.add_argument("--device", default="auto")

    def handle(self, *args, **options):
        try:
            import cv2
            import numpy as np
            import torch
            from ultralytics import YOLO
        except Exception as exc:
            raise CommandError("OpenCV, NumPy, Torch, and Ultralytics are required.") from exc
        sessions = TrainingSession.objects.filter(status=TrainingSession.Status.COMPLETE).exclude(model_file="")
        session = sessions.filter(pk=options.get("session_id")).first() if options.get("session_id") else sessions.first()
        if session is None:
            raise CommandError("No completed model session is available.")
        if not model_artifact_matches(session):
            raise CommandError("The model artifact is missing or does not match its registered SHA-256 hash.")
        capture = cv2.VideoCapture(options["video_path"])
        if not capture.isOpened():
            raise CommandError("Video could not be opened.")
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        indexes = np.linspace(0, max(total_frames - 1, 0), max(1, options["frames"]), dtype=int)
        frames = []
        for index in indexes:
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, frame = capture.read()
            if ok:
                frames.append(frame)
        capture.release()
        if not frames:
            raise CommandError("No frames could be decoded.")
        device = options["device"]
        if device == "auto":
            device = "0" if torch.cuda.is_available() else "cpu"
        model = YOLO(str(resolve_model_artifact(session.model_file)))
        self.stdout.write(f"session={session.pk} task={model.task} device={device} frames={len(frames)}")
        for size in [int(value.strip()) for value in options["sizes"].split(",") if value.strip()]:
            model.predict(source=frames[0], imgsz=size, conf=0.25, device=device, verbose=False, save=False)
            durations = []
            detections = 0
            for frame in frames:
                started = time.perf_counter()
                result = model.predict(source=frame, imgsz=size, conf=0.25, device=device, verbose=False, save=False)[0]
                durations.append((time.perf_counter() - started) * 1000)
                detections += len(result.boxes) if result.boxes is not None else 0
            mean_ms = statistics.mean(durations)
            p95_ms = sorted(durations)[min(len(durations) - 1, int(len(durations) * 0.95))]
            self.stdout.write(
                f"imgsz={size} mean_ms={mean_ms:.2f} p95_ms={p95_ms:.2f} "
                f"fps={1000 / mean_ms:.3f} detections={detections}"
            )
