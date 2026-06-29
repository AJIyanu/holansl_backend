from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.db import transaction
from django.utils import timezone

from accounts.models import AuditLog
from accounts.utils import create_audit_log
from tasks.constants import (
    TaskActivityType,
    TaskStatus,
)
from tasks.models import (
    ACTIVE_TASK_STATUSES,
    FINAL_TASK_STATUSES,
    Task,
    TaskBatch,
)

from .access import (
    can_archive_task,
    can_cancel_task,
    can_manage_batch,
    can_restore_task,
    can_update_task_status,
)
from .activities import (
    create_batch_activity,
    create_task_activities,
    create_task_activity,
)
from .task_notifications import (
    schedule_batch_cancelled_notification,
    schedule_batch_updated_notification,
    schedule_task_cancelled_notification,
    schedule_task_completed_notification,
)
from .reminders import (
    cancel_active_task_reminders,
    cancel_reminders_after_batch_due_at,
)

STATUS_TRANSITIONS = {
    TaskStatus.TO_DO: {
        TaskStatus.IN_PROGRESS,
        TaskStatus.BLOCKED,
        TaskStatus.COMPLETED,
    },
    TaskStatus.IN_PROGRESS: {
        TaskStatus.TO_DO,
        TaskStatus.BLOCKED,
        TaskStatus.COMPLETED,
    },
    TaskStatus.BLOCKED: {
        TaskStatus.TO_DO,
        TaskStatus.IN_PROGRESS,
        TaskStatus.COMPLETED,
    },
    TaskStatus.COMPLETED: set(),
    TaskStatus.CANCELLED: set(),
}


def _require_actor(actor):
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied("An active authenticated user is required.")


def _clean_reason(reason):
    reason = (reason or "").strip()

    if not reason:
        raise ValidationError({"reason": ("A cancellation reason is required.")})

    return reason


def _lock_task(task):
    task_id = getattr(task, "pk", task)

    try:
        return (
            Task.objects.select_for_update()
            .select_related(
                "batch",
                "batch__created_by",
                "assigned_to",
                "assigned_department",
            )
            .get(pk=task_id)
        )
    except Task.DoesNotExist as exc:
        raise ValidationError({"task": "The selected task does not exist."}) from exc


def _lock_batch(batch):
    batch_id = getattr(batch, "pk", batch)

    try:
        return (
            TaskBatch.objects.select_for_update()
            .select_related(
                "created_by",
                "source_department",
            )
            .get(pk=batch_id)
        )
    except TaskBatch.DoesNotExist as exc:
        raise ValidationError(
            {"batch": ("The selected task batch does not exist.")}
        ) from exc


def _task_snapshot(task):
    return {
        "status": task.status,
        "completed_at": (task.completed_at.isoformat() if task.completed_at else None),
        "cancelled_at": (task.cancelled_at.isoformat() if task.cancelled_at else None),
        "cancellation_reason": task.cancellation_reason,
        "archived_at": (task.archived_at.isoformat() if task.archived_at else None),
    }


def _batch_snapshot(batch):
    return {
        "title": batch.title,
        "description": batch.description,
        "priority": batch.priority,
        "start_at": (batch.start_at.isoformat() if batch.start_at else None),
        "due_at": (batch.due_at.isoformat() if batch.due_at else None),
        "cancelled_at": (
            batch.cancelled_at.isoformat() if batch.cancelled_at else None
        ),
        "cancellation_reason": batch.cancellation_reason,
        "archived_at": (batch.archived_at.isoformat() if batch.archived_at else None),
    }


