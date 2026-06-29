import uuid

from django.conf import settings
from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.db import transaction
from django.utils import timezone

from accounts.models import AuditLog
from accounts.utils import create_audit_log

from notifications.constants import (
    NotificationChannel,
    NotificationEventMode,
    NotificationSeverity,
)
from notifications.data import RecipientSpec
from notifications.models import Notification
from notifications.services import (
    cancel_scheduled_notification,
    notify,
)
from notifications.services.providers import (
    channel_is_configured,
)

from tasks.constants import (
    TaskActivityType,
    TaskAssignmentType,
)
from tasks.models import (
    ACTIVE_TASK_STATUSES,
    Task,
    TaskReminder,
)

from .activities import create_task_activity


def get_reminder_capabilities():
    reminders_enabled = getattr(
        settings,
        "TASK_REMINDERS_ENABLED",
        True,
    )

    dashboard_enabled = getattr(
        settings,
        "TASK_DASHBOARD_REMINDERS_ENABLED",
        True,
    )

    external_scheduling_enabled = getattr(
        settings,
        "TASK_SCHEDULED_EXTERNAL_DELIVERY_ENABLED",
        False,
    )

    email_configured = channel_is_configured(NotificationChannel.EMAIL)

    whatsapp_configured = channel_is_configured(NotificationChannel.WHATSAPP)

    dashboard_available = bool(reminders_enabled and dashboard_enabled)

    email_available = bool(
        reminders_enabled and external_scheduling_enabled and email_configured
    )

    whatsapp_available = bool(
        reminders_enabled and external_scheduling_enabled and whatsapp_configured
    )

    return {
        "enabled": reminders_enabled,
        "processing_mode": getattr(
            settings,
            "NOTIFICATION_PROCESSING_MODE",
            "inline",
        ),
        "scheduled_external_delivery_enabled": external_scheduling_enabled,
        "channels": {
            NotificationChannel.DASHBOARD: {
                "available": dashboard_available,
                "reason": (
                    None
                    if dashboard_available
                    else ("Dashboard reminders are disabled for this deployment.")
                ),
            },
            NotificationChannel.EMAIL: {
                "available": email_available,
                "reason": (
                    None
                    if email_available
                    else (
                        "Scheduled email reminders require "
                        "an enabled scheduler and configured "
                        "email provider."
                    )
                ),
            },
            NotificationChannel.WHATSAPP: {
                "available": whatsapp_available,
                "reason": (
                    None
                    if whatsapp_available
                    else (
                        "Scheduled WhatsApp reminders require "
                        "an enabled scheduler and configured "
                        "WhatsApp provider."
                    )
                ),
            },
        },
        "message": (
            None
            if external_scheduling_enabled
            else (
                "Dashboard reminders are available. "
                "Scheduled email and WhatsApp reminders "
                "are unavailable on the current server "
                "configuration."
            )
        ),
    }


def _require_active_user(user):
    if not user or not user.is_authenticated or not user.is_active:
        raise PermissionDenied("An active authenticated user is required.")


def _lock_task(task):
    task_id = getattr(task, "pk", task)

    try:
        return (
            Task.objects.select_for_update()
            .select_related(
                "batch",
                "assigned_to",
                "assigned_department",
            )
            .get(pk=task_id)
        )
    except Task.DoesNotExist as exc:
        raise ValidationError({"task": ("The selected task does not exist.")}) from exc


def _lock_reminder(reminder):
    reminder_id = getattr(
        reminder,
        "pk",
        reminder,
    )

    try:
        return (
            TaskReminder.objects.select_for_update()
            .select_related(
                "task",
                "task__batch",
                "task__assigned_to",
                "user",
                "notification",
            )
            .get(pk=reminder_id)
        )
    except TaskReminder.DoesNotExist as exc:
        raise ValidationError(
            {"reminder": ("The selected reminder does not exist.")}
        ) from exc


