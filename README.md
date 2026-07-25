# RoadVision / RoadSense

RoadVision is a Django application for authenticated pothole-video analysis, engineering review, model training, and dataset feedback. It uses the XAMPP MariaDB database `roadsense` by default.

## Local setup

1. Start MySQL in XAMPP.
2. Install dependencies: `python -m pip install -r requirements.txt`
3. Apply schema changes: `python manage.py migrate`
4. Start the site: `python manage.py runserver`
5. Start a durable analysis worker in another terminal:

   `python manage.py run_video_visualizer_analysis --watch`

6. Start the automatic image-training worker in another terminal:

   `python manage.py run_yolo_training --watch`

The ignored `.env` contains the local database-scoped `roadsense_app` credentials. Use `.env.example` as the production template. Never deploy with the local file or default administrator password.

Administrators manage the active model, thresholds, tracker, device, overlays, and retry policy in **Settings**. **Video Analyzer** contains only survey upload/source fields and per-survey road, GPS, and calibration metadata.

## Fleet camera intake

Administrators register vehicle cameras in **Fleet Cams** with a stable device ID, road section, and optional RTSP/HTTP/HTTPS stream URL. An online camera can send footage to **Video Analyzer** in either of two ways:

- **Start continuous detection** validates the configured feed, switches the analyzer to real-time mode, and keeps detecting until an operator stops it. The worker publishes the latest annotated frame, persists completed tracks and snapshots, and reconnects interrupted feeds automatically without creating an unbounded video archive.
- **Live camera capture** opens the phone, tablet, or laptop camera, records a clip of up to 45 seconds, and uploads it as a fleet dashcam analysis.

Browser capture requires camera permission and HTTPS (localhost is also accepted by modern browsers). Trusted private-network camera feeds require `ALLOW_PRIVATE_STREAMS=true`; leave it disabled when private RTSP/HTTP sources are not needed. Camera credentials must not be embedded in saved stream URLs.

Continuous analysis is intended for RTSP/HTTP/HTTPS dashcam or IP-camera feeds. Browser-only phone cameras still use captured clips; expose the phone as a trusted stream source when it must operate as an always-on dashcam.

## Readiness gates

Run `python manage.py roadvision_readiness --strict` before processing production footage.

Analysis remains blocked until there is an active, validated pothole model with:

- a readable `.pt` artifact;
- an artifact that still matches its SHA-256 provenance hash;
- an enabled Ultralytics detection or segmentation task, with segmentation preferred when mask measurements are required;
- mAP50 at or above `MODEL_MIN_MAP50`;
- for segmentation models, an approved local held-out test evaluation with at least `MODEL_MIN_LOCAL_TEST_IMAGES` images;
- for segmentation models, local mask mAP50 at or above `MODEL_MIN_MAP50`.

Training remains blocked until every split contains approved annotated images. Defaults are 50 train, 10 validation, and 10 test images. These are minimum plumbing gates, not a claim that the resulting dataset is representative enough for deployment.

Register an externally evaluated model with:

`python manage.py register_pothole_model C:\path\best.pt --name pothole-v1 --map50 0.72 --map5095 0.48 --validated-by "DPWH external survey set 2026-07"`

Then evaluate it on RoadVision's approved, source-group-separated test split and activate only if it passes:

`python manage.py evaluate_pothole_model --session-id ID --image-size 512 --device auto --activate`

Users only upload road images in the Training module. The active validated
pothole model proposes masks for each upload. A person must review and approve
those masks; predictions are never treated as ground truth automatically. Supply
a stable source group (survey, route, or source-video ID) when uploading. Every
image in a source group stays in one of the 70/20/10 train/validation/test splits,
preventing adjacent frames from leaking into held-out results. A new session is
queued as soon as the reviewed dataset readiness gate passes. Run the durable automatic trainer with:

`python manage.py run_yolo_training --watch`

When a trained model passes the held-out test gate, it automatically becomes
the active video-analysis and image-labeling model.

