import traceback
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from notifications.exceptions import (
    NotificationConfigurationError,
    TemporaryDeliveryError,
)
from notifications.services import send_sensitive_email
from notifications.services.sensitive_email import SensitiveEmail


@override_settings(
    RESEND_API_KEY="test-key",
    RESEND_FROM_EMAIL="HolanSL <noreply@example.invalid>",
    NOTIFICATION_EMAIL_ENABLED=True,
    NOTIFICATION_PROCESSING_MODE="outbox",
)
class SensitiveEmailTests(SimpleTestCase):
    def send(self):
        return send_sensitive_email(
            destination="staff@example.invalid",
            subject="Reset",
            body_text="secret-link",
            body_html="<p>secret-link</p>",
        )

    @patch("notifications.services.providers.resend_email.resend.Emails.send")
    def test_immediate_email_without_database_preferences_or_queue(self, send):
        send.return_value = {"id": "email-id", "body": "secret-link"}
        result = self.send()
        self.assertEqual(result.provider_message_id, "email-id")
        self.assertEqual(result.metadata, {})
        send.assert_called_once()
        self.assertEqual(
            send.call_args.args[0]["from"], "HolanSL <noreply@example.invalid>"
        )
        # SimpleTestCase forbids DB access, including preferences and outbox writes.

    @patch("notifications.services.providers.resend_email.resend.Emails.send")
    def test_configuration_errors_never_fall_back(self, send):
        for config in (
            {"NOTIFICATION_EMAIL_ENABLED": False},
            {"RESEND_API_KEY": ""},
            {"RESEND_FROM_EMAIL": "", "EMAIL_HOST_USER": ""},
        ):
            with self.subTest(config=config), override_settings(**config):
                with self.assertRaises(NotificationConfigurationError):
                    self.send()
        send.assert_not_called()

    @patch(
        "notifications.services.providers.resend_email.resend.Emails.send",
        side_effect=RuntimeError("secret-link"),
    )
    def test_provider_failure_is_redacted(self, send):
        with (
            self.assertNoLogs("notifications"),
            self.assertRaises(TemporaryDeliveryError) as caught,
        ):
            self.send()
        self.assertNotIn(
            "secret-link", "".join(traceback.format_exception(caught.exception))
        )
        self.assertEqual(caught.exception.metadata, {})
        send.assert_called_once()

    def test_no_channel_override_and_no_secret_repr(self):
        with self.assertRaises(TypeError):
            send_sensitive_email(
                destination="staff@example.invalid",
                subject="Reset",
                body_text="secret-link",
                channels=["DASHBOARD"],
            )
        self.assertNotIn(
            "secret-link",
            repr(SensitiveEmail("staff@example.invalid", "Reset", "secret-link")),
        )

    @patch("notifications.services.providers.resend_email.resend.Emails.send")
    def test_missing_destination_fails_without_sending(self, send):
        with self.assertRaises(NotificationConfigurationError):
            send_sensitive_email(
                destination="", subject="Reset", body_text="secret-link"
            )
        send.assert_not_called()

    @override_settings(
        RESEND_FROM_EMAIL="",
        RESEND_FROM_NAME="HolanSL Admin",
        EMAIL_HOST_USER="noreply@example.invalid",
    )
    @patch("notifications.services.providers.resend_email.resend.Emails.send")
    def test_legacy_sender_fallback_still_uses_resend(self, send):
        send.return_value = {"id": "email-id"}
        self.send()
        self.assertEqual(
            send.call_args.args[0]["from"], "HolanSL Admin <noreply@example.invalid>"
        )
