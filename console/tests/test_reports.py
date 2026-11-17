from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from console.models import AppRole, DatasetAuditLog, PotholeReport, UserRole, VideoVisualizerAnalysis


class ReportManagementTests(TestCase):
    records_url = "/api/reports/records/"
    action_url = "/api/actions/modules/reports/"

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="report-editor", password="test-password")
        UserRole.objects.filter(user=self.user).update(role=AppRole.ENGINEER)
        self.client.force_login(self.user)
        self.report = PotholeReport.objects.create(
            city="Manila", device_id="CAM-01", lat=14.6, lng=121,
            severity="high", notes="Initial survey", detected_at=timezone.now(),
        )
        self.analysis = VideoVisualizerAnalysis.objects.create(report=self.report)
        self.fields = {
            "action": "update_report", "report_id": self.report.pk,
            "city": "Laguna", "device_id": "PHONE-02", "lat": "14.4", "lng": "121.2",
            "severity": "critical", "status": "in-progress", "notes": "Rechecked on site",
            "detected_at": "2026-11-17T12:34:56+08:00", "image_url": "https://example.com/evidence.jpg",
        }

    def test_edit_saves_report_information_and_preserves_analysis_links(self):
        response = self.client.post(self.action_url, self.fields)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["ok"])
        self.report.refresh_from_db()
        self.analysis.refresh_from_db()
        self.assertEqual(self.analysis.report_id, self.report.pk)
        self.assertEqual(PotholeReport.objects.count(), 1)
        for field in ["city", "device_id", "severity", "status", "notes", "image_url"]:
            self.assertEqual(getattr(self.report, field), self.fields[field])
        self.assertEqual(self.report.lat, 14.4)
        self.assertEqual(self.report.lng, 121.2)
        self.assertEqual(self.report.detected_at.isoformat(), "2026-11-17T04:34:56+00:00")
        self.assertEqual(response.json()["report"]["id"], self.report.pk)
        self.assertTrue(DatasetAuditLog.objects.filter(action="report-update", created_by=self.user).exists())
        self.assertEqual(self.client.get(f"/api/analyzer/?report={self.report.pk}").json()["report"]["city"], "Laguna")

    def test_invalid_information_returns_fields_without_partial_updates(self):
        for field, value in [
            ("lat", "91"), ("lng", "-181"), ("severity", "unknown"),
            ("status", "unknown"), ("city", ""), ("device_id", ""),
            ("detected_at", "invalid"), ("image_url", "not-a-url"), ("notes", "x" * 2001),
        ]:
            with self.subTest(field=field):
                response = self.client.post(self.action_url, self.fields | {field: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json()["fields"])
                self.report.refresh_from_db()
                self.assertEqual(self.report.city, "Manila")
                self.assertEqual(self.report.status, "open")
        self.assertFalse(DatasetAuditLog.objects.filter(action="report-update").exists())

    def test_blank_optional_fields_can_be_cleared(self):
        response = self.client.post(self.action_url, self.fields | {"notes": "", "image_url": ""})
        self.assertEqual(response.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.notes, "")
        self.assertEqual(self.report.image_url, "")

    def test_edit_requires_an_existing_positive_report_id(self):
        for identifier, status in [(None, 400), ("", 400), ("abc", 400), ("0", 400), ("-1", 400), ("9" * 19, 400), (self.report.pk + 100, 404)]:
            with self.subTest(identifier=identifier):
                fields = self.fields | {"report_id": identifier}
                if identifier is None:
                    fields.pop("report_id")
                self.assertEqual(self.client.post(self.action_url, fields).status_code, status)

    def test_records_and_edit_require_console_access(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.records_url).status_code, 401)
        self.assertEqual(self.client.post(self.action_url, self.fields).status_code, 401)
        self.client.force_login(self.user)
        UserRole.objects.filter(user=self.user).update(role=AppRole.VIEWER)
        self.assertEqual(self.client.get(self.records_url).status_code, 403)
        self.assertEqual(self.client.post(self.action_url, self.fields).status_code, 403)
        self.report.refresh_from_db()
        self.assertEqual(self.report.city, "Manila")
        UserRole.objects.filter(user=self.user).update(role=AppRole.ADMIN)
        self.assertEqual(self.client.post(self.action_url, self.fields).status_code, 200)

    def test_actions_remain_restricted_to_the_reports_route(self):
        self.assertEqual(self.client.post(self.action_url, {"action": "remove_analysis"}).status_code, 400)
        self.assertEqual(self.client.post("/api/actions/modules/live-map/", self.fields).status_code, 400)
        self.assertEqual(self.client.post(self.action_url, self.fields, content_type="application/json").status_code, 415)
        self.assertEqual(self.client.get(self.action_url).status_code, 405)
        self.assertEqual(self.client.post(self.records_url, self.fields).status_code, 405)

    def test_search_and_pagination_cover_every_report_in_stable_order(self):
        for index in range(4):
            PotholeReport.objects.create(
                city="Cavite", device_id=f"CAM-{index + 2}", lat=14.5, lng=121,
                severity="low", detected_at=self.report.detected_at,
            )
        first = self.client.get(self.records_url, {"limit": 2}).json()
        second = self.client.get(self.records_url, {"limit": 2, "offset": 2}).json()
        self.assertEqual(first["pagination"], {"limit": 2, "offset": 0, "total": 5})
        ids = [item["id"] for item in first["reports"] + second["reports"]]
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(ids, sorted(ids, reverse=True))
        for term in ["Manila", "CAM-01", "Initial survey", str(self.report.pk)]:
            with self.subTest(term=term):
                data = self.client.get(self.records_url, {"q": term}).json()
                self.assertIn(self.report.pk, [item["id"] for item in data["reports"]])
        self.assertEqual(self.client.get(self.records_url, {"q": "no-such-report"}).json()["reports"], [])

    def test_pagination_rejects_invalid_bounds(self):
        for params in [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": "invalid"}, {"offset": "9" * 50}]:
            self.assertEqual(self.client.get(self.records_url, params).status_code, 400)
