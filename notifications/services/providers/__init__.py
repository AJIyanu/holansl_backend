from django.conf import settings

from notifications.constants import NotificationChannel
from notifications.exceptions import (
    NotificationConfigurationError,
)

from .dashboard import DashboardProvider
from .resend_email import ResendEmailProvider
from .whatsapp import WhatsAppProvider


def channel_is_configured(channel):
    if channel == NotificationChannel.DASHBOARD:
        return True

    if channel == NotificationChannel.EMAIL:
        return bool(
            getattr(
                settings,
                "NOTIFICATION_EMAIL_ENABLED",
                True,
            )
            and settings.RESEND_API_KEY
            and (settings.RESEND_FROM_EMAIL or settings.EMAIL_HOST_USER)
        )

    if channel == NotificationChannel.WHATSAPP:
        return bool(
            getattr(
                settings,
                "NOTIFICATION_WHATSAPP_ENABLED",
                False,
            )
            and getattr(
                settings,
                "NOTIFICATION_WHATSAPP_PROVIDER",
                "disabled",
            )
            != "disabled"
        )

    return False


def get_provider(channel):
    if channel == NotificationChannel.DASHBOARD:
        return DashboardProvider()

    if channel == NotificationChannel.EMAIL:
        return ResendEmailProvider()

    if channel == NotificationChannel.WHATSAPP:
        return WhatsAppProvider()

    raise NotificationConfigurationError(f"Unsupported notification channel: {channel}")
