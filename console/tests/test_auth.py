from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings


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
            "/auth/",
            {"mode": "signup", "full_name": "Blocked", "email": "new@example.com", "password": "password"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_user_model().objects.count(), before)

    @override_settings(LOGIN_MAX_ATTEMPTS=2, LOGIN_LOCKOUT_SECONDS=60)
    def test_repeated_bad_passwords_are_throttled(self):
        payload = {"email": self.user.email, "password": "wrong"}
        self.client.post("/auth/", payload, REMOTE_ADDR="192.0.2.1")
        self.client.post("/auth/", payload, REMOTE_ADDR="192.0.2.1")
        response = self.client.post("/auth/", payload, REMOTE_ADDR="192.0.2.1")
        self.assertContains(response, "Too many sign-in attempts")

    def test_valid_sign_in_redirects_to_analyzer(self):
        response = self.client.post(
            "/auth/",
            {"email": self.user.email, "password": "correct-password"},
        )
        self.assertRedirects(response, "/_authenticated/admin/", fetch_redirect_response=False)

    def test_sign_in_page_offers_password_visibility_toggle(self):
        response = self.client.get("/auth/")

        self.assertContains(response, 'id="sign-in-password"')
        self.assertContains(response, 'aria-controls="sign-in-password"')
        self.assertContains(response, 'aria-label="Show password"')
        self.assertContains(response, 'class="password-toggle-icon"')
        self.assertContains(response, "console/auth.js")

    def test_health_endpoint(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_authenticated_console_includes_responsive_navigation_shell(self):
        self.user.console_role.role = "engineer"
        self.user.console_role.save(update_fields=["role"])
        self.client.force_login(self.user)

        response = self.client.get("/_authenticated/admin/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "console/responsive.css")
        self.assertContains(response, 'id="app-sidebar"')
        self.assertContains(response, 'id="sidebar-backdrop"')
        self.assertContains(response, 'aria-controls="app-sidebar"')
        self.assertContains(response, 'id="main-content"')
