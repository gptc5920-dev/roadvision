import hashlib
import io
import json
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command, CommandError
from django.test import Client, TestCase, override_settings

from console.models import AppRole, UserRole


class SignInTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="engineer@example.com",
            email="engineer@example.com",
            password="correct-password",
        )

    def test_signup_mode_cannot_create_an_account(self):
        before = get_user_model().objects.count()
        response = self.client.post(
            "/api/auth/login/",
            {"mode": "signup", "full_name": "Blocked", "email": "new@example.com", "password": "password"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        self.assertEqual(get_user_model().objects.count(), before)

    def test_repeated_failures_do_not_block_correct_credentials(self):
        for path, success_status in [("/api/auth/login/", 200), ("/auth/", 302)]:
            with self.subTest(path=path):
                browser = Client()
                for _ in range(7):
                    response = browser.post(path, {
                        "email": self.user.email, "password": "wrong",
                    }, REMOTE_ADDR="192.0.2.1")
                    self.assertEqual(response.status_code, 400)
                response = browser.post(path, {
                    "email": self.user.email, "password": "correct-password",
                }, REMOTE_ADDR="192.0.2.1")
                self.assertEqual(response.status_code, success_status)
                self.assertEqual(int(browser.session["_auth_user_id"]), self.user.pk)

    def test_existing_cached_lockout_does_not_block_sign_in(self):
        old_key = "login:" + hashlib.sha256(
            f"192.0.2.1:{self.user.email}".encode()
        ).hexdigest()
        cache.set(old_key, 100, timeout=300)
        for path, success_status in [("/api/auth/login/", 200), ("/auth/", 302)]:
            with self.subTest(path=path):
                browser = Client()
                response = browser.post(path, {
                    "email": self.user.email, "password": "correct-password",
                }, REMOTE_ADDR="192.0.2.1")
                self.assertEqual(response.status_code, success_status)
                self.assertEqual(int(browser.session["_auth_user_id"]), self.user.pk)

    def test_valid_sign_in_redirects_to_analyzer(self):
        response = self.client.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": "correct-password"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["redirect"], "/app/analyzer")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_sign_in_page_is_owned_by_frontend_server(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 404)

    def test_createsuperuser_persists_account_and_assigns_console_admin(self):
        with patch.dict(os.environ, {"DJANGO_SUPERUSER_PASSWORD": "CLI-Admin-2026!"}):
            call_command(
                "createsuperuser", username="cli-admin", email="cli-admin@example.com",
                interactive=False, stdout=io.StringIO(),
            )
        account = get_user_model().objects.get(username="cli-admin")
        self.assertTrue(account.is_superuser)
        self.assertTrue(account.check_password("CLI-Admin-2026!"))
        self.assertEqual(account.console_role.role, AppRole.ADMIN)
        response = self.client.post("/api/auth/login/", {
            "email": account.email, "password": "CLI-Admin-2026!",
        })
        self.assertEqual(response.status_code, 200)
        session = self.client.get("/api/session/").json()
        self.assertTrue(session["user"]["isAdmin"])

    def test_existing_superuser_with_viewer_role_keeps_admin_access(self):
        account = get_user_model().objects.create_superuser(
            username="legacy-admin", email="legacy-admin@example.com", password="Legacy-2026!",
        )
        UserRole.objects.filter(user=account).update(role=AppRole.VIEWER)
        self.client.force_login(account)
        self.assertTrue(self.client.get("/api/session/").json()["user"]["isAdmin"])
        self.assertEqual(self.client.get("/api/personnel/").status_code, 200)

    def test_email_login_matches_existing_username_case_insensitively(self):
        response = self.client.post("/api/auth/login/", {
            "email": self.user.email.upper(), "password": "correct-password",
        })
        self.assertEqual(response.status_code, 200)

    def test_ambiguous_email_is_rejected(self):
        get_user_model().objects.create_user(
            username="another-engineer", email=self.user.email.upper(), password="other-password",
        )
        response = self.client.post("/api/auth/login/", {
            "email": self.user.email, "password": "correct-password",
        })
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_account_cannot_sign_in_by_email(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.post("/api/auth/login/", {
            "email": self.user.email, "password": "correct-password",
        })
        self.assertEqual(response.status_code, 400)

    def test_regular_viewer_is_not_granted_superuser_access(self):
        self.user.console_role.role = AppRole.VIEWER
        self.user.console_role.save(update_fields=["role"])
        self.client.force_login(self.user)
        session = self.client.get("/api/session/").json()
        self.assertFalse(session["user"]["isAdmin"])
        self.assertEqual(self.client.get("/api/personnel/").status_code, 403)

    def test_account_inspection_reports_saved_account_without_password(self):
        output = io.StringIO()
        call_command("inspect_account", self.user.email, stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["database"]["userTable"], "auth_user")
        self.assertEqual(report["accounts"][0]["id"], self.user.pk)
        self.assertTrue(report["accounts"][0]["hasUsablePassword"])
        self.assertNotIn(self.user.password, output.getvalue())
        self.assertNotIn("correct-password", output.getvalue())

    def test_account_inspection_identifies_missing_account(self):
        output = io.StringIO()
        with self.assertRaisesMessage(CommandError, "Account not found"):
            call_command("inspect_account", "missing@example.com", stdout=output)
        self.assertEqual(json.loads(output.getvalue())["accounts"], [])

    def test_health_endpoint(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_liveness_endpoint_does_not_require_authentication(self):
        response = self.client.get("/livez/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "alive"})

    @override_settings(
        ALLOWED_HOSTS=["localhost"],
        SECURE_SSL_REDIRECT=True,
        SECURE_REDIRECT_EXEMPT=[r"^livez/$"],
    )
    def test_internal_liveness_probe_bypasses_https_redirect(self):
        response = self.client.get("/livez/", HTTP_HOST="localhost:8000")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "alive"})

    def test_authenticated_console_is_owned_by_frontend_server(self):
        self.user.console_role.role = "engineer"
        self.user.console_role.save(update_fields=["role"])
        self.client.force_login(self.user)

        response = self.client.get("/app/analyzer")

        self.assertEqual(response.status_code, 404)