def _validate_reminder_task(
    *,
    task,
    user,
    remind_at,
):
    if task.batch.assignment_type != TaskAssignmentType.PERSONAL:
        raise ValidationError(
            {"task": ("Reminders can only be created for personal tasks.")}
        )

    if task.assigned_to_id != user.id:
        raise PermissionDenied("Only the personal-task owner can manage its reminders.")

    if task.status not in ACTIVE_TASK_STATUSES:
        raise ValidationError(
            {"task": ("Completed or cancelled tasks cannot have active reminders.")}
        )

    if task.archived_at:
        raise ValidationError({"task": ("Archived tasks cannot have reminders.")})

    if task.batch.cancelled_at:
        raise ValidationError({"task": ("The task assignment has been cancelled.")})

    if timezone.is_naive(remind_at):
        raise ValidationError(
            {"remind_at": ("The reminder time must contain timezone information.")}
        )

    if remind_at <= timezone.now():
        raise ValidationError(
            {"remind_at": ("The reminder must be scheduled in the future.")}
        )

    if task.batch.due_at and remind_at > task.batch.due_at:
        raise ValidationError(
            {"remind_at": ("The reminder cannot be scheduled after the task deadline.")}
        )


def _normalise_channels(channels):
    if not isinstance(channels, list) or not channels:
        raise ValidationError({"channels": ("Select at least one reminder channel.")})

    capabilities = get_reminder_capabilities()

    if not capabilities["enabled"]:
        raise ValidationError({"reminders": ("Task reminders are disabled.")})

    normalised = []

    for channel in channels:
        value = str(channel).upper()

        channel_capability = capabilities["channels"].get(value)

        if channel_capability is None:
            raise ValidationError(
                {"channels": (f"Unsupported reminder channel: {channel}")}
            )

        if not channel_capability["available"]:
            raise ValidationError({"channels": (channel_capability["reason"])})

        if value not in normalised:
            normalised.append(value)

    return normalised


def _create_reminder_notification(
    *,
    reminder,
    actor,
):
    task = reminder.task
    batch = task.batch

    action_url = getattr(
        settings,
        "TASK_REMINDER_NOTIFICATION_ACTION_URL",
        "/dashboard/tasks",
    )

    result = notify(
        recipients=[
            RecipientSpec(
                user=reminder.user,
                action_url=action_url,
                action_label="View task",
                metadata={
                    "task_id": str(task.id),
                    "task_reminder_id": str(reminder.id),
                },
                template_context={
                    "task_title": batch.title,
                    "task_due_at": (batch.due_at.isoformat() if batch.due_at else ""),
                    "remind_at": (reminder.remind_at.isoformat()),
                },
            )
        ],
        notification_type="task.reminder",
        category="task",
        title="Task reminder",
        message=(f'Reminder for "{batch.title}".'),
        channels=reminder.channels,
        event_mode=NotificationEventMode.SHARED,
        severity=(
            NotificationSeverity.WARNING
            if batch.priority in {"HIGH", "URGENT"}
            else NotificationSeverity.INFO
        ),
        actor=actor,
        source=reminder,
        template_key="task.reminder",
        metadata={
            "task_id": str(task.id),
            "task_batch_id": str(task.batch_id),
            "task_reminder_id": str(reminder.id),
            "remind_at": reminder.remind_at.isoformat(),
            "channels": reminder.channels,
        },
        template_context={
            "task_title": batch.title,
            "task_description": batch.description,
            "task_priority": batch.priority,
            "task_due_at": (batch.due_at.isoformat() if batch.due_at else ""),
            "remind_at": reminder.remind_at.isoformat(),
        },
        deduplication_key=(f"task-reminder:{reminder.id}:{uuid.uuid4().hex}"),
        scheduled_at=reminder.remind_at,
        # The user explicitly selected the channels and time.
        # Do not let general preferences or quiet hours
        # silently override this reminder.
        is_mandatory=True,
    )

    notification_id = result.notification_ids[0]

    return Notification.objects.get(pk=notification_id)