@transaction.atomic
def update_task_batch(
    *,
    batch,
    actor,
    changes,
    request=None,
):
    _require_actor(actor)

    locked_batch = _lock_batch(batch)

    if not can_manage_batch(actor, locked_batch):
        raise PermissionDenied(
            "You do not have permission to edit this task assignment."
        )

    if locked_batch.cancelled_at:
        raise ValidationError("A cancelled task assignment cannot be edited.")

    if locked_batch.archived_at:
        raise ValidationError("An archived task assignment cannot be edited.")

    allowed_fields = {
        "title",
        "description",
        "priority",
        "start_at",
        "due_at",
    }

    unknown_fields = set(changes) - allowed_fields

    if unknown_fields:
        raise ValidationError(
            {
                "fields": (
                    f"Unsupported task fields: {', '.join(sorted(unknown_fields))}."
                )
            }
        )

    previous_value = _batch_snapshot(locked_batch)

    changed_fields = []

    for field, value in changes.items():
        if field == "title":
            value = (value or "").strip()

            if not value:
                raise ValidationError({"title": ("A task title is required.")})

        if field == "description":
            value = (value or "").strip()

        if getattr(locked_batch, field) != value:
            setattr(locked_batch, field, value)
            changed_fields.append(field)

    if not changed_fields:
        return locked_batch

    locked_batch.full_clean()

    locked_batch.save(
        update_fields=[
            *changed_fields,
            "updated_at",
        ]
    )

    if "due_at" in changed_fields:
        cancel_reminders_after_batch_due_at(
            batch=locked_batch,
            actor=actor,
            request=request,
        )

    tasks = list(
        locked_batch.tasks.select_related(
            "assigned_to",
            "batch",
        )
    )

    new_value = _batch_snapshot(locked_batch)

    create_batch_activity(
        batch=locked_batch,
        actor=actor,
        activity_type=(TaskActivityType.BATCH_UPDATED),
        previous_value=previous_value,
        new_value=new_value,
        metadata={
            "changed_fields": changed_fields,
        },
    )

    create_task_activities(
        tasks=tasks,
        actor=actor,
        activity_type=(TaskActivityType.TASK_DETAILS_UPDATED),
        previous_value=previous_value,
        new_value=new_value,
        metadata={
            "changed_fields": changed_fields,
        },
    )

    create_audit_log(
        user=actor,
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.UPDATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task_batch",
        action="update",
        object_id=locked_batch.id,
        request=request,
        metadata={
            "changed_fields": changed_fields,
            "previous_value": previous_value,
            "new_value": new_value,
        },
    )

    schedule_batch_updated_notification(
        batch=locked_batch,
        tasks=tasks,
        actor=actor,
        changed_fields=changed_fields,
    )

    return locked_batch


@transaction.atomic
def transition_task_status(
    *,
    task,
    actor,
    new_status,
    request=None,
):
    _require_actor(actor)

    locked_task = _lock_task(task)

    if not can_update_task_status(
        actor,
        locked_task,
    ):
        raise PermissionDenied(
            "You do not have permission to update this task's status."
        )

    if locked_task.archived_at:
        raise ValidationError("An archived task cannot be updated.")

    if locked_task.batch.cancelled_at:
        raise ValidationError("This task assignment has been cancelled.")

    valid_statuses = {value for value, _label in TaskStatus.choices}

    if new_status not in valid_statuses:
        raise ValidationError({"status": "Unsupported task status."})

    if new_status == TaskStatus.CANCELLED:
        raise ValidationError(
            {"status": ("Use the task cancellation endpoint to cancel a task.")}
        )

    if new_status == locked_task.status:
        raise ValidationError({"status": ("The task already has this status.")})

    allowed_statuses = STATUS_TRANSITIONS.get(
        locked_task.status,
        set(),
    )

    if new_status not in allowed_statuses:
        raise ValidationError(
            {
                "status": (
                    f"Task status cannot change from "
                    f"{locked_task.status} to {new_status}."
                )
            }
        )

    previous_value = _task_snapshot(locked_task)

    locked_task.status = new_status

    if new_status == TaskStatus.COMPLETED:
        locked_task.completed_at = timezone.now()
    else:
        locked_task.completed_at = None

    locked_task.full_clean()

    locked_task.save(
        update_fields=[
            "status",
            "completed_at",
            "updated_at",
        ]
    )

    new_value = _task_snapshot(locked_task)

    create_task_activity(
        task=locked_task,
        actor=actor,
        activity_type=(TaskActivityType.STATUS_CHANGED),
        previous_value=previous_value,
        new_value=new_value,
    )

    create_audit_log(
        user=actor,
        target_user=locked_task.assigned_to,
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.UPDATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task",
        action="status_change",
        object_id=locked_task.id,
        request=request,
        metadata={
            "previous_status": previous_value["status"],
            "new_status": locked_task.status,
            "task_batch_id": str(locked_task.batch_id),
        },
    )

    if new_status == TaskStatus.COMPLETED:
        cancel_active_task_reminders(
            tasks=[locked_task],
            actor=actor,
            reason=("The reminder was cancelled because the task was completed."),
            request=request,
        )

        schedule_task_completed_notification(
            task=locked_task,
            actor=actor,
        )

    return locked_task


