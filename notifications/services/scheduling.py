from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from notifications.constants import (
    DeliveryStatus,
    NotificationChannel,
)
from notifications.models import (
    Notification,
    NotificationDelivery,
)


@transaction.atomic
def cancel_scheduled_notification(
    notification,
    *,
    actor=None,
    reason="",
):
    """
    Expire a scheduled notification and cancel any delivery
    that has not already been externally delivered.

    Dashboard notifications are hidden by expiring the
    Notification event.
    """

    notification_id = getattr(
        notification,
        "pk",
        notification,
    )

    locked_notification = Notification.objects.select_for_update().get(
        pk=notification_id
    )

    now = timezone.now()

    metadata = {
        **(locked_notification.metadata or {}),
        "cancelled_at": now.isoformat(),
        "cancelled_by_id": (str(actor.id) if actor else None),
        "cancellation_reason": (reason or ""),
    }

    locked_notification.expires_at = now
    locked_notification.metadata = metadata

    locked_notification.save(
        update_fields=[
            "expires_at",
            "metadata",
            "updated_at",
        ]
    )

    cancellable_delivery_query = Q(
        status__in=[
            DeliveryStatus.PENDING,
            DeliveryStatus.RETRYING,
            DeliveryStatus.PROCESSING,
        ]
    ) | Q(
        channel=NotificationChannel.DASHBOARD,
        status__in=[
            DeliveryStatus.SENT,
            DeliveryStatus.DELIVERED,
            DeliveryStatus.READ,
        ],
    )

    NotificationDelivery.objects.filter(
        notification_recipient__notification=(locked_notification)
    ).filter(cancellable_delivery_query).update(
        status=DeliveryStatus.CANCELLED,
        locked_at=None,
        locked_by="",
        error_code=("scheduled_notification_cancelled"),
        error_message=(reason or "The scheduled notification was cancelled."),
        updated_at=now,
    )

    return locked_notification
