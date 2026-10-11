import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from unittest.mock import patch

from console.models import (
    AppRole,
    PotholeReport,
    ReportStatus,
    Severity,
    UserRole,
    VideoDatasetSample,
    VideoPotholeTrack,
    VideoVisualizerAnalysis,
    VideoVisualizerStatus,
    TrainingSession,
)


class ReactSpaApiTests(TestCase):
    def test_track_api_exposes_saved_mask_coordinates_and_provenance(self):
        self.client.force_login(self.user)
        for task, source in [("detect", "estimated"), ("segment", "model")]:
            session = TrainingSession.objects.create(model_task=task)
            analysis = VideoVisualizerAnalysis.objects.create(
                original_filename="masked.mp4", file_hash="a"*64, file_type="mp4",
                status=VideoVisualizerStatus.COMPLETE, model_session=session,
            )
            polygon = [[.2, .2], [.4, .2], [.3, .4]]
            track = VideoPotholeTrack.objects.create(analysis=analysis, track_id=1, best_segmentation_points=polygon)
            response = self.client.get(f"/api/analyzer/?analysis={analysis.pk}")
            self.assertEqual(response.status_code, 200)
            serialized = response.json()["selected"]["tracks"][0]
            self.assertEqual(serialized["segmentationPoints"], polygon)
            self.assertEqual(serialized["maskSource"], source)
            inventory = self.client.get(f"/api/detections/?analysis={analysis.pk}")
            self.assertEqual(inventory.json()["tracks"][0]["maskSource"], source)
            track.best_segmentation_points = []
            track.save(update_fields=["best_segmentation_points"])
            self.assertEqual(self.client.get(f"/api/detections/?analysis={analysis.pk}").json()["tracks"][0]["maskSource"], "none")

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="spa-admin@example.com",
            email="spa-admin@example.com",
            password="RoadVision-SPA-2026!",
        )
        UserRole.objects.update_or_create(user=self.user, defaults={"role": AppRole.ADMIN})

    def test_public_and_nested_spa_routes_use_single_shell(self):
        for path in ["/", "/login", "/app/dashboard", "/app/analyzer", "/app/dataset?tab=annotate"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertContains(response, 'id="root"')
            self.assertRegex(response.content.decode(), r'/static/console/react/assets/index-[^" ]+\.js')
            self.assertEqual(response["Cache-Control"], "no-cache")
            for asset in re.findall(r'/static/console/react/assets/[^" ]+', response.content.decode()):
                self.assertTrue((settings.BASE_DIR / asset.lstrip("/")).is_file(), asset)

    def test_session_and_login_are_json(self):
        anonymous = self.client.get("/api/session/")
        self.assertEqual(anonymous.status_code, 200)
        self.assertFalse(anonymous.json()["authenticated"])

        login = self.client.post(
            "/api/auth/login/",
            data='{"email":"spa-admin@example.com","password":"RoadVision-SPA-2026!"}',
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        session = self.client.get("/api/session/").json()
        self.assertTrue(session["authenticated"])
        self.assertTrue(session["user"]["isAdmin"])

    def test_login_rejects_malformed_and_non_object_json(self):
        malformed = self.client.post(
            "/api/auth/login/",
            data='{"email":',
            content_type="application/json",
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertIn("malformed JSON", malformed.json()["error"])

        non_object = self.client.post(
            "/api/auth/login/",
            data="[]",
            content_type="application/json",
        )
        self.assertEqual(non_object.status_code, 400)
        self.assertIn("must be an object", non_object.json()["error"])

    def test_spa_csrf_token_survives_login_rotation(self):
        browser = Client(enforce_csrf_checks=True)
        initial_token = browser.get("/api/session/").json()["csrfToken"]
        login = browser.post(
            "/api/auth/login/",
            data='{"email":"spa-admin@example.com","password":"RoadVision-SPA-2026!"}',
            content_type="application/json",
            HTTP_X_CSRFTOKEN=initial_token,
        )

        self.assertEqual(login.status_code, 200)
        rotated_token = login.json()["csrfToken"]
        self.assertNotEqual(rotated_token, initial_token)
        logout = browser.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=rotated_token)
        self.assertEqual(logout.status_code, 200)

    def test_protected_api_rejects_anonymous_users(self):
        response = self.client.get("/api/overview/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "Authentication required.")

    def test_overview_and_report_action_use_json_contract(self):
        self.client.force_login(self.user)
        report = PotholeReport.objects.create(
            lat=14.5995,
            lng=120.9842,
            severity=Severity.HIGH,
            status=ReportStatus.OPEN,
            detected_at=timezone.now(),
            city="Manila",
            device_id="CAM-01",
        )
        overview = self.client.get("/api/overview/")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["metrics"]["openReports"], 1)

        update = self.client.post(
            "/api/actions/modules/dispatch/",
            {"action": "update_report_status", "report_id": report.pk, "status": ReportStatus.RESOLVED},
        )
        self.assertEqual(update.status_code, 200)
        self.assertTrue(update.json()["ok"])
        report.refresh_from_db()
        self.assertEqual(report.status, ReportStatus.RESOLVED)

    def test_invalid_report_status_is_not_reported_as_success(self):
        self.client.force_login(self.user)
        report = PotholeReport.objects.create(
            lat=14.5995,
            lng=120.9842,
            severity=Severity.HIGH,
            status=ReportStatus.OPEN,
            detected_at=timezone.now(),
            city="Manila",
            device_id="CAM-01",
        )
        response = self.client.post(
            "/api/actions/modules/dispatch/",
            {"action": "update_report_status", "report_id": report.pk, "status": "invalid"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        report.refresh_from_db()
        self.assertEqual(report.status, ReportStatus.OPEN)

    def test_action_routes_reject_missing_cross_domain_and_json_actions(self):
        self.client.force_login(self.user)
        missing = self.client.post("/api/actions/modules/dispatch/", {})
        self.assertEqual(missing.status_code, 400)

        cross_domain = self.client.post(
            "/api/actions/modules/dispatch/",
            {"action": "add_sample", "name": "Wrong route"},
        )
        self.assertEqual(cross_domain.status_code, 400)
        self.assertFalse(VideoDatasetSample.objects.exists())

        json_action = self.client.post(
            "/api/actions/modules/dispatch/",
            data='{"action":"update_report_status"}',
            content_type="application/json",
        )
        self.assertEqual(json_action.status_code, 415)

    def test_invalid_source_and_track_values_return_field_errors(self):
        self.client.force_login(self.user)
        source = self.client.post(
            "/api/actions/modules/detection-sources/",
            {
                "action": "add_sample",
                "name": "Survey",
                "road_name": "Road 1",
                "city": "Manila",
                "duration_seconds": "not-a-number",
                "frame_count": "10",
                "fps": "30",
            },
        )
        self.assertEqual(source.status_code, 400)
        self.assertIn("duration_seconds", source.json()["fields"])
        self.assertFalse(VideoDatasetSample.objects.exists())

        analysis = VideoVisualizerAnalysis.objects.create(
            original_filename="review.mp4",
            file_hash="a" * 64,
            file_type="mp4",
            status=VideoVisualizerStatus.COMPLETE,
        )
        track = VideoPotholeTrack.objects.create(analysis=analysis, track_id=1)
        review = self.client.post(
            "/api/actions/modules/detections/",
            {
                "action": "review_track",
                "track_id": track.pk,
                "review_status": "confirmed",
                "severity": "high",
                "measured_depth_mm": "not-a-number",
                "latitude": "91",
                "longitude": "181",
            },
        )
        self.assertEqual(review.status_code, 400)
        self.assertIn("measured_depth_mm", review.json()["fields"])
        self.assertIn("latitude", review.json()["fields"])

    def test_profile_update_rejects_duplicate_email(self):
        duplicate = get_user_model().objects.create_user(
            username="duplicate@example.com",
            email="duplicate@example.com",
            password="Duplicate-2026!",
        )
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/actions/modules/settings/",
            {
                "action": "update_profile",
                "full_name": "SPA Admin",
                "email": duplicate.email.upper(),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["fields"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "spa-admin@example.com")

    def test_reports_are_bounded_and_query_parameters_are_validated(self):
        self.client.force_login(self.user)
        for index in range(3):
            PotholeReport.objects.create(
                lat=14.5,
                lng=120.9,
                severity=Severity.LOW,
                detected_at=timezone.now(),
                city="Manila",
                device_id=f"CAM-{index}",
            )
        response = self.client.get("/api/reports/?limit=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["reports"]), 2)
        self.assertTrue(response.json()["pagination"]["truncated"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(self.client.get("/api/reports/?limit=all").status_code, 400)
        self.assertEqual(self.client.get("/api/analyzer/?analysis=0").status_code, 400)

    @patch("console.api.views.analysis_frame_detections")
    def test_analyzer_api_does_not_load_unused_frame_artifacts(self, frame_loader):
        self.client.force_login(self.user)
        analysis = VideoVisualizerAnalysis.objects.create(
            original_filename="complete.mp4",
            file_hash="b" * 64,
            file_type="mp4",
            status=VideoVisualizerStatus.COMPLETE,
        )
        response = self.client.get(f"/api/analyzer/?analysis={analysis.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("frameDetections", response.json()["selected"])
        frame_loader.assert_not_called()

    def test_overview_query_count_stays_bounded(self):
        self.client.force_login(self.user)
        self.client.get("/api/overview/")
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/overview/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 12)

    def test_legacy_console_route_redirects_to_spa(self):
        self.client.force_login(self.user)
        response = self.client.get("/_authenticated/admin/overview/")
        self.assertRedirects(response, "/app/dashboard", fetch_redirect_response=False)

    def test_every_spa_read_api_returns_json(self):
        self.client.force_login(self.user)
        for path in [
            "/api/overview/",
            "/api/reports/",
            "/api/fleet/",
            "/api/detections/",
            "/api/analyzer/",
            "/api/dataset/",
            "/api/sources/",
            "/api/personnel/",
            "/api/settings/",
        ]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response["Content-Type"], "application/json")
