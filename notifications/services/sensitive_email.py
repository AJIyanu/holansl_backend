"""Immediate, non-persistent delivery for authentication secrets.

Never use the notification outbox for these bodies: it persists rendered content.
No channel selector, preferences, quiet hours, fallback, or automatic retry applies.
"""

from dataclasses import dataclass

from django.views.decorators.debug import sensitive_variables

from notifications.constants import NotificationChannel
from notifications.data import ProviderResult
from notifications.exceptions import (
    NotificationConfigurationError,
    TemporaryDeliveryError,
)
from notifications.services.providers import channel_is_configured, get_provider


@dataclass(frozen=True, repr=False)
class SensitiveEmail:
    destination: str
    subject: str
    body_text: str
    body_html: str = ""
    title: str = ""


@sensitive_variables()
def send_sensitive_email(*, destination, subject, body_text, body_html=""):
    """Return safe acceptance metadata; never create inbox or delivery records."""
    if not channel_is_configured(NotificationChannel.EMAIL):
        raise NotificationConfigurationError("Notification email is not configured.")
    if not destination:
        raise NotificationConfigurationError("An email destination is required.")

    try:
        result = get_provider(NotificationChannel.EMAIL).send(
            SensitiveEmail(destination, subject, body_text, body_html)
        )
    except Exception:
        # Provider errors may echo request content. Do not chain or log them.
        raise TemporaryDeliveryError(
            "Sensitive email could not be sent.", code="sensitive_email_failed"
        ) from None

    # Drop provider response metadata, which may contain request content.
    return ProviderResult(
        status=result.status, provider_message_id=result.provider_message_id
    )