@transaction.atomic
def create_task_reminder(
    *,
    task,
    user,
    remind_at,
    channels,
    request=None,
):
    _require_active_user(user)

    if not getattr(
        settings,
        "TASK_REMINDERS_ENABLED",
        True,
    ):
        raise ValidationError("Task reminders are disabled.")

    locked_task = _lock_task(task)

    _validate_reminder_task(
        task=locked_task,
        user=user,
        remind_at=remind_at,
    )

    channels = _normalise_channels(channels)

    duplicate = TaskReminder.objects.filter(
        task=locked_task,
        user=user,
        remind_at=remind_at,
        cancelled_at__isnull=True,
    ).exists()

    if duplicate:
        raise ValidationError(
            {
                "remind_at": (
                    "An active reminder already exists for this task at that time."
                )
            }
        )

    reminder = TaskReminder(
        task=locked_task,
        user=user,
        remind_at=remind_at,
        channels=channels,
    )

    reminder.full_clean()
    reminder.save()

    notification = _create_reminder_notification(
        reminder=reminder,
        actor=user,
    )

    reminder.notification = notification

    reminder.save(
        update_fields=[
            "notification",
            "updated_at",
        ]
    )

    create_task_activity(
        task=locked_task,
        actor=user,
        activity_type=(TaskActivityType.REMINDER_CREATED),
        new_value={
            "reminder_id": str(reminder.id),
            "remind_at": reminder.remind_at.isoformat(),
            "channels": reminder.channels,
            "notification_id": str(notification.id),
        },
        metadata={
            "reminder_id": str(reminder.id),
        },
    )

    create_audit_log(
        user=user,
        target_user=user,
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.CREATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task_reminder",
        action="create",
        object_id=reminder.id,
        request=request,
        metadata={
            "task_id": str(locked_task.id),
            "remind_at": reminder.remind_at.isoformat(),
            "channels": reminder.channels,
        },
    )

    return reminder


@transaction.atomic
def update_task_reminder(
    *,
    reminder,
    user,
    remind_at=None,
    channels=None,
    request=None,
):
    _require_active_user(user)

    locked_reminder = _lock_reminder(reminder)

    if locked_reminder.user_id != user.id:
        raise PermissionDenied("You cannot edit another user's reminder.")

    if locked_reminder.cancelled_at:
        raise ValidationError("A cancelled reminder cannot be edited.")

    new_remind_at = remind_at if remind_at is not None else locked_reminder.remind_at

    new_channels = (
        _normalise_channels(channels)
        if channels is not None
        else locked_reminder.channels
    )

    _validate_reminder_task(
        task=locked_reminder.task,
        user=user,
        remind_at=new_remind_at,
    )

    if (
        new_remind_at == locked_reminder.remind_at
        and new_channels == locked_reminder.channels
    ):
        raise ValidationError("The reminder is unchanged.")

    previous_value = {
        "remind_at": locked_reminder.remind_at.isoformat(),
        "channels": locked_reminder.channels,
        "notification_id": (
            str(locked_reminder.notification_id)
            if locked_reminder.notification_id
            else None
        ),
    }

    if locked_reminder.notification_id:
        cancel_scheduled_notification(
            locked_reminder.notification,
            actor=user,
            reason="The task reminder was rescheduled.",
        )

    locked_reminder.remind_at = new_remind_at
    locked_reminder.channels = new_channels
    locked_reminder.notification = None

    locked_reminder.full_clean()

    locked_reminder.save(
        update_fields=[
            "remind_at",
            "channels",
            "notification",
            "updated_at",
        ]
    )

    notification = _create_reminder_notification(
        reminder=locked_reminder,
        actor=user,
    )

    locked_reminder.notification = notification

    locked_reminder.save(
        update_fields=[
            "notification",
            "updated_at",
        ]
    )

    new_value = {
        "remind_at": locked_reminder.remind_at.isoformat(),
        "channels": locked_reminder.channels,
        "notification_id": str(notification.id),
    }

    create_task_activity(
        task=locked_reminder.task,
        actor=user,
        activity_type=(TaskActivityType.REMINDER_UPDATED),
        previous_value=previous_value,
        new_value=new_value,
        metadata={
            "reminder_id": str(locked_reminder.id),
        },
    )

    create_audit_log(
        user=user,
        target_user=user,
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.UPDATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task_reminder",
        action="update",
        object_id=locked_reminder.id,
        request=request,
        metadata={
            "task_id": str(locked_reminder.task_id),
            "previous_value": previous_value,
            "new_value": new_value,
        },
    )

    return locked_reminder


