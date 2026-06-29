import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.db import transaction
from django.utils import timezone

from accounts.models import (
    AuditLog,
    Department,
)
from accounts.utils import create_audit_log
from notifications.constants import (
    NotificationChannel,
    NotificationEventMode,
)
from notifications.data import RecipientSpec
from notifications.services import notify
from tasks.constants import (
    TaskActivityType,
    TaskAssignmentType,
    TaskPriority,
)
from tasks.models import Task, TaskBatch

from .access import (
    active_staff_profiles,
    can_assign_across_organisation,
    can_assign_to_department,
    can_assign_to_user,
)
from .activities import (
    create_batch_activity,
    create_task_activities,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskAssignmentResult:
    batch: TaskBatch
    tasks: list[Task]
    notification_scheduled: bool

    @property
    def recipient_count(self):
        return len(self.tasks)

    @property
    def recipient_user_ids(self):
        return [task.assigned_to_id for task in self.tasks if task.assigned_to_id]


def _normalise_title(title):
    title = (title or "").strip()

    if not title:
        raise ValidationError({"title": "A task title is required."})

    if len(title) > 255:
        raise ValidationError(
            {"title": ("The task title cannot exceed 255 characters.")}
        )

    return title


def _validate_assignment_type(
    assignment_type,
):
    valid_values = {value for value, _label in TaskAssignmentType.choices}

    if assignment_type not in valid_values:
        raise ValidationError({"assignment_type": ("Unsupported assignment type.")})


def _validate_priority(priority):
    valid_values = {value for value, _label in TaskPriority.choices}

    if priority not in valid_values:
        raise ValidationError({"priority": ("Unsupported task priority.")})


def _validate_dates(start_at, due_at):
    if start_at and timezone.is_naive(start_at):
        raise ValidationError(
            {"start_at": ("The task start time must include timezone information.")}
        )

    if due_at and timezone.is_naive(due_at):
        raise ValidationError(
            {"due_at": ("The task due time must include timezone information.")}
        )

    if start_at and due_at and due_at < start_at:
        raise ValidationError(
            {"due_at": ("The due time cannot be before the start time.")}
        )


def _active_creator_profile(creator):
    if not creator or not creator.is_authenticated or not creator.is_active:
        raise PermissionDenied("An active authenticated staff account is required.")

    profile = active_staff_profiles().filter(user=creator).first()

    if profile is None:
        raise PermissionDenied("An active staff profile is required to create tasks.")

    return profile


def _normalise_user_ids(user_ids):
    if user_ids is None:
        return []

    normalised = []
    seen = set()

    for user_id in user_ids:
        value = str(getattr(user_id, "pk", user_id))

        if value in seen:
            continue

        seen.add(value)
        normalised.append(value)

    return normalised


def _resolve_selected_user_profiles(
    *,
    creator,
    user_ids,
):
    normalised_ids = _normalise_user_ids(user_ids)

    if not normalised_ids:
        raise ValidationError({"user_ids": ("Select at least one staff member.")})

    profiles = list(
        active_staff_profiles()
        .filter(user_id__in=normalised_ids)
        .order_by(
            "user__first_name",
            "user__last_name",
        )
    )

    found_ids = {str(profile.user_id) for profile in profiles}

    missing_ids = [user_id for user_id in normalised_ids if user_id not in found_ids]

    if missing_ids:
        raise ValidationError(
            {
                "user_ids": {
                    "detail": (
                        "One or more selected users are "
                        "inactive, unavailable or do not "
                        "have an active staff profile."
                    ),
                    "invalid_ids": missing_ids,
                }
            }
        )

    unauthorised_ids = [
        str(profile.user_id)
        for profile in profiles
        if not can_assign_to_user(
            creator,
            profile.user,
        )
    ]

    if unauthorised_ids:
        raise PermissionDenied(
            "You cannot assign tasks to one or more selected staff members."
        )

    return profiles


def _resolve_department_profiles(
    *,
    creator,
    department,
    include_assigner,
):
    if department is None:
        raise ValidationError(
            {"department": ("A department is required for a department assignment.")}
        )

    if not isinstance(department, Department):
        try:
            department = Department.objects.get(pk=department)
        except Department.DoesNotExist as exc:
            raise ValidationError(
                {"department": ("The selected department does not exist.")}
            ) from exc

    if not can_assign_to_department(
        creator,
        department,
    ):
        raise PermissionDenied(
            "You do not have permission to assign tasks to this department."
        )

    profiles = list(
        active_staff_profiles()
        .filter(department=department)
        .order_by(
            "user__first_name",
            "user__last_name",
        )
    )

    if not include_assigner:
        profiles = [profile for profile in profiles if profile.user_id != creator.id]

    if not profiles:
        raise ValidationError(
            {"department": ("No active staff members were found for this department.")}
        )

    return department, profiles


def resolve_assignment_recipients(
    *,
    creator,
    assignment_type,
    user_ids=None,
    department=None,
    include_assigner=False,
):
    creator_profile = _active_creator_profile(creator)

    if assignment_type == TaskAssignmentType.PERSONAL:
        return None, [creator_profile]

    if assignment_type == TaskAssignmentType.USERS:
        profiles = _resolve_selected_user_profiles(
            creator=creator,
            user_ids=user_ids,
        )

        return None, profiles

    if assignment_type == TaskAssignmentType.DEPARTMENT:
        return _resolve_department_profiles(
            creator=creator,
            department=department,
            include_assigner=include_assigner,
        )

    raise ValidationError({"assignment_type": ("Unsupported assignment type.")})


def _display_name(user):
    return user.get_full_name().strip() or user.username


def _build_task(
    *,
    batch,
    profile,
):
    department = profile.department

    task = Task(
        batch=batch,
        assigned_to=profile.user,
        assignee_name=_display_name(profile.user),
        assignee_email=profile.user.email,
        assignee_employee_id=(profile.employee_id or ""),
        assigned_department=department,
        assigned_department_name=(department.name if department else ""),
        assigned_department_code=(department.code if department else ""),
    )

    task.full_clean()

    return task


def _notification_channels(channels=None):
    values = (
        channels
        if channels is not None
        else getattr(
            settings,
            "TASK_ASSIGNMENT_NOTIFICATION_CHANNELS",
            [
                NotificationChannel.DASHBOARD,
                NotificationChannel.EMAIL,
            ],
        )
    )

    valid_channels = {value for value, _label in NotificationChannel.choices}

    result = []

    for channel in values:
        channel = str(channel).upper()

        if channel not in valid_channels:
            raise ValidationError(
                {
                    "notification_channels": (
                        f"Unsupported notification channel: {channel}"
                    )
                }
            )

        if channel not in result:
            result.append(channel)

    if not result:
        raise ValidationError(
            {"notification_channels": ("Select at least one notification channel.")}
        )

    return result


def _notification_event_mode(event_mode=None):
    value = event_mode or getattr(
        settings,
        "TASK_ASSIGNMENT_NOTIFICATION_EVENT_MODE",
        NotificationEventMode.SHARED,
    )

    value = str(value).upper()

    valid_modes = {choice for choice, _label in NotificationEventMode.choices}

    if value not in valid_modes:
        raise ValidationError(
            {"notification_event_mode": ("Unsupported notification event mode.")}
        )

    return value


def _send_assignment_notification_safely(
    *,
    batch,
    tasks,
    actor,
    channels,
    event_mode,
):
    try:
        action_url = getattr(
            settings,
            "TASK_ASSIGNMENT_NOTIFICATION_ACTION_URL",
            "/dashboard/tasks",
        )

        recipient_specs = [
            RecipientSpec(
                user=task.assigned_to,
                action_url=action_url,
                action_label="View tasks",
                metadata={
                    "task_id": str(task.id),
                    "task_batch_id": str(batch.id),
                },
                template_context={
                    "task_id": str(task.id),
                    "task_title": batch.title,
                    "task_priority": batch.priority,
                    "task_due_at": (batch.due_at.isoformat() if batch.due_at else ""),
                },
            )
            for task in tasks
            if task.assigned_to_id
        ]

        notify(
            recipients=recipient_specs,
            notification_type="task.assigned",
            category="task",
            title="New task assigned",
            message=(f'You have been assigned "{batch.title}".'),
            channels=channels,
            event_mode=event_mode,
            severity=("URGENT" if batch.priority == TaskPriority.URGENT else "INFO"),
            actor=actor,
            source=batch,
            template_key="task.assigned",
            metadata={
                "task_batch_id": str(batch.id),
                "assignment_type": batch.assignment_type,
                "priority": batch.priority,
                "due_at": (batch.due_at.isoformat() if batch.due_at else None),
                "recipient_count": len(tasks),
            },
            template_context={
                "task_title": batch.title,
                "task_description": batch.description,
                "task_priority": batch.priority,
                "task_due_at": (batch.due_at.isoformat() if batch.due_at else ""),
                "assigned_by_name": batch.created_by_name,
            },
            deduplication_key=(f"task-assigned:{batch.id}"),
        )

    except Exception:
        # Task creation has already committed.
        # A notification problem must not undo it.
        logger.exception(
            "Unable to create assignment notifications for task batch %s.",
            batch.id,
        )


def _schedule_assignment_notification(
    *,
    batch,
    tasks,
    actor,
    channels,
    event_mode,
):
    transaction.on_commit(
        lambda: _send_assignment_notification_safely(
            batch=batch,
            tasks=tasks,
            actor=actor,
            channels=channels,
            event_mode=event_mode,
        )
    )


@transaction.atomic
def create_task_assignment(
    *,
    creator,
    assignment_type,
    title,
    description="",
    priority=TaskPriority.MEDIUM,
    start_at=None,
    due_at=None,
    user_ids=None,
    department=None,
    include_assigner=False,
    notification_channels=None,
    notification_event_mode=None,
    request=None,
):
    """
    Create one task batch and one independent task row
    for every resolved recipient.

    Personal tasks do not generate assignment notifications.
    """

    _validate_assignment_type(assignment_type)

    _validate_priority(priority)

    _validate_dates(
        start_at,
        due_at,
    )

    title = _normalise_title(title)
    description = (description or "").strip()

    creator_profile = _active_creator_profile(creator)

    resolved_department, profiles = resolve_assignment_recipients(
        creator=creator,
        assignment_type=assignment_type,
        user_ids=user_ids,
        department=department,
        include_assigner=include_assigner,
    )

    if (
        assignment_type != TaskAssignmentType.PERSONAL
        and not can_assign_across_organisation(creator)
        and not creator.has_perm("tasks.assign_task")
        and not creator.has_perm("tasks.assign_department_task")
        and not creator.has_perm("tasks.manage_department_task")
    ):
        raise PermissionDenied("You do not have permission to assign tasks.")

    batch = TaskBatch(
        title=title,
        description=description,
        assignment_type=assignment_type,
        priority=priority,
        start_at=start_at,
        due_at=due_at,
        source_department=resolved_department,
        source_department_name=(
            resolved_department.name if resolved_department else ""
        ),
        source_department_code=(
            resolved_department.code if resolved_department else ""
        ),
        created_by=creator,
        created_by_name=_display_name(creator),
        created_by_email=creator.email,
    )

    batch.full_clean()
    batch.save()

    tasks = [
        _build_task(
            batch=batch,
            profile=profile,
        )
        for profile in profiles
    ]

    Task.objects.bulk_create(tasks)

    create_batch_activity(
        batch=batch,
        actor=creator,
        activity_type=(TaskActivityType.BATCH_CREATED),
        new_value={
            "title": batch.title,
            "assignment_type": batch.assignment_type,
            "priority": batch.priority,
            "start_at": (batch.start_at.isoformat() if batch.start_at else None),
            "due_at": (batch.due_at.isoformat() if batch.due_at else None),
            "recipient_count": len(tasks),
        },
    )

    create_task_activities(
        tasks=tasks,
        actor=creator,
        activity_type=(TaskActivityType.TASK_ASSIGNED),
        new_value={
            "batch_id": str(batch.id),
            "title": batch.title,
            "priority": batch.priority,
        },
    )

    create_audit_log(
        user=creator,
        event_category=(AuditLog.EventCategory.CRUD),
        event_type=AuditLog.EventType.CREATE,
        status=AuditLog.EventStatus.SUCCESS,
        app_label="tasks",
        resource="task_batch",
        action="create",
        object_id=batch.id,
        request=request,
        metadata={
            "title": batch.title,
            "assignment_type": batch.assignment_type,
            "priority": batch.priority,
            "department_id": (
                str(resolved_department.id) if resolved_department else None
            ),
            "recipient_count": len(tasks),
            "recipient_user_ids": [
                str(task.assigned_to_id) for task in tasks if task.assigned_to_id
            ],
        },
    )

    notifications_enabled = bool(
        getattr(
            settings,
            "TASK_ASSIGNMENT_NOTIFICATIONS_ENABLED",
            True,
        )
    )

    notification_scheduled = bool(
        notifications_enabled and assignment_type != TaskAssignmentType.PERSONAL
    )

    if notification_scheduled:
        channels = _notification_channels(notification_channels)

        event_mode = _notification_event_mode(notification_event_mode)

        _schedule_assignment_notification(
            batch=batch,
            tasks=tasks,
            actor=creator,
            channels=channels,
            event_mode=event_mode,
        )

    return TaskAssignmentResult(
        batch=batch,
        tasks=tasks,
        notification_scheduled=notification_scheduled,
    )
