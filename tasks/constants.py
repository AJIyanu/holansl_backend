from django.db import models
from django.utils.translation import gettext_lazy as _


class TaskAssignmentType(models.TextChoices):
    PERSONAL = "PERSONAL", _("Personal")
    USERS = "USERS", _("Selected staff")
    DEPARTMENT = "DEPARTMENT", _("Department")


class TaskPriority(models.TextChoices):
    LOW = "LOW", _("Low")
    MEDIUM = "MEDIUM", _("Medium")
    HIGH = "HIGH", _("High")
    URGENT = "URGENT", _("Urgent")


class TaskStatus(models.TextChoices):
    TO_DO = "TO_DO", _("To do")
    IN_PROGRESS = "IN_PROGRESS", _("In progress")
    BLOCKED = "BLOCKED", _("Blocked")
    COMPLETED = "COMPLETED", _("Completed")
    CANCELLED = "CANCELLED", _("Cancelled")


class TaskActivityType(models.TextChoices):
    BATCH_CREATED = (
        "BATCH_CREATED",
        _("Task batch created"),
    )

    BATCH_UPDATED = (
        "BATCH_UPDATED",
        _("Task batch updated"),
    )

    BATCH_CANCELLED = (
        "BATCH_CANCELLED",
        _("Task batch cancelled"),
    )

    BATCH_ARCHIVED = (
        "BATCH_ARCHIVED",
        _("Task batch archived"),
    )

    BATCH_RESTORED = (
        "BATCH_RESTORED",
        _("Task batch restored"),
    )

    TASK_CREATED = (
        "TASK_CREATED",
        _("Task created"),
    )

    TASK_ASSIGNED = (
        "TASK_ASSIGNED",
        _("Task assigned"),
    )

    TASK_REASSIGNED = (
        "TASK_REASSIGNED",
        _("Task reassigned"),
    )

    TASK_DETAILS_UPDATED = (
        "TASK_DETAILS_UPDATED",
        _("Task details updated"),
    )

    STATUS_CHANGED = (
        "STATUS_CHANGED",
        _("Task status changed"),
    )

    TASK_CANCELLED = (
        "TASK_CANCELLED",
        _("Task cancelled"),
    )

    TASK_ARCHIVED = (
        "TASK_ARCHIVED",
        _("Task archived"),
    )

    TASK_RESTORED = (
        "TASK_RESTORED",
        _("Task restored"),
    )

    COMMENT_ADDED = (
        "COMMENT_ADDED",
        _("Comment added"),
    )

    COMMENT_EDITED = (
        "COMMENT_EDITED",
        _("Comment edited"),
    )

    COMMENT_REMOVED = (
        "COMMENT_REMOVED",
        _("Comment removed"),
    )

    REMINDER_CREATED = (
        "REMINDER_CREATED",
        _("Reminder created"),
    )

    REMINDER_UPDATED = (
        "REMINDER_UPDATED",
        _("Reminder updated"),
    )

    REMINDER_CANCELLED = (
        "REMINDER_CANCELLED",
        _("Reminder cancelled"),
    )

    REMINDER_SENT = (
        "REMINDER_SENT",
        _("Reminder sent"),
    )

    NOTIFICATION_CREATED = (
        "NOTIFICATION_CREATED",
        _("Notification created"),
    )