@transaction.atomic
def cancel_task_reminder(
    *,
    reminder,
    user,
    reason="Cancelled by user.",
    request=None,
):
    _require_active_user(user)

    locked_reminder = _lock_reminder(reminder)

    if locked_reminder.user_id != user.id:
        raise PermissionDenied("You cannot cancel another user's reminder.")

    if locked_reminder.cancelled_at:
        raise ValidationError("This reminder is already cancelled.")

    reason = (reason or "Cancelled by user.").strip()

    now = timezone.now()

    locked_reminder.cancelled_at = now
    locked_reminder.cancelled_by = user

    locked_reminder.save(
        update_fields=[
            "cancelled_at",
            "cancelled_by",
            "updated_at",
        ]
    )

    if locked_reminder.notification_id:
        cancel_scheduled_notification(
            locked_reminder.notification,
            actor=user,
            reason=reason,
        )

    create_task_activity(
        task=locked_reminder.task,
        actor=user,
        activity_type=(TaskActivityType.REMINDER_CANCELLED),
        previous_value={
            "cancelled_at": None,
        },
        new_value={
            "cancelled_at": now.isoformat(),
            "reason": reason,
        },
        metadata={
            "reminder_id": str(locked_reminder.id),
        },
    )

    create_audit_log(
        user=user,
        target_user=user,
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.UPDATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task_reminder",
        action="cancel",
        object_id=locked_reminder.id,
        request=request,
        metadata={
            "task_id": str(locked_reminder.task_id),
            "reason": reason,
        },
    )

    return locked_reminder


@transaction.atomic
def cancel_active_task_reminders(
    *,
    tasks,
    actor,
    reason,
    request=None,
):
    """
    Internal lifecycle helper.

    Automatically cancels reminders when tasks are completed
    or cancelled.
    """

    task_list = list(tasks) if isinstance(tasks, (list, tuple, set)) else [tasks]

    task_ids = [getattr(task, "pk", task) for task in task_list]

    reminders = list(
        TaskReminder.objects.select_for_update()
        .select_related(
            "task",
            "task__batch",
            "notification",
            "user",
        )
        .filter(
            task_id__in=task_ids,
            cancelled_at__isnull=True,
        )
    )

    now = timezone.now()

    for reminder in reminders:
        reminder.cancelled_at = now
        reminder.cancelled_by = actor

        reminder.save(
            update_fields=[
                "cancelled_at",
                "cancelled_by",
                "updated_at",
            ]
        )

        if reminder.notification_id:
            cancel_scheduled_notification(
                reminder.notification,
                actor=actor,
                reason=reason,
            )

        create_task_activity(
            task=reminder.task,
            actor=actor,
            activity_type=(TaskActivityType.REMINDER_CANCELLED),
            previous_value={
                "cancelled_at": None,
            },
            new_value={
                "cancelled_at": now.isoformat(),
                "reason": reason,
            },
            metadata={
                "reminder_id": str(reminder.id),
                "automatic": True,
            },
        )

        create_audit_log(
            user=actor,
            target_user=reminder.user,
            event_category=(AuditLog.EventCategory.CRUD),
            event_type=AuditLog.EventType.UPDATE,
            status=AuditLog.EventStatus.SUCCESS,
            app_label="tasks",
            resource="task_reminder",
            action="automatic_cancel",
            object_id=reminder.id,
            request=request,
            metadata={
                "task_id": str(reminder.task_id),
                "reason": reason,
            },
        )

    return reminders


@transaction.atomic
def cancel_reminders_after_batch_due_at(
    *,
    batch,
    actor,
    request=None,
):
    """
    Cancel reminders that became invalid because the task
    deadline was moved to an earlier time.
    """

    if batch.due_at is None:
        return []

    reminders = list(
        TaskReminder.objects.select_for_update()
        .select_related(
            "task",
            "task__batch",
            "notification",
            "user",
        )
        .filter(
            task__batch=batch,
            cancelled_at__isnull=True,
            remind_at__gt=batch.due_at,
        )
    )

    now = timezone.now()
    reason = "The reminder was cancelled because the task deadline changed."

    for reminder in reminders:
        reminder.cancelled_at = now
        reminder.cancelled_by = actor

        reminder.save(
            update_fields=[
                "cancelled_at",
                "cancelled_by",
                "updated_at",
            ]
        )

        if reminder.notification_id:
            cancel_scheduled_notification(
                reminder.notification,
                actor=actor,
                reason=reason,
            )

        create_task_activity(
            task=reminder.task,
            actor=actor,
            activity_type=(TaskActivityType.REMINDER_CANCELLED),
            new_value={
                "cancelled_at": now.isoformat(),
                "reason": reason,
            },
            metadata={
                "reminder_id": str(reminder.id),
                "automatic": True,
            },
        )

    return reminders
