from notifications.exceptions import (
    NotificationConfigurationError,
)

from .base import NotificationProvider


class WhatsAppProvider(NotificationProvider):
    """
    Provider boundary for a future WhatsApp adapter.

    The channel, templates, preferences and outbox rows
    already support WhatsApp. Replace this class when
    Meta, Twilio or another provider is selected.
    """

    name = "unconfigured-whatsapp"

    def send(self, delivery):
        raise NotificationConfigurationError(
            "A WhatsApp notification provider has not been configured."
        )
