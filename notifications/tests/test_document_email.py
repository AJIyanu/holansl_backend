import base64
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from notifications.services.document_email import send_document_email
from notifications.exceptions import NotificationConfigurationError


@override_settings(
    RESEND_API_KEY="test-key",
    RESEND_FROM_EMAIL="HolanSL <invoice@example.invalid>",
    NOTIFICATION_EMAIL_ENABLED=True,
)
class DocumentEmailTests(SimpleTestCase):
    @patch(
        "notifications.services.providers.resend_email.resend.Emails.send",
        return_value={"id": "document-email-id"},
    )
    def test_attachment_and_request_key_use_shared_provider(self, send):
        result = send_document_email(
            destination="client@example.invalid",
            subject="Invoice HOL-ABC-261105-001",
            message="Please review the attached invoice.",
            filename="invoice.pdf",
            content=b"%PDF-test",
            request_key="test-key-1",
        )
        self.assertEqual(result.provider_message_id, "document-email-id")
        payload, options = send.call_args.args
        self.assertEqual(options, {"idempotency_key": "invoice-test-key-1"})
        self.assertEqual(payload["to"], ["client@example.invalid"])
        self.assertEqual(
            base64.b64decode(payload["attachments"][0]["content"]), b"%PDF-test"
        )

    @override_settings(NOTIFICATION_EMAIL_ENABLED=False)
    @patch("notifications.services.providers.resend_email.resend.Emails.send")
    def test_unconfigured_email_does_not_call_provider(self, send):
        with self.assertRaises(NotificationConfigurationError):
            send_document_email(
                destination="client@example.invalid",
                subject="Invoice",
                message="Message",
                filename="invoice.pdf",
                content=b"%PDF-test",
                request_key="test-key-1",
            )
        send.assert_not_called()
