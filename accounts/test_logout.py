from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

from .models import AuditLog


class LogoutTests(APITestCase):
    def setUp(self):
        self.password = "A-secure-test-password-42"
        self.user = get_user_model().objects.create_user(
            username="logout-user",
            email="logout@example.com",
            password=self.password,
        )
        login_response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": self.user.username, "password": self.password},
            format="json",
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        self.access = login_response.data["access"]
        self.refresh = login_response.data["refresh"]

    def test_logout_revokes_access_and_refresh_and_records_audit(self):
        response = self.client.post(
            reverse("logout"),
            {"access": self.access, "refresh": self.refresh},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(BlacklistedToken.objects.count(), 2)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        protected_response = self.client.get(reverse("current-user"))
        self.assertEqual(protected_response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.credentials()
        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": self.refresh},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)

        audit = AuditLog.objects.get(event_type=AuditLog.EventType.LOGOUT)
        self.assertEqual(audit.user, self.user)
        self.assertEqual(audit.status, AuditLog.EventStatus.SUCCESS)
        self.assertNotIn(self.access, str(audit.metadata))
        self.assertNotIn(self.refresh, str(audit.metadata))

    def test_logout_rejects_mismatched_token_pair_without_revoking_it(self):
        other_user = get_user_model().objects.create_user(
            username="other-logout-user",
            email="other-logout@example.com",
            password=self.password,
        )
        other_login = self.client.post(
            reverse("token_obtain_pair"),
            {"username": other_user.username, "password": self.password},
            format="json",
        )

        response = self.client.post(
            reverse("logout"),
            {"access": self.access, "refresh": other_login.data["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(BlacklistedToken.objects.exists())
        audit = AuditLog.objects.filter(
            event_type=AuditLog.EventType.LOGOUT,
            status=AuditLog.EventStatus.FAILED,
        ).latest("created_at")
        self.assertIsNone(audit.user)
        self.assertNotIn(self.access, str(audit.metadata))
        self.assertNotIn(other_login.data["refresh"], str(audit.metadata))
