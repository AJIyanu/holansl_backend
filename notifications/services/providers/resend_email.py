import resend

from django.conf import settings

from notifications.constants import DeliveryStatus
from notifications.data import ProviderResult
from notifications.exceptions import (
    NotificationConfigurationError,
    TemporaryDeliveryError,
)

from .base import NotificationProvider


def get_resend_sender():
    if settings.RESEND_FROM_EMAIL:
        return settings.RESEND_FROM_EMAIL

    if settings.EMAIL_HOST_USER:
        return f"{settings.RESEND_FROM_NAME} <{settings.EMAIL_HOST_USER}>"

    raise NotificationConfigurationError(
        "RESEND_FROM_EMAIL or EMAIL_HOST_USER must be configured."
    )


class ResendEmailProvider(NotificationProvider):
    name = "resend"

    def send(self, delivery):
        if not settings.RESEND_API_KEY:
            raise NotificationConfigurationError("RESEND_API_KEY is not configured.")

        if not delivery.destination:
            raise NotificationConfigurationError(
                "The email delivery has no destination."
            )

        resend.api_key = settings.RESEND_API_KEY

        payload = {
            "from": get_resend_sender(),
            "to": [delivery.destination],
            "subject": (delivery.subject or delivery.title),
            "text": delivery.body_text,
        }

        if delivery.body_html:
            payload["html"] = delivery.body_html

        try:
            response = resend.Emails.send(payload)
        except Exception as exc:
            raise TemporaryDeliveryError(
                str(exc),
                code="resend_send_failed",
            ) from exc

        if isinstance(response, dict):
            message_id = response.get("id", "")
            metadata = response
        else:
            message_id = getattr(
                response,
                "id",
                "",
            )
            metadata = {"response": str(response)}

        return ProviderResult(
            status=DeliveryStatus.SENT,
            provider_message_id=message_id,
            metadata=metadata,
        )
