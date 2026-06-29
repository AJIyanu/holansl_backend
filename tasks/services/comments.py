from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.db import transaction
from django.utils import timezone

from accounts.models import AuditLog
from accounts.utils import create_audit_log

from tasks.constants import TaskActivityType
from tasks.models import (
    Task,
    TaskComment,
)

from .access import (
    can_comment_on_task,
    can_edit_task_comment,
    can_remove_task_comment,
)
from .activities import create_task_activity
from .task_notifications import (
    schedule_task_comment_notification,
)


def _require_actor(actor):
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied("An active authenticated user is required.")


def _normalise_comment_body(body):
    body = (body or "").strip()

    if not body:
        raise ValidationError({"body": ("A comment cannot be empty.")})

    if len(body) > 10000:
        raise ValidationError({"body": ("A comment cannot exceed 10,000 characters.")})

    return body


def _normalise_removal_reason(reason):
    reason = (reason or "").strip()

    if not reason:
        raise ValidationError({"reason": ("A removal reason is required.")})

    if len(reason) > 2000:
        raise ValidationError(
            {"reason": ("The removal reason cannot exceed 2,000 characters.")}
        )

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
        raise ValidationError({"task": ("The selected task does not exist.")}) from exc


def _lock_comment(comment):
    comment_id = getattr(
        comment,
        "pk",
        comment,
    )

    try:
        return (
            TaskComment.objects.select_for_update()
            .select_related(
                "task",
                "task__batch",
                "task__batch__created_by",
                "task__assigned_to",
                "author",
                "removed_by",
            )
            .get(pk=comment_id)
        )
    except TaskComment.DoesNotExist as exc:
        raise ValidationError(
            {"comment": ("The selected comment does not exist.")}
        ) from exc


@transaction.atomic
def add_task_comment(
    *,
    task,
    actor,
    body,
    request=None,
):
    _require_actor(actor)

    locked_task = _lock_task(task)

    if not can_comment_on_task(
        actor,
        locked_task,
    ):
        raise PermissionDenied("You do not have permission to comment on this task.")

    if locked_task.archived_at:
        raise ValidationError("An archived task cannot receive comments.")

    body = _normalise_comment_body(body)

    comment = TaskComment(
        task=locked_task,
        author=actor,
        body=body,
    )

    comment.full_clean()
    comment.save()

    create_task_activity(
        task=locked_task,
        actor=actor,
        activity_type=(TaskActivityType.COMMENT_ADDED),
        new_value={
            "comment_id": str(comment.id),
            "author_id": str(actor.id),
        },
        metadata={
            "comment_id": str(comment.id),
        },
    )

    create_audit_log(
        user=actor,
        target_user=locked_task.assigned_to,
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.CREATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task_comment",
        action="create",
        object_id=comment.id,
        request=request,
        metadata={
            "task_id": str(locked_task.id),
            "task_batch_id": str(locked_task.batch_id),
        },
    )

    schedule_task_comment_notification(
        task=locked_task,
        comment=comment,
        actor=actor,
    )

    return comment


@transaction.atomic
def edit_task_comment(
    *,
    comment,
    actor,
    body,
    request=None,
):
    _require_actor(actor)

    locked_comment = _lock_comment(comment)

    if not can_edit_task_comment(
        actor,
        locked_comment,
    ):
        raise PermissionDenied("You do not have permission to edit this comment.")

    body = _normalise_comment_body(body)

    if body == locked_comment.body:
        raise ValidationError({"body": ("The updated comment is unchanged.")})

    previous_body = locked_comment.body

    locked_comment.body = body
    locked_comment.edited_at = timezone.now()

    locked_comment.full_clean()

    locked_comment.save(
        update_fields=[
            "body",
            "edited_at",
            "updated_at",
        ]
    )

    create_task_activity(
        task=locked_comment.task,
        actor=actor,
        activity_type=(TaskActivityType.COMMENT_EDITED),
        previous_value={
            "comment_id": str(locked_comment.id),
            "character_count": len(previous_body),
        },
        new_value={
            "comment_id": str(locked_comment.id),
            "character_count": len(locked_comment.body),
        },
        metadata={
            "comment_id": str(locked_comment.id),
            "body_changed": True,
        },
    )

    create_audit_log(
        user=actor,
        target_user=(locked_comment.task.assigned_to),
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.UPDATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task_comment",
        action="edit",
        object_id=locked_comment.id,
        request=request,
        metadata={
            "task_id": str(locked_comment.task_id),
            "task_batch_id": str(locked_comment.task.batch_id),
        },
    )

    return locked_comment


@transaction.atomic
def remove_task_comment(
    *,
    comment,
    actor,
    reason,
    request=None,
):
    _require_actor(actor)

    locked_comment = _lock_comment(comment)

    if not can_remove_task_comment(
        actor,
        locked_comment,
    ):
        raise PermissionDenied("You do not have permission to remove this comment.")

    reason = _normalise_removal_reason(reason)

    locked_comment.remove(
        removed_by=actor,
        reason=reason,
    )

    create_task_activity(
        task=locked_comment.task,
        actor=actor,
        activity_type=(TaskActivityType.COMMENT_REMOVED),
        previous_value={
            "comment_id": str(locked_comment.id),
            "removed": False,
        },
        new_value={
            "comment_id": str(locked_comment.id),
            "removed": True,
            "removal_reason": reason,
        },
        metadata={
            "comment_id": str(locked_comment.id),
            "comment_author_id": (
                str(locked_comment.author_id) if locked_comment.author_id else None
            ),
        },
    )

    create_audit_log(
        user=actor,
        target_user=(locked_comment.task.assigned_to),
        event_category=AuditLog.EventCategory.CRUD,
        event_type=AuditLog.EventType.UPDATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task_comment",
        action="remove",
        object_id=locked_comment.id,
        request=request,
        metadata={
            "task_id": str(locked_comment.task_id),
            "task_batch_id": str(locked_comment.task.batch_id),
            "comment_author_id": (
                str(locked_comment.author_id) if locked_comment.author_id else None
            ),
            "reason": reason,
        },
    )

    return locked_comment