Automatic training supports YOLO11 and YOLO26 instance-segmentation models.
Administrators choose the default training architecture in **Settings**. Image and video results
render each pothole with a translucent magenta instance mask, a red bounding
box, and a `pothole confidence` label. Box-derived masks are never accepted as
model segmentation, held-out mask evidence, calibrated measurement, or reviewed
ground truth.

Set `ALLOW_DETECTION_MODE=true` to support validated object-detection models as
a standard boxes-and-tracking workflow. Set `DETECTION_MASK_REFINEMENT=true` to
add bounded GrabCut foreground estimates inside detected boxes. These orange
masks are visual aids, remain labeled `estimated`, do not contribute to mask
evaluation or calibrated measurements, and still require manual review before
dataset use. `DETECTION_MASK_MAX_SIZE=192` bounds refinement work per box and
can be tuned from 64 to 512 to trade throughput for contour detail. Segmentation models provide the authoritative magenta masks and
automatically unlock mask-derived features.

Each queued training session freezes image IDs, source groups, image hashes,
label text, and label hashes, so later review changes cannot silently alter the experiment. Training is
deterministic by seed, supports pothole-oriented conservative/balanced/aggressive
augmentation profiles, uses cosine learning-rate decay and late mosaic shutdown,
and evaluates `best.pt` against the untouched test split. The validation gate
uses segmentation-mask metrics, and Training History reports local precision,
recall, mask mAP50, and mask mAP50-95.

For reproducible speed checks on representative footage, run:

`python manage.py benchmark_pothole_model C:\path\survey.mp4 --session-id ID --frames 20 --sizes 512,640,768 --device auto`

The operational default is 512-pixel input. `device=auto` selects CUDA when
available; CPU analysis automatically processes at least every third source
frame. Each completed analysis records inference FPS, source-frame throughput,
real-time factor, and the effective frame skip so speed figures are not confused.

## Job lifecycle

Analysis jobs use database leases and follow `queued -> running -> complete`, with retry, failed, and cancelled states. Expired worker leases are reclaimed. Each failure is appended to bounded error history, and the UI supports retry and cancellation.

Video uploads and processed outputs are streamed rather than copied into web-worker memory. Detailed frame detections are gzip artifacts instead of large MySQL JSON values. Set the optional S3-compatible variables from `.env.example` to move media out of the local filesystem.

## Output semantics

The Analyzer produces an annotated video, class-aware unique tracks, confidence statistics, timestamps, snapshots, GPS association, review state, and a CSV report. One-frame candidates are discarded by default, nearby fragmented tracks are merged by class/time/IoU, and the raw/discarded/merged counts remain auditable. Record an independently reviewed distinct-pothole count on an analysis and resolve every track to add a count-recall proxy, count error, and duplicate-rate evidence to model evaluation. This count proxy is not identity-matched event recall. Severity is a visual screening classification based on true mask area for segmentation models and bounding-box coverage for detection models. Physical length, width, area, and depth remain unavailable unless the track is explicitly camera-calibrated or field-measured.

The migration `0015_unify_legacy_analyses` exposes old `VideoAnalysis`/`DetectionEvent` records through the canonical Analyzer while retaining the legacy tables for traceability.

## Operations

- Health check: `GET /healthz/`
- Readiness: `python manage.py roadvision_readiness`
- Worker: `python manage.py run_video_visualizer_analysis --watch`
- One job: `python manage.py run_video_visualizer_analysis --analysis-id ID`
- Local model evaluation: `python manage.py evaluate_pothole_model --session-id ID`
- Model/video benchmark: `python manage.py benchmark_pothole_model C:\path\survey.mp4 --session-id ID`
- Tests: `python manage.py test`
- Read-only concurrency smoke check: `python manage.py roadvision_load_check --requests 50 --concurrency 5`

For production, run the web and worker processes separately, enable HTTPS, configure a strong `DJANGO_SECRET_KEY`, use restricted MySQL credentials, rotate the default account, configure centralized logs, and use durable object storage with retention policies.
