import logging

from django.conf import settings
from django.db import transaction

from notifications.constants import (
    NotificationChannel,
    NotificationEventMode,
    NotificationSeverity,
)
from notifications.data import RecipientSpec
from notifications.services import notify


logger = logging.getLogger(__name__)


def _normalise_channels():
    configured = getattr(
        settings,
        "TASK_LIFECYCLE_NOTIFICATION_CHANNELS",
        [NotificationChannel.DASHBOARD],
    )

    valid_channels = {value for value, _label in NotificationChannel.choices}

    channels = []

    for value in configured:
        channel = str(value).upper()

        if channel in valid_channels and channel not in channels:
            channels.append(channel)

    return channels or [NotificationChannel.DASHBOARD]


def _normalise_recipients(users, actor=None):
    recipients = []
    seen_user_ids = set()

    for user in users:
        if (
            user is None
            or not user.is_active
            or user.pk in seen_user_ids
            or (actor and user.pk == actor.pk)
        ):
            continue

        seen_user_ids.add(user.pk)
        recipients.append(user)

    return recipients


def _send_lifecycle_notification(
    *,
    users,
    notification_type,
    title,
    message,
    actor,
    source,
    template_key,
    metadata,
    template_context,
    deduplication_key,
    severity,
):
    recipients = _normalise_recipients(
        users,
        actor=actor,
    )

    if not recipients:
        return

    action_url = getattr(
        settings,
        "TASK_ASSIGNMENT_NOTIFICATION_ACTION_URL",
        "/dashboard/tasks",
    )

    recipient_specs = [
        RecipientSpec(
            user=user,
            action_url=action_url,
            action_label="View tasks",
        )
        for user in recipients
    ]

    try:
        notify(
            recipients=recipient_specs,
            notification_type=notification_type,
            category="task",
            title=title,
            message=message,
            channels=_normalise_channels(),
            event_mode=NotificationEventMode.SHARED,
            severity=severity,
            actor=actor,
            source=source,
            template_key=template_key,
            metadata=metadata or {},
            template_context=template_context or {},
            deduplication_key=deduplication_key,
        )
    except Exception:
        logger.exception(
            "Unable to create lifecycle notification: %s",
            notification_type,
        )


def schedule_lifecycle_notification(**kwargs):
    transaction.on_commit(lambda: _send_lifecycle_notification(**kwargs))


def schedule_batch_updated_notification(
    *,
    batch,
    tasks,
    actor,
    changed_fields,
):
    schedule_lifecycle_notification(
        users=[task.assigned_to for task in tasks if task.assigned_to_id],
        notification_type="task.updated",
        title="Task details updated",
        message=(f'Details for "{batch.title}" were updated.'),
        actor=actor,
        source=batch,
        template_key="task.updated",
        metadata={
            "task_batch_id": str(batch.id),
            "changed_fields": changed_fields,
        },
        template_context={
            "task_title": batch.title,
            "changed_fields": ", ".join(changed_fields),
            "updated_by_name": (actor.get_full_name().strip() or actor.username),
        },
        deduplication_key=(f"task-updated:{batch.id}:{batch.updated_at.isoformat()}"),
        severity=NotificationSeverity.INFO,
    )


def schedule_task_completed_notification(
    *,
    task,
    actor,
):
    creator = task.batch.created_by

    if creator is None:
        return

    schedule_lifecycle_notification(
        users=[creator],
        notification_type="task.completed",
        title="Task completed",
        message=(f'"{task.batch.title}" was completed by {task.assignee_name}.'),
        actor=actor,
        source=task,
        template_key="task.completed",
        metadata={
            "task_id": str(task.id),
            "task_batch_id": str(task.batch_id),
            "assignee_name": task.assignee_name,
        },
        template_context={
            "task_title": task.batch.title,
            "assignee_name": task.assignee_name,
        },
        deduplication_key=(f"task-completed:{task.id}"),
        severity=NotificationSeverity.SUCCESS,
    )


def schedule_task_cancelled_notification(
    *,
    task,
    actor,
):
    if task.assigned_to is None:
        return

    schedule_lifecycle_notification(
        users=[task.assigned_to],
        notification_type="task.cancelled",
        title="Task cancelled",
        message=(f'"{task.batch.title}" was cancelled.'),
        actor=actor,
        source=task,
        template_key="task.cancelled",
        metadata={
            "task_id": str(task.id),
            "task_batch_id": str(task.batch_id),
            "cancellation_reason": task.cancellation_reason,
        },
        template_context={
            "task_title": task.batch.title,
            "cancellation_reason": task.cancellation_reason,
        },
        deduplication_key=(f"task-cancelled:{task.id}"),
        severity=NotificationSeverity.WARNING,
    )


def schedule_batch_cancelled_notification(
    *,
    batch,
    affected_tasks,
    actor,
):
    schedule_lifecycle_notification(
        users=[task.assigned_to for task in affected_tasks if task.assigned_to_id],
        notification_type="task.cancelled",
        title="Task cancelled",
        message=f'"{batch.title}" was cancelled.',
        actor=actor,
        source=batch,
        template_key="task.cancelled",
        metadata={
            "task_batch_id": str(batch.id),
            "cancellation_reason": batch.cancellation_reason,
            "affected_task_count": len(affected_tasks),
        },
        template_context={
            "task_title": batch.title,
            "cancellation_reason": batch.cancellation_reason,
        },
        deduplication_key=(f"task-batch-cancelled:{batch.id}"),
        severity=NotificationSeverity.WARNING,
    )


def schedule_task_comment_notification(
    *,
    task,
    comment,
    actor,
):
    recipients = [
        task.assigned_to,
        task.batch.created_by,
    ]

    schedule_lifecycle_notification(
        users=recipients,
        notification_type="task.comment_added",
        title="New task comment",
        message=(
            f"{actor.get_full_name().strip() or actor.username} "
            f'commented on "{task.batch.title}".'
        ),
        actor=actor,
        source=task,
        template_key="task.comment_added",
        metadata={
            "task_id": str(task.id),
            "task_batch_id": str(task.batch_id),
            "comment_id": str(comment.id),
        },
        template_context={
            "task_title": task.batch.title,
            "comment_author_name": (actor.get_full_name().strip() or actor.username),
            "comment_body": comment.body,
        },
        deduplication_key=(f"task-comment-added:{comment.id}"),
        severity=NotificationSeverity.INFO,
    )
