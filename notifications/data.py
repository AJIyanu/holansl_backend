from dataclasses import dataclass, field
from typing import Any

from .constants import DeliveryStatus


@dataclass(frozen=True)
class RecipientSpec:
    user: Any
    action_url: str = ""
    action_label: str = ""
    metadata: dict = field(default_factory=dict)
    template_context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderResult:
    status: str = DeliveryStatus.SENT
    provider_message_id: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NotificationResult:
    notification_ids: list
    recipient_ids: list
    delivery_ids: list
    created: bool = True

    def as_dict(self):
        return {
            "notification_ids": [str(value) for value in self.notification_ids],
            "recipient_ids": [str(value) for value in self.recipient_ids],
            "delivery_ids": [str(value) for value in self.delivery_ids],
            "notifications_created": len(self.notification_ids),
            "recipients_created": len(self.recipient_ids),
            "deliveries_created": len(self.delivery_ids),
            "created": self.created,
        }