@transaction.atomic
def cancel_task(
    *,
    task,
    actor,
    reason,
    request=None,
):
    _require_actor(actor)

    reason = _clean_reason(reason)
    locked_task = _lock_task(task)

    if not can_cancel_task(actor, locked_task):
        raise PermissionDenied("You do not have permission to cancel this task.")

    if locked_task.archived_at:
        raise ValidationError("An archived task cannot be cancelled.")

    if locked_task.status in FINAL_TASK_STATUSES:
        raise ValidationError("A completed or cancelled task cannot be cancelled.")

    previous_value = _task_snapshot(locked_task)

    now = timezone.now()

    locked_task.status = TaskStatus.CANCELLED
    locked_task.completed_at = None
    locked_task.cancelled_at = now
    locked_task.cancelled_by = actor
    locked_task.cancellation_reason = reason

    locked_task.full_clean()

    locked_task.save(
        update_fields=[
            "status",
            "completed_at",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
            "updated_at",
        ]
    )

    new_value = _task_snapshot(locked_task)

    create_task_activity(
        task=locked_task,
        actor=actor,
        activity_type=(TaskActivityType.TASK_CANCELLED),
        previous_value=previous_value,
        new_value=new_value,
    )

    create_audit_log(
        user=actor,
        target_user=locked_task.assigned_to,
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.UPDATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task",
        action="cancel",
        object_id=locked_task.id,
        request=request,
        metadata={
            "reason": reason,
            "task_batch_id": str(locked_task.batch_id),
        },
    )

    cancel_active_task_reminders(
        tasks=[locked_task],
        actor=actor,
        reason=("The reminder was cancelled because the task was cancelled."),
        request=request,
    )

    schedule_task_cancelled_notification(
        task=locked_task,
        actor=actor,
    )

    return locked_task


@transaction.atomic
def cancel_task_batch(
    *,
    batch,
    actor,
    reason,
    request=None,
):
    _require_actor(actor)

    reason = _clean_reason(reason)
    locked_batch = _lock_batch(batch)

    if not can_manage_batch(actor, locked_batch):
        raise PermissionDenied(
            "You do not have permission to cancel this task assignment."
        )

    if locked_batch.archived_at:
        raise ValidationError("An archived task assignment cannot be cancelled.")

    if locked_batch.cancelled_at:
        raise ValidationError("This task assignment is already cancelled.")

    tasks = list(
        locked_batch.tasks.select_for_update().select_related(
            "assigned_to",
            "batch",
        )
    )

    affected_tasks = [
        task
        for task in tasks
        if task.status in ACTIVE_TASK_STATUSES and task.archived_at is None
    ]

    if not affected_tasks:
        raise ValidationError("This task assignment has no active tasks to cancel.")

    previous_value = _batch_snapshot(locked_batch)

    now = timezone.now()

    locked_batch.cancelled_at = now
    locked_batch.cancelled_by = actor
    locked_batch.cancellation_reason = reason

    locked_batch.full_clean()

    locked_batch.save(
        update_fields=[
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
            "updated_at",
        ]
    )

    for task in affected_tasks:
        task.status = TaskStatus.CANCELLED
        task.completed_at = None
        task.cancelled_at = now
        task.cancelled_by = actor
        task.cancellation_reason = reason
        task.updated_at = now
        task.full_clean()

    Task.objects.bulk_update(
        affected_tasks,
        [
            "status",
            "completed_at",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
            "updated_at",
        ],
    )

    new_value = _batch_snapshot(locked_batch)

    create_batch_activity(
        batch=locked_batch,
        actor=actor,
        activity_type=(TaskActivityType.BATCH_CANCELLED),
        previous_value=previous_value,
        new_value=new_value,
        metadata={
            "affected_task_count": len(affected_tasks),
        },
    )

    create_task_activities(
        tasks=affected_tasks,
        actor=actor,
        activity_type=(TaskActivityType.TASK_CANCELLED),
        previous_value={
            "status": "ACTIVE",
        },
        new_value={
            "status": TaskStatus.CANCELLED,
            "cancelled_at": now.isoformat(),
            "cancellation_reason": reason,
        },
        metadata={
            "cancelled_from_batch": True,
        },
    )

    create_audit_log(
        user=actor,
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.UPDATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task_batch",
        action="cancel",
        object_id=locked_batch.id,
        request=request,
        metadata={
            "reason": reason,
            "affected_task_count": len(affected_tasks),
        },
    )

    cancel_active_task_reminders(
        tasks=affected_tasks,
        actor=actor,
        reason=(
            "The reminder was cancelled because the task assignment was cancelled."
        ),
        request=request,
    )

    schedule_batch_cancelled_notification(
        batch=locked_batch,
        affected_tasks=affected_tasks,
        actor=actor,
    )

    return locked_batch, affected_tasks


