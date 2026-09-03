from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import AuditLog, PasswordResetCode
from accounts.utils import create_password_reset_code, get_valid_password_reset_code
from notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationRecipient,
)


@override_settings(
    RESEND_API_KEY="test-key",
    RESEND_FROM_EMAIL="HolanSL <noreply@example.invalid>",
    NOTIFICATION_EMAIL_ENABLED=True,
    FRONTEND_URL="https://admin.example.invalid",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class PasswordEmailTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="reset-staff",
            email="staff@example.invalid",
            password="Initial-password-987!",
            first_name="<script>bad</script>",
        )
        self.send = patch(
            "notifications.services.providers.resend_email.resend.Emails.send",
            return_value={"id": "email-id"},
        ).start()
        self.addCleanup(patch.stopall)

    def assert_private_delivery(self, response):
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(NotificationRecipient.objects.exists())
        self.assertFalse(NotificationDelivery.objects.exists())
        payload = self.send.call_args.args[0]
        self.assertEqual(payload["to"], [self.user.email])
        link = next(
            line for line in payload["text"].splitlines() if line.startswith("https://")
        )
        token = parse_qs(urlsplit(link).query)["code"][0]
        self.assertNotIn(token, str(response.data))
        self.assertNotIn(token, str(list(AuditLog.objects.values())))
        self.assertNotIn(token, str(list(PasswordResetCode.objects.values())))
        self.assertNotIn("<script>bad</script>", payload["html"])
        self.assertIsNotNone(get_valid_password_reset_code(token))
        return token

    def test_reset_request_verify_confirm_and_single_use(self):
        response = self.client.post(
            "/account/password-reset/request/", {"email": self.user.email}
        )
        self.assertEqual(response.status_code, 200)
        token = self.assert_private_delivery(response)
        self.assertEqual(
            self.client.post(
                "/account/password-reset/verify/", {"code": token}
            ).status_code,
            200,
        )
        data = {
            "code": token,
            "password": "Replacement-strong-847!",
            "password_confirm": "Replacement-strong-847!",
        }
        self.assertEqual(
            self.client.post("/account/password-reset/confirm/", data).status_code, 200
        )
        self.assertEqual(
            self.client.post("/account/password-reset/confirm/", data).status_code, 400
        )

    def test_default_password_login_sends_email_without_tokens(self):
        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])
        response = self.client.post(
            "/token/",
            {"username": self.user.username, "password": "Initial-password-987!"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(str(response.data["code"][0]), "password_change_required")
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)
        self.assert_private_delivery(response)
        self.assertEqual(
            self.send.call_args.args[0]["subject"], "Set your HolanSL Admin password"
        )

    def test_provider_failure_invalidates_token_in_both_flows(self):
        for onboarding in (False, True):
            with self.subTest(onboarding=onboarding):
                self.user.must_change_password = onboarding
                self.user.save(update_fields=["must_change_password"])
                self.send.side_effect = RuntimeError("secret-provider-body")
                path = "/token/" if onboarding else "/account/password-reset/request/"
                data = (
                    {
                        "username": self.user.username,
                        "password": "Initial-password-987!",
                    }
                    if onboarding
                    else {"email": self.user.email}
                )
                with (
                    self.assertNoLogs("accounts.utils"),
                    self.assertNoLogs("notifications.services"),
                ):
                    response = self.client.post(path, data)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(PasswordResetCode.objects.exists())
                self.assertNotIn("secret-provider-body", str(response.data))
                self.assertNotIn(
                    "secret-provider-body", str(list(AuditLog.objects.values()))
                )
                self.assertFalse(NotificationDelivery.objects.exists())

    def test_unknown_and_inactive_accounts_do_not_send(self):
        unknown = self.client.post(
            "/account/password-reset/request/", {"email": "unknown@example.invalid"}
        )
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        inactive = self.client.post(
            "/account/password-reset/request/", {"email": self.user.email}
        )
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(unknown.data, inactive.data)
        self.send.assert_not_called()

    def test_expired_and_replaced_tokens_are_invalid(self):
        old, raw_old = create_password_reset_code(self.user)
        current, raw_current = create_password_reset_code(self.user)
        self.assertIsNone(get_valid_password_reset_code(raw_old))
        current.expires_at = timezone.now() - timedelta(seconds=1)
        current.save(update_fields=["expires_at"])
        self.assertIsNone(get_valid_password_reset_code(raw_current))
