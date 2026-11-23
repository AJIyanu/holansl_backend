from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import AuthenticationSession, LoginThrottle, MFAChallenge


class EmailMFAAuthenticationTests(APITestCase):
    def setUp(self):
        self.password = "A-secure-test-password-42"
        self.user = get_user_model().objects.create_user(
            username="staff",
            email="staff@holansl.com",
            password=self.password,
        )

    @patch("accounts.auth_security._send_otp")
    @patch("accounts.auth_security.secrets.randbelow", return_value=123456)
    def test_password_then_email_code_issues_session_tokens(self, _random, send):
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": self.user.email, "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["mfa_required"])
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)
        send.assert_called_once_with(self.user, "123456")

        verified = self.client.post(
            reverse("email-mfa-verify"),
            {"challenge_id": response.data["challenge_id"], "code": "123456"},
            format="json",
        )

        self.assertEqual(verified.status_code, status.HTTP_200_OK)
        self.assertIn("access", verified.data)
        self.assertIn("refresh", verified.data)
        self.assertEqual(
            AuthenticationSession.objects.filter(user=self.user).count(), 1
        )
        self.assertIsNotNone(MFAChallenge.objects.get(user=self.user).consumed_at)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {verified.data['access']}")
        self.assertEqual(self.client.get(reverse("current-user")).status_code, 200)

    @patch("accounts.auth_security._send_otp")
    def test_username_login_is_rejected_even_for_company_account(self, send):
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": self.user.username, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        send.assert_not_called()

    @patch("accounts.auth_security._send_otp")
    def test_external_email_is_rejected_for_non_superuser(self, send):
        self.user.email = "staff@example.com"
        self.user.save(update_fields=["email"])
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": self.user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        send.assert_not_called()

    @patch("accounts.auth_security._send_otp")
    def test_named_external_superuser_exception_is_allowed(self, send):
        self.user.email = "aderemijoseph@live.com"
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["email", "is_superuser", "is_staff"])
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": self.user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["mfa_required"])

    def test_five_bad_passwords_block_account(self):
        for _ in range(5):
            self.client.post(
                reverse("token_obtain_pair"),
                {"username": self.user.email, "password": "wrong-password"},
                format="json",
            )

        self.assertTrue(LoginThrottle.objects.filter(scope="account").exists())
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": self.user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("accounts.auth_security._send_otp")
    @patch("accounts.auth_security.secrets.randbelow", return_value=123456)
    def test_code_is_single_use_and_session_can_be_revoked(self, _random, _send):
        login = self.client.post(
            reverse("token_obtain_pair"),
            {"username": self.user.email, "password": self.password},
            format="json",
        )
        payload = {"challenge_id": login.data["challenge_id"], "code": "123456"}
        verified = self.client.post(reverse("email-mfa-verify"), payload, format="json")
        self.assertEqual(verified.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.client.post(
                reverse("email-mfa-verify"), payload, format="json"
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {verified.data['access']}")
        sessions = self.client.get(reverse("authentication-session-list"))
        self.assertEqual(sessions.status_code, status.HTTP_200_OK)
        session_id = sessions.data["results"][0]["id"]
        self.assertTrue(sessions.data["results"][0]["current"])
        self.assertEqual(
            self.client.delete(
                reverse("authentication-session-detail", args=[session_id])
            ).status_code,
            status.HTTP_204_NO_CONTENT,
        )
        self.assertNotEqual(self.client.get(reverse("current-user")).status_code, 200)