@transaction.atomic
def archive_task(
    *,
    task,
    actor,
    request=None,
):
    _require_actor(actor)

    locked_task = _lock_task(task)

    if not can_archive_task(actor, locked_task):
        raise PermissionDenied("You do not have permission to archive this task.")

    if locked_task.archived_at:
        raise ValidationError("This task is already archived.")

    if locked_task.status not in FINAL_TASK_STATUSES:
        raise ValidationError("Only completed or cancelled tasks can be archived.")

    previous_value = _task_snapshot(locked_task)

    locked_task.archived_at = timezone.now()
    locked_task.archived_by = actor

    locked_task.full_clean()

    locked_task.save(
        update_fields=[
            "archived_at",
            "archived_by",
            "updated_at",
        ]
    )

    create_task_activity(
        task=locked_task,
        actor=actor,
        activity_type=(TaskActivityType.TASK_ARCHIVED),
        previous_value=previous_value,
        new_value=_task_snapshot(locked_task),
    )

    create_audit_log(
        user=actor,
        target_user=locked_task.assigned_to,
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.UPDATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task",
        action="archive",
        object_id=locked_task.id,
        request=request,
    )

    return locked_task


@transaction.atomic
def restore_task(
    *,
    task,
    actor,
    request=None,
):
    _require_actor(actor)

    locked_task = _lock_task(task)

    if not can_restore_task(actor, locked_task):
        raise PermissionDenied("You do not have permission to restore this task.")

    if not locked_task.archived_at:
        raise ValidationError("This task is not archived.")

    if locked_task.batch.archived_at:
        raise ValidationError(
            "Restore the task batch before restoring an individual task."
        )

    previous_value = _task_snapshot(locked_task)

    locked_task.archived_at = None
    locked_task.archived_by = None

    locked_task.full_clean()

    locked_task.save(
        update_fields=[
            "archived_at",
            "archived_by",
            "updated_at",
        ]
    )

    create_task_activity(
        task=locked_task,
        actor=actor,
        activity_type=(TaskActivityType.TASK_RESTORED),
        previous_value=previous_value,
        new_value=_task_snapshot(locked_task),
    )

    create_audit_log(
        user=actor,
        target_user=locked_task.assigned_to,
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.UPDATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task",
        action="restore",
        object_id=locked_task.id,
        request=request,
    )

    return locked_task


