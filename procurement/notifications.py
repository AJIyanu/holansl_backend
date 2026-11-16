import logging

from django.contrib.auth import get_user_model

from notifications.constants import (
    NotificationChannel,
    NotificationEventMode,
    NotificationSeverity,
)
from notifications.data import RecipientSpec
from notifications.services import notify


logger = logging.getLogger(__name__)

RECIPIENT_DEPARTMENT_CODES = ("PROC", "MGT")


def activity_recipients():
    return (
        get_user_model()
        .objects.filter(
            is_active=True,
            is_staff=True,
            profile__department__code__in=RECIPIENT_DEPARTMENT_CODES,
        )
        .distinct()
        .order_by("pk")
    )


def send_item_activity_notification(activity_id):
    from .models import ItemActivity

    activity = (
        ItemActivity.objects.select_related("item", "created_by")
        .filter(pk=activity_id)
        .first()
    )
    if activity is None:
        return None

    users = list(activity_recipients())
    if not users:
        return None

    action_url = f"/dashboard/procurement/requesttracks/items/{activity.item_id}"
    recipients = [
        RecipientSpec(
            user=user,
            action_url=action_url,
            action_label="View item tracker",
        )
        for user in users
    ]
    try:
        return notify(
            recipients=recipients,
            notification_type="procurement.item_activity.created",
            category="procurement",
            title=f"{activity.item.name}: {activity.title}",
            message=activity.description,
            channels=(NotificationChannel.DASHBOARD, NotificationChannel.EMAIL),
            event_mode=NotificationEventMode.SHARED,
            severity=NotificationSeverity.INFO,
            actor=activity.created_by,
            source=activity,
            metadata={
                "activity_id": str(activity.id),
                "item_id": str(activity.item_id),
                "item_code": activity.item.code,
                "activity_title": activity.title,
                "occurred_at": activity.occurred_at.isoformat(),
            },
            deduplication_key=f"procurement.item_activity:{activity.id}",
        )
    except Exception:
        logger.exception(
            "Unable to notify Procurement activity recipients for activity %s.",
            activity.id,
        )
        return None
