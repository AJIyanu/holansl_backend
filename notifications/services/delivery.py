import logging
import socket

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from notifications.constants import (
    DeliveryStatus,
    NotificationProcessingMode,
)
from notifications.exceptions import (
    NotificationConfigurationError,
    PermanentDeliveryError,
    TemporaryDeliveryError,
)
from notifications.models import NotificationDelivery
from notifications.services.providers import get_provider


logger = logging.getLogger(__name__)


DELIVERABLE_STATUSES = {
    DeliveryStatus.PENDING,
    DeliveryStatus.RETRYING,
}


def _worker_name():
    return (
        getattr(
            settings,
            "NOTIFICATION_WORKER_NAME",
            "",
        )
        or socket.gethostname()
    )


def _backoff_seconds(attempt_count):
    base = getattr(
        settings,
        "NOTIFICATION_RETRY_BASE_SECONDS",
        60,
    )

    maximum = getattr(
        settings,
        "NOTIFICATION_RETRY_MAX_SECONDS",
        3600,
    )

    return min(
        base * (2 ** max(attempt_count - 1, 0)),
        maximum,
    )


def _claim_delivery(delivery_id):
    now = timezone.now()

    with transaction.atomic():
        delivery = (
            NotificationDelivery.objects.select_for_update()
            .select_related(
                "notification_recipient__notification",
                "notification_recipient__recipient",
            )
            .filter(pk=delivery_id)
            .first()
        )

        if delivery is None or delivery.status not in DELIVERABLE_STATUSES:
            return None

        notification = delivery.notification_recipient.notification

        if notification.expires_at and notification.expires_at <= now:
            delivery.status = DeliveryStatus.CANCELLED
            delivery.error_code = "notification_expired"
            delivery.error_message = "The notification expired before delivery."
            delivery.locked_at = None
            delivery.locked_by = ""

            delivery.save(
                update_fields=[
                    "status",
                    "error_code",
                    "error_message",
                    "locked_at",
                    "locked_by",
                    "updated_at",
                ]
            )

            return None

        if delivery.scheduled_at > now or delivery.next_attempt_at > now:
            return None

        delivery.status = DeliveryStatus.PROCESSING
        delivery.attempt_count += 1
        delivery.last_attempt_at = now
        delivery.locked_at = now
        delivery.locked_by = _worker_name()

        delivery.save(
            update_fields=[
                "status",
                "attempt_count",
                "last_attempt_at",
                "locked_at",
                "locked_by",
                "updated_at",
            ]
        )

        return delivery


def _mark_success(delivery_id, result):
    now = timezone.now()
    status = result.status

    if status not in {
        DeliveryStatus.SENT,
        DeliveryStatus.DELIVERED,
        DeliveryStatus.READ,
    }:
        status = DeliveryStatus.SENT

    updates = {
        "status": status,
        "provider_message_id": result.provider_message_id or "",
        "response_metadata": result.metadata or {},
        "error_code": "",
        "error_message": "",
        "locked_at": None,
        "locked_by": "",
        "failed_at": None,
        "updated_at": now,
    }

    if status in {
        DeliveryStatus.SENT,
        DeliveryStatus.DELIVERED,
        DeliveryStatus.READ,
    }:
        updates["sent_at"] = now

    if status in {
        DeliveryStatus.DELIVERED,
        DeliveryStatus.READ,
    }:
        updates["delivered_at"] = now

    if status == DeliveryStatus.READ:
        updates["read_at"] = now

    NotificationDelivery.objects.filter(pk=delivery_id).update(**updates)


def _mark_failure(
    delivery_id,
    exc,
    *,
    permanent=False,
):
    now = timezone.now()

    delivery = NotificationDelivery.objects.get(pk=delivery_id)

    code = getattr(
        exc,
        "code",
        "delivery_failed",
    )

    metadata = getattr(
        exc,
        "metadata",
        {},
    )

    should_retry = not permanent and delivery.attempt_count < delivery.max_attempts

    if should_retry:
        status = DeliveryStatus.RETRYING

        next_attempt_at = now + timedelta(
            seconds=_backoff_seconds(delivery.attempt_count)
        )

        failed_at = None
    else:
        status = DeliveryStatus.FAILED
        next_attempt_at = delivery.next_attempt_at
        failed_at = now

    NotificationDelivery.objects.filter(pk=delivery_id).update(
        status=status,
        next_attempt_at=next_attempt_at,
        failed_at=failed_at,
        error_code=code,
        error_message=str(exc),
        response_metadata=metadata,
        locked_at=None,
        locked_by="",
        updated_at=now,
    )


