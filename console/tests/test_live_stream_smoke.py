"""RUN_STREAM_SMOKE=1 exercises an actual HTTP camera stream and bundled YOLO weights."""
import hashlib
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from console.management.commands.run_video_visualizer_analysis import Command
from console.models import TrainingSession, VideoVisualizerAnalysis, VideoVisualizerStatus
from console.views import read_video_stream_metadata


@skipUnless(os.environ.get("RUN_STREAM_SMOKE") == "1", "Set RUN_STREAM_SMOKE=1 for a real HTTP stream and YOLO test")
@override_settings(ALLOW_PRIVATE_STREAMS=True, ALLOW_DETECTION_MODE=True, DETECTION_MASK_REFINEMENT=False,
                   AUTO_START_ANALYSIS_WORKER=False, LIVE_PREVIEW_INTERVAL_SECONDS=0.01)
class RealStreamSmokeTests(TestCase):
    def test_real_http_stream_is_decoded_inferred_saved_and_stopped(self):
        import cv2
        import numpy as np
        import torch
        from ultralytics import YOLO

        torch.set_num_threads(2)
        artifact = Path(settings.BASE_DIR) / "models/registered/f380cd373f61-potholenet-yolo11m-v1.pt"
        self.assertTrue(artifact.is_file())
        frame = cv2.imread(str(Path(settings.BASE_DIR) / "media/diagnostics/analysis-13-samples/frame-1399.jpg"))
        if frame is None:
            frame = np.full((240, 320, 3), 180, dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", frame)
        self.assertTrue(ok)
        image = encoded.tobytes()
        class Camera(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    for _ in range(60):
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(image)).encode() + b"\r\n\r\n" + image + b"\r\n")
                        self.wfile.flush()
                        time.sleep(0.02)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Camera)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/camera"
            metadata = read_video_stream_metadata(url)
            self.assertEqual(metadata["width"], frame.shape[1])
            self.assertGreaterEqual(metadata["frame_count"], 0)
            with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
                session = TrainingSession.objects.create(status="complete", model_task="detect", is_validated=True,
                    map50="0.5909", model_file=str(artifact), model_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
                analysis = VideoVisualizerAnalysis.objects.create(model_session=session, source_url=url, source_type="live-stream",
                    is_continuous=True, device="cpu", tracker="iou", fps=metadata["fps"], width=metadata["width"],
                    height=metadata["height"], confidence_threshold=30, input_resolution=320)
                model = YOLO(str(artifact))
                predictions = []
                def stop_after_inference(predictor):
                    predictions.append(True)
                    if len(predictions) >= 3:
                        VideoVisualizerAnalysis.objects.filter(pk=analysis.pk).update(stop_requested=True)
                model.add_callback("on_predict_end", stop_after_inference)
                command = Command()
                claimed = command.claim_next("real-stream-check", analysis.pk)
                try:
                    command.process_analysis(claimed, cv2, lambda _: model)
                finally:
                    capture = getattr(command, "_active_capture", None)
                    if capture is not None: capture.release()
                analysis.refresh_from_db()
                self.assertEqual(analysis.status, VideoVisualizerStatus.COMPLETE)
                self.assertGreaterEqual(len(predictions), 3)
                self.assertGreater(analysis.frames_processed, 0)
                self.assertTrue(analysis.live_preview_frame.storage.exists(analysis.live_preview_frame.name))
                user = get_user_model().objects.create_user(username="stream-smoke-engineer")
                self.client.force_login(user)
                response = self.client.get(f"/_authenticated/admin/video-analyzer/{analysis.pk}/media/live-preview/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "image/jpeg")
                self.assertTrue(b"".join(response.streaming_content).startswith(b"\xff\xd8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
