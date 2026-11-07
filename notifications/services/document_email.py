"""Immediate external document delivery through the shared email provider."""

import base64
from dataclasses import dataclass
from notifications.constants import NotificationChannel
from notifications.exceptions import NotificationConfigurationError
from notifications.services.providers import channel_is_configured, get_provider


@dataclass(frozen=True)
class DocumentEmail:
    destination: str
    subject: str
    body_text: str
    attachments: list
    idempotency_key: str
    body_html: str = ""
    title: str = ""


def send_document_email(
    *, destination, subject, message, filename, content, request_key
):
    if not channel_is_configured(NotificationChannel.EMAIL):
        raise NotificationConfigurationError("Email delivery is not configured.")
    return get_provider(NotificationChannel.EMAIL).send(
        DocumentEmail(
            destination,
            subject,
            message,
            [
                {
                    "filename": filename,
                    "content": base64.b64encode(content).decode("ascii"),
                }
            ],
            f"invoice-{request_key}",
        )
    )