def dispatch_delivery(delivery_id):
    delivery = _claim_delivery(delivery_id)

    if delivery is None:
        return False

    try:
        provider = get_provider(delivery.channel)
        result = provider.send(delivery)

    except (
        PermanentDeliveryError,
        NotificationConfigurationError,
    ) as exc:
        logger.warning(
            "Permanent notification delivery failure: %s",
            exc,
        )

        _mark_failure(
            delivery.id,
            exc,
            permanent=True,
        )

        return False

    except TemporaryDeliveryError as exc:
        logger.warning(
            "Temporary notification delivery failure: %s",
            exc,
        )

        _mark_failure(
            delivery.id,
            exc,
            permanent=False,
        )

        return False

    except Exception as exc:
        logger.exception("Unexpected notification delivery failure")

        wrapped = TemporaryDeliveryError(
            str(exc),
            code="unexpected_provider_error",
        )

        _mark_failure(
            delivery.id,
            wrapped,
            permanent=False,
        )

        return False

    _mark_success(delivery.id, result)

    return True


def release_stale_processing_deliveries():
    now = timezone.now()

    timeout_seconds = getattr(
        settings,
        "NOTIFICATION_LOCK_TIMEOUT_SECONDS",
        900,
    )

    stale_before = now - timedelta(seconds=timeout_seconds)

    return NotificationDelivery.objects.filter(
        status=DeliveryStatus.PROCESSING,
        locked_at__lt=stale_before,
    ).update(
        status=DeliveryStatus.RETRYING,
        next_attempt_at=now,
        locked_at=None,
        locked_by="",
        error_code="stale_processing_lock",
        error_message=("A stale processing lock was released."),
        updated_at=now,
    )


def get_due_delivery_ids(*, batch_size):
    now = timezone.now()

    queryset = NotificationDelivery.objects.filter(
        status__in=DELIVERABLE_STATUSES,
        scheduled_at__lte=now,
        next_attempt_at__lte=now,
    ).filter(
        Q(notification_recipient__notification__expires_at__isnull=True)
        | Q(notification_recipient__notification__expires_at__gt=now)
    )

    return list(
        queryset.order_by(
            "next_attempt_at",
            "created_at",
        ).values_list("id", flat=True)[:batch_size]
    )


def process_due_deliveries(*, batch_size=None):
    batch_size = batch_size or getattr(
        settings,
        "NOTIFICATION_PROCESSING_BATCH_SIZE",
        100,
    )

    release_stale_processing_deliveries()

    delivery_ids = get_due_delivery_ids(batch_size=batch_size)

    processed = 0
    succeeded = 0

    for delivery_id in delivery_ids:
        processed += 1

        if dispatch_delivery(delivery_id):
            succeeded += 1

    return {
        "selected": len(delivery_ids),
        "processed": processed,
        "succeeded": succeeded,
        "failed_or_deferred": processed - succeeded,
    }


def dispatch_delivery_ids(delivery_ids):
    for delivery_id in delivery_ids:
        dispatch_delivery(delivery_id)


def _inline_callback(delivery_ids):
    try:
        dispatch_delivery_ids(delivery_ids)

        opportunistic_batch = getattr(
            settings,
            "NOTIFICATION_OPPORTUNISTIC_BATCH_SIZE",
            10,
        )

        if opportunistic_batch > 0:
            process_due_deliveries(batch_size=opportunistic_batch)

    except Exception:
        logger.exception("Inline notification processing failed")


def schedule_delivery_processing(delivery_ids):
    mode = getattr(
        settings,
        "NOTIFICATION_PROCESSING_MODE",
        NotificationProcessingMode.INLINE,
    )

    if mode in {
        NotificationProcessingMode.INLINE,
        NotificationProcessingMode.HYBRID,
    }:
        ids = [str(delivery_id) for delivery_id in delivery_ids]

        transaction.on_commit(lambda: _inline_callback(ids))


def retry_delivery(delivery):
    if delivery.status not in {
        DeliveryStatus.FAILED,
        DeliveryStatus.CANCELLED,
        DeliveryStatus.SKIPPED,
    }:
        return delivery

    delivery.status = DeliveryStatus.PENDING
    delivery.next_attempt_at = timezone.now()
    delivery.failed_at = None
    delivery.error_code = ""
    delivery.error_message = ""
    delivery.locked_at = None
    delivery.locked_by = ""

    delivery.save(
        update_fields=[
            "status",
            "next_attempt_at",
            "failed_at",
            "error_code",
            "error_message",
            "locked_at",
            "locked_by",
            "updated_at",
        ]
    )

    schedule_delivery_processing([delivery.id])

    return delivery
