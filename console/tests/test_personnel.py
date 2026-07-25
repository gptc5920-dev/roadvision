from django.contrib.auth import get_user_model
from django.test import TestCase

from console.models import AppRole, UserRole


class PersonnelAccountTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="admin@example.com",
            email="admin@example.com",
            password="Admin-secure-2026!",
        )
        UserRole.objects.update_or_create(user=self.admin, defaults={"role": AppRole.ADMIN})
        self.client.force_login(self.admin)

    def test_admin_can_create_personnel_account(self):
        response = self.client.post(
            "/_authenticated/admin/personnel/",
            {
                "action": "create_personnel_account",
                "full_name": "Maria Santos",
                "email": "Maria.Santos@example.com",
                "role": AppRole.ENGINEER,
                "temporary_password": "Road-Engineer-2026!",
                "confirm_password": "Road-Engineer-2026!",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="maria.santos@example.com")
        self.assertEqual(user.email, "maria.santos@example.com")
        self.assertEqual(user.profile.full_name, "Maria Santos")
        self.assertEqual(user.console_role.role, AppRole.ENGINEER)
        self.assertTrue(user.check_password("Road-Engineer-2026!"))

    def test_duplicate_email_is_rejected_case_insensitively(self):
        get_user_model().objects.create_user(
            username="existing@example.com",
            email="existing@example.com",
            password="Existing-secure-2026!",
        )
        response = self.client.post(
            "/_authenticated/admin/personnel/",
            {
                "action": "create_personnel_account",
                "full_name": "Duplicate User",
                "email": "EXISTING@example.com",
                "role": AppRole.VIEWER,
                "temporary_password": "Road-Viewer-2026!",
                "confirm_password": "Road-Viewer-2026!",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "already exists", status_code=400)
        self.assertEqual(get_user_model().objects.filter(email__iexact="existing@example.com").count(), 1)

    def test_non_admin_cannot_create_personnel_account(self):
        engineer = get_user_model().objects.create_user(
            username="engineer@example.com",
            email="engineer@example.com",
            password="Engineer-secure-2026!",
        )
        UserRole.objects.update_or_create(user=engineer, defaults={"role": AppRole.ENGINEER})
        self.client.force_login(engineer)
        response = self.client.post(
            "/_authenticated/admin/personnel/",
            {
                "action": "create_personnel_account",
                "full_name": "Blocked User",
                "email": "blocked@example.com",
                "role": AppRole.ENGINEER,
                "temporary_password": "Road-Blocked-2026!",
                "confirm_password": "Road-Blocked-2026!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(get_user_model().objects.filter(username="blocked@example.com").exists())