@transaction.atomic
def archive_task_batch(
    *,
    batch,
    actor,
    request=None,
):
    _require_actor(actor)

    locked_batch = _lock_batch(batch)

    if not can_manage_batch(actor, locked_batch):
        raise PermissionDenied(
            "You do not have permission to archive this task assignment."
        )

    if locked_batch.archived_at:
        raise ValidationError("This task assignment is already archived.")

    tasks = list(
        locked_batch.tasks.select_for_update().select_related(
            "assigned_to",
            "batch",
        )
    )

    active_tasks = [task for task in tasks if task.status not in FINAL_TASK_STATUSES]

    if active_tasks:
        raise ValidationError(
            {
                "tasks": (
                    "All tasks must be completed or cancelled "
                    "before the assignment can be archived."
                )
            }
        )

    archive_time = timezone.now()

    locked_batch.archived_at = archive_time
    locked_batch.archived_by = actor

    locked_batch.save(
        update_fields=[
            "archived_at",
            "archived_by",
            "updated_at",
        ]
    )

    newly_archived_tasks = []

    for task in tasks:
        if task.archived_at is None:
            task.archived_at = archive_time
            task.archived_by = actor
            task.updated_at = archive_time
            task.full_clean()
            newly_archived_tasks.append(task)

    if newly_archived_tasks:
        Task.objects.bulk_update(
            newly_archived_tasks,
            [
                "archived_at",
                "archived_by",
                "updated_at",
            ],
        )

    create_batch_activity(
        batch=locked_batch,
        actor=actor,
        activity_type=(TaskActivityType.BATCH_ARCHIVED),
        new_value={
            "archived_at": archive_time.isoformat(),
            "archived_task_count": len(newly_archived_tasks),
        },
    )

    create_task_activities(
        tasks=newly_archived_tasks,
        actor=actor,
        activity_type=(TaskActivityType.TASK_ARCHIVED),
        new_value={
            "archived_at": archive_time.isoformat(),
            "archived_from_batch": True,
        },
    )

    create_audit_log(
        user=actor,
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.UPDATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task_batch",
        action="archive",
        object_id=locked_batch.id,
        request=request,
        metadata={
            "archived_task_count": len(newly_archived_tasks),
        },
    )

    return locked_batch, newly_archived_tasks


@transaction.atomic
def restore_task_batch(
    *,
    batch,
    actor,
    request=None,
):
    _require_actor(actor)

    locked_batch = _lock_batch(batch)

    if not can_manage_batch(actor, locked_batch):
        raise PermissionDenied(
            "You do not have permission to restore this task assignment."
        )

    if not locked_batch.archived_at:
        raise ValidationError("This task assignment is not archived.")

    original_archive_time = locked_batch.archived_at

    tasks_to_restore = list(
        locked_batch.tasks.select_for_update()
        .select_related(
            "assigned_to",
            "batch",
        )
        .filter(archived_at=original_archive_time)
    )

    locked_batch.archived_at = None
    locked_batch.archived_by = None

    locked_batch.save(
        update_fields=[
            "archived_at",
            "archived_by",
            "updated_at",
        ]
    )

    now = timezone.now()

    for task in tasks_to_restore:
        task.archived_at = None
        task.archived_by = None
        task.updated_at = now
        task.full_clean()

    if tasks_to_restore:
        Task.objects.bulk_update(
            tasks_to_restore,
            [
                "archived_at",
                "archived_by",
                "updated_at",
            ],
        )

    create_batch_activity(
        batch=locked_batch,
        actor=actor,
        activity_type=(TaskActivityType.BATCH_RESTORED),
        previous_value={
            "archived_at": original_archive_time.isoformat(),
        },
        new_value={
            "archived_at": None,
            "restored_task_count": len(tasks_to_restore),
        },
    )

    create_task_activities(
        tasks=tasks_to_restore,
        actor=actor,
        activity_type=(TaskActivityType.TASK_RESTORED),
        previous_value={
            "archived_at": original_archive_time.isoformat(),
        },
        new_value={
            "archived_at": None,
            "restored_from_batch": True,
        },
    )

    create_audit_log(
        user=actor,
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.UPDATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task_batch",
        action="restore",
        object_id=locked_batch.id,
        request=request,
        metadata={
            "restored_task_count": len(tasks_to_restore),
        },
    )

    return locked_batch, tasks_to_restore
