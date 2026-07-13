"""
CRM notification integration.

The module locates authorised staff and dispatches meaningful CRM events
through the project's existing generic notification application.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from notifications.constants import (
    NotificationEventMode,
    NotificationSeverity,
)
from notifications.data import RecipientSpec
from notifications.models import Notification
from notifications.services import (
    cancel_scheduled_notification,
    notify,
)

from .models import (
    PartyBankAccount,
    PartyDocument,
)


def users_with_permission(
    permission: str,
    *,
    exclude_user_id: UUID | str | None = None,
):
    """
    Return active users who hold a Django permission or are superusers.

    Args:
        permission: Permission string in ``app_label.codename`` format.
        exclude_user_id: Optional user ID to remove from the result.

    Returns:
        QuerySet: Distinct queryset of active authorised users.

    Raises:
        ValueError: If the permission does not contain an app and codename.
    """

    try:
        app_label, codename = permission.split(
            ".",
            1,
        )
    except ValueError as exc:
        raise ValueError("Permission must use app_label.codename format.") from exc

    User = get_user_model()

    queryset = (
        User.objects.filter(
            is_active=True,
        )
        .filter(
            Q(is_superuser=True)
            | Q(
                user_permissions__content_type__app_label=(app_label),
                user_permissions__codename=codename,
            )
            | Q(
                groups__permissions__content_type__app_label=(app_label),
                groups__permissions__codename=codename,
            )
        )
        .distinct()
    )

    if exclude_user_id:
        queryset = queryset.exclude(
            pk=exclude_user_id,
        )

    return queryset


def _recipient_specs(
    users,
    *,
    action_url: str,
    action_label: str,
) -> list[RecipientSpec]:
    """
    Convert users into notification recipient specifications.

    Args:
        users: Iterable of active user instances.
        action_url: Frontend destination associated with the notification.
        action_label: Label shown for the destination action.

    Returns:
        list[RecipientSpec]: Notification recipient definitions.
    """

    return [
        RecipientSpec(
            user=user,
            action_url=action_url,
            action_label=action_label,
        )
        for user in users
    ]


def dispatch_bank_account_change(
    *,
    bank_account_id: UUID | str,
    actor_id: UUID | str | None,
    action: str,
):
    """
    Notify authorised staff that sensitive payment details changed.

    Args:
        bank_account_id: PartyBankAccount identifier.
        actor_id: User who made the change.
        action: Description such as ``created`` or ``updated``.

    Returns:
        NotificationResult | None: Notification service result, or None when
        no eligible recipient exists.
    """

    bank_account = (
        PartyBankAccount.objects.select_related(
            "party",
        )
        .filter(
            pk=bank_account_id,
        )
        .first()
    )

    if bank_account is None:
        return None

    User = get_user_model()

    actor = (
        User.objects.filter(
            pk=actor_id,
        ).first()
        if actor_id
        else None
    )

    recipients = list(
        users_with_permission(
            "crm.view_sensitive_partybankaccount",
            exclude_user_id=actor_id,
        )
    )

    if not recipients:
        return None

    action_url = f"{settings.CRM_NOTIFICATION_ACTION_URL}/{bank_account.party_id}"

    return notify(
        recipients=_recipient_specs(
            recipients,
            action_url=action_url,
            action_label="View CRM party",
        ),
        notification_type="crm.bank_account.changed",
        category="crm",
        title="CRM payment details changed",
        message=(
            f"Payment details for {bank_account.party.display_name} were {action}."
        ),
        channels=settings.CRM_NOTIFICATION_CHANNELS,
        event_mode=NotificationEventMode.SHARED,
        severity=NotificationSeverity.WARNING,
        actor=actor,
        source=bank_account,
        metadata={
            "party_id": str(
                bank_account.party_id,
            ),
            "bank_account_id": str(
                bank_account.id,
            ),
            "action": action,
            "masked_account_number": (bank_account.masked_account_number),
        },
    )


def cancel_document_expiry_notification(
    *,
    document: PartyDocument,
    actor=None,
    reason: str,
) -> None:
    """
    Cancel a previously scheduled document-expiry notification.

    Args:
        document: PartyDocument containing the old deduplication key.
        actor: Optional user responsible for the cancellation.
        reason: Human-readable cancellation reason.

    Returns:
        None.
    """

    if not document.expiry_notification_key:
        return

    notification = Notification.objects.filter(
        deduplication_key=(document.expiry_notification_key),
    ).first()

    if notification is not None:
        cancel_scheduled_notification(
            notification,
            actor=actor,
            reason=reason,
        )


def schedule_document_expiry_notification(
    *,
    document_id: UUID | str,
    actor_id: UUID | str | None = None,
):
    """
    Schedule a warning before a CRM document expires.

    Args:
        document_id: PartyDocument identifier.
        actor_id: Optional user who uploaded or changed the document.

    Returns:
        NotificationResult | None: Notification service result, or None when
        the document has no expiry or no authorised recipient exists.
    """

    document = (
        PartyDocument.objects.select_related(
            "party",
        )
        .filter(
            pk=document_id,
            is_active=True,
        )
        .first()
    )

    if document is None or document.expires_at is None:
        return None

    User = get_user_model()

    actor = (
        User.objects.filter(
            pk=actor_id,
        ).first()
        if actor_id
        else None
    )

    permission = (
        "crm.view_confidential_partydocument"
        if document.is_confidential
        else "crm.view_partydocument"
    )

    recipients = list(
        users_with_permission(
            permission,
        )
    )

    if not recipients:
        return None

    cancel_document_expiry_notification(
        document=document,
        actor=actor,
        reason="Document expiry notification was rescheduled.",
    )

    notice_days = getattr(
        settings,
        "CRM_DOCUMENT_EXPIRY_NOTICE_DAYS",
        30,
    )

    warning_date = document.expires_at - timedelta(days=notice_days)

    scheduled_at = timezone.make_aware(
        datetime.combine(
            warning_date,
            time(hour=9),
        ),
        timezone.get_current_timezone(),
    )

    if scheduled_at < timezone.now():
        scheduled_at = timezone.now()

    notification_expires_at = timezone.make_aware(
        datetime.combine(
            document.expires_at + timedelta(days=1),
            time.min,
        ),
        timezone.get_current_timezone(),
    )

    deduplication_key = (
        f"crm-document-expiry:{document.id}:{document.expires_at.isoformat()}"
    )

    action_url = f"{settings.CRM_NOTIFICATION_ACTION_URL}/{document.party_id}"

    result = notify(
        recipients=_recipient_specs(
            recipients,
            action_url=action_url,
            action_label="View document",
        ),
        notification_type="crm.document.expiring",
        category="crm",
        title="CRM document approaching expiry",
        message=(
            f"{document.original_filename} for "
            f"{document.party.display_name} expires on "
            f"{document.expires_at.isoformat()}."
        ),
        channels=settings.CRM_NOTIFICATION_CHANNELS,
        event_mode=NotificationEventMode.SHARED,
        severity=NotificationSeverity.WARNING,
        actor=actor,
        source=document,
        metadata={
            "party_id": str(
                document.party_id,
            ),
            "document_id": str(
                document.id,
            ),
            "category": document.category,
            "expires_at": (document.expires_at.isoformat()),
        },
        deduplication_key=deduplication_key,
        scheduled_at=scheduled_at,
        expires_at=notification_expires_at,
    )

    PartyDocument.objects.filter(
        pk=document.id,
    ).update(
        expiry_notification_key=(deduplication_key),
    )

    return result
