import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from notifications.constants import NotificationChannel
from .querysets import NoHardDeleteManager

from .constants import (
    TaskActivityType,
    TaskAssignmentType,
    TaskPriority,
    TaskStatus,
)


ACTIVE_TASK_STATUSES = (
    TaskStatus.TO_DO,
    TaskStatus.IN_PROGRESS,
    TaskStatus.BLOCKED,
)

FINAL_TASK_STATUSES = (
    TaskStatus.COMPLETED,
    TaskStatus.CANCELLED,
)


class TaskBatch(models.Model):
    """
    The common instruction from which individual staff tasks
    are generated.

    A department assignment creates one TaskBatch and one
    Task record for every selected department member.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    objects = NoHardDeleteManager()

    title = models.CharField(max_length=255)

    description = models.TextField(blank=True)

    assignment_type = models.CharField(
        max_length=20,
        choices=TaskAssignmentType.choices,
        db_index=True,
    )

    priority = models.CharField(
        max_length=20,
        choices=TaskPriority.choices,
        default=TaskPriority.MEDIUM,
        db_index=True,
    )

    start_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    due_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    source_department = models.ForeignKey(
        "accounts.Department",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_batches",
    )

    # Snapshots preserve the original assignment information
    # if the department is renamed or later deleted.
    source_department_name = models.CharField(
        max_length=150,
        blank=True,
    )

    source_department_code = models.CharField(
        max_length=20,
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_task_batches",
    )

    created_by_name = models.CharField(
        max_length=255,
        blank=True,
    )

    created_by_email = models.EmailField(
        blank=True,
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_task_batches",
    )

    cancellation_reason = models.TextField(
        blank=True,
    )

    archived_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archived_task_batches",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-created_at"]

        # Deliberately exclude Django's default delete permission.
        default_permissions = (
            "add",
            "change",
            "view",
        )

        indexes = [
            models.Index(
                fields=[
                    "assignment_type",
                    "priority",
                    "-created_at",
                ],
                name="task_batch_type_prio_idx",
            ),
            models.Index(
                fields=[
                    "created_by",
                    "archived_at",
                    "-created_at",
                ],
                name="task_batch_creator_idx",
            ),
            models.Index(
                fields=[
                    "source_department",
                    "archived_at",
                    "-created_at",
                ],
                name="task_batch_dept_idx",
            ),
        ]

        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(start_at__isnull=True)
                    | Q(due_at__isnull=True)
                    | Q(due_at__gte=F("start_at"))
                ),
                name="task_batch_dates_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        cancelled_at__isnull=True,
                        cancellation_reason="",
                    )
                    | (Q(cancelled_at__isnull=False) & ~Q(cancellation_reason=""))
                ),
                name="task_batch_cancel_valid",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        Q(assignment_type=(TaskAssignmentType.DEPARTMENT))
                        & ~Q(source_department_name="")
                    )
                    | (
                        ~Q(assignment_type=(TaskAssignmentType.DEPARTMENT))
                        & Q(source_department__isnull=True)
                        & Q(source_department_name="")
                        & Q(source_department_code="")
                    )
                ),
                name="task_batch_department_valid",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def is_cancelled(self):
        return self.cancelled_at is not None

    @property
    def is_archived(self):
        return self.archived_at is not None

    def clean(self):
        super().clean()

        if self.start_at and self.due_at and self.due_at < self.start_at:
            raise ValidationError(
                {"due_at": ("The due time cannot be before the start time.")}
            )

        if self.assignment_type == TaskAssignmentType.DEPARTMENT:
            # Required when first creating the assignment.
            # The FK may later become null if the department
            # is permanently deleted, while snapshots remain.
            if self._state.adding and not self.source_department_id:
                raise ValidationError(
                    {
                        "source_department": (
                            "A department assignment requires a department."
                        )
                    }
                )

            if not self.source_department_name:
                raise ValidationError(
                    {
                        "source_department_name": (
                            "A department-name snapshot is required."
                        )
                    }
                )

        elif (
            self.source_department_id
            or self.source_department_name
            or self.source_department_code
        ):
            raise ValidationError(
                {
                    "source_department": (
                        "Only department assignments may have a source department."
                    )
                }
            )

        if self.cancelled_at and not self.cancellation_reason.strip():
            raise ValidationError(
                {"cancellation_reason": ("A cancellation reason is required.")}
            )

        if not self.cancelled_at and self.cancellation_reason.strip():
            raise ValidationError(
                {
                    "cancellation_reason": (
                        "A cancellation reason cannot exist before cancellation."
                    )
                }
            )

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _("Task batches cannot be deleted. Cancel or archive the batch instead.")
        )


class Task(models.Model):
    """
    One assignee's independently managed task.

    Every selected recipient receives a separate Task record,
    even when they were assigned through the same batch.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    batch = models.ForeignKey(
        TaskBatch,
        on_delete=models.PROTECT,
        related_name="tasks",
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )

    # Assignee snapshots preserve history if the user changes
    # their details or is later deleted.
    assignee_name = models.CharField(
        max_length=255,
        blank=True,
    )

    assignee_email = models.EmailField(
        blank=True,
    )

    assignee_employee_id = models.CharField(
        max_length=50,
        blank=True,
    )

    assigned_department = models.ForeignKey(
        "accounts.Department",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )

    assigned_department_name = models.CharField(
        max_length=150,
        blank=True,
    )

    assigned_department_code = models.CharField(
        max_length=20,
        blank=True,
    )

    objects = NoHardDeleteManager()

    status = models.CharField(
        max_length=20,
        choices=TaskStatus.choices,
        default=TaskStatus.TO_DO,
        db_index=True,
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_tasks",
    )

    cancellation_reason = models.TextField(
        blank=True,
    )

    archived_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archived_tasks",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-created_at"]

        default_permissions = (
            "add",
            "change",
            "view",
        )

        permissions = [
            (
                "assign_task",
                "Can assign tasks",
            ),
            (
                "cancel_task",
                "Can cancel tasks",
            ),
            (
                "archive_task",
                "Can archive tasks",
            ),
            (
                "restore_task",
                "Can restore archived tasks",
            ),
            (
                "view_department_task",
                "Can view department tasks",
            ),
            (
                "assign_department_task",
                "Can assign department tasks",
            ),
            (
                "manage_department_task",
                "Can manage department tasks",
            ),
            (
                "view_all_task",
                "Can view all tasks",
            ),
            (
                "assign_all_task",
                "Can assign tasks across the organisation",
            ),
            (
                "manage_all_task",
                "Can manage all organisation tasks",
            ),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "batch",
                    "assigned_to",
                ],
                condition=Q(assigned_to__isnull=False),
                name="task_unique_batch_assignee",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=TaskStatus.COMPLETED,
                        completed_at__isnull=False,
                        cancelled_at__isnull=True,
                        cancellation_reason="",
                    )
                    | (
                        Q(
                            status=TaskStatus.CANCELLED,
                            completed_at__isnull=True,
                            cancelled_at__isnull=False,
                        )
                        & ~Q(cancellation_reason="")
                    )
                    | Q(
                        status__in=ACTIVE_TASK_STATUSES,
                        completed_at__isnull=True,
                        cancelled_at__isnull=True,
                        cancellation_reason="",
                    )
                ),
                name="task_status_dates_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(archived_at__isnull=True) | Q(status__in=FINAL_TASK_STATUSES)
                ),
                name="task_archive_state_valid",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "assigned_to",
                    "status",
                    "archived_at",
                    "-created_at",
                ],
                name="task_assignee_state_idx",
            ),
            models.Index(
                fields=[
                    "assigned_department",
                    "status",
                    "archived_at",
                    "-created_at",
                ],
                name="task_department_state_idx",
            ),
            models.Index(
                fields=[
                    "batch",
                    "status",
                ],
                name="task_batch_status_idx",
            ),
        ]

    def __str__(self):
        assignee = self.assignee_name or self.assigned_to or "Unknown assignee"

        return f"{self.batch.title} - {assignee}"

    @property
    def is_archived(self):
        return self.archived_at is not None

    @property
    def is_overdue(self):
        return bool(
            self.batch.due_at
            and self.batch.due_at < timezone.now()
            and self.status in ACTIVE_TASK_STATUSES
            and self.archived_at is None
        )

    def clean(self):
        super().clean()

        if not self.assigned_to_id and not self.assignee_name:
            raise ValidationError(
                {
                    "assigned_to": (
                        "A task requires an assignee or an assignee snapshot."
                    )
                }
            )

        if self.status == TaskStatus.COMPLETED:
            if not self.completed_at:
                raise ValidationError(
                    {"completed_at": ("A completed task needs a completion time.")}
                )

            if self.cancelled_at or self.cancellation_reason.strip():
                raise ValidationError(
                    {"status": ("A completed task cannot also be cancelled.")}
                )

        elif self.status == TaskStatus.CANCELLED:
            if not self.cancelled_at:
                raise ValidationError(
                    {"cancelled_at": ("A cancelled task needs a cancellation time.")}
                )

            if not self.cancellation_reason.strip():
                raise ValidationError(
                    {"cancellation_reason": ("A cancellation reason is required.")}
                )

            if self.completed_at:
                raise ValidationError(
                    {"status": ("A cancelled task cannot also be completed.")}
                )

        elif self.completed_at or self.cancelled_at or self.cancellation_reason.strip():
            raise ValidationError(
                {
                    "status": (
                        "Active tasks cannot have completion or cancellation data."
                    )
                }
            )

        if self.archived_at and self.status not in FINAL_TASK_STATUSES:
            raise ValidationError(
                {"archived_at": ("Only completed or cancelled tasks can be archived.")}
            )

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _("Tasks cannot be deleted. Cancel or archive the task instead.")
        )


class TaskComment(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    task = models.ForeignKey(
        Task,
        on_delete=models.PROTECT,
        related_name="comments",
    )

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_comments",
    )

    body = models.TextField()

    edited_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    removed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="removed_task_comments",
    )

    removal_reason = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    objects = NoHardDeleteManager()

    class Meta:
        ordering = ["created_at"]

        default_permissions = (
            "add",
            "change",
            "view",
        )

        permissions = [
            (
                "moderate_taskcomment",
                "Can remove task comments",
            ),
        ]

        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        removed_at__isnull=True,
                        removed_by__isnull=True,
                        removal_reason="",
                    )
                    | (Q(removed_at__isnull=False) & ~Q(removal_reason=""))
                ),
                name="task_comment_removed_valid",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "task",
                    "removed_at",
                    "created_at",
                ],
                name="task_comment_task_idx",
            ),
        ]

    def __str__(self):
        return f"Comment on {self.task_id}"

    @property
    def is_removed(self):
        return self.removed_at is not None

    def clean(self):
        super().clean()

        if self.removed_at and not self.removal_reason.strip():
            raise ValidationError(
                {"removal_reason": ("A reason is required when removing a comment.")}
            )

        if not self.removed_at and (self.removed_by_id or self.removal_reason.strip()):
            raise ValidationError(
                {
                    "removed_at": (
                        "Removal details cannot exist before the comment is removed."
                    )
                }
            )

    def remove(self, *, removed_by, reason):
        reason = (reason or "").strip()

        if self.removed_at:
            raise ValidationError(_("This comment has already been removed."))

        if not reason:
            raise ValidationError(_("A removal reason is required."))

        self.removed_at = timezone.now()
        self.removed_by = removed_by
        self.removal_reason = reason

        self.save(
            update_fields=[
                "removed_at",
                "removed_by",
                "removal_reason",
                "updated_at",
            ]
        )

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _("Task comments cannot be deleted. Remove the comment instead.")
        )


class TaskReminder(models.Model):
    """
    A personal-task reminder scheduled for one user.

    The reminder's Notification is created later by the
    reminder service with scheduled_at=remind_at.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    task = models.ForeignKey(
        Task,
        on_delete=models.PROTECT,
        related_name="reminders",
    )

    objects = NoHardDeleteManager()

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="task_reminders",
    )

    remind_at = models.DateTimeField(
        db_index=True,
    )

    # Example:
    # ["DASHBOARD", "EMAIL"]
    channels = models.JSONField(
        default=list,
    )

    notification = models.ForeignKey(
        "notifications.Notification",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_reminders",
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_task_reminders",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["remind_at"]

        default_permissions = (
            "add",
            "change",
            "view",
        )

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "task",
                    "user",
                    "remind_at",
                ],
                condition=Q(cancelled_at__isnull=True),
                name="task_unique_active_user_reminder",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        cancelled_at__isnull=True,
                        cancelled_by__isnull=True,
                    )
                    | Q(cancelled_at__isnull=False)
                ),
                name="task_reminder_cancel_valid",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "user",
                    "cancelled_at",
                    "remind_at",
                ],
                name="task_reminder_due_idx",
            ),
        ]

    def __str__(self):
        return f"Reminder for {self.task_id} at {self.remind_at}"

    @property
    def is_cancelled(self):
        return self.cancelled_at is not None

    @property
    def is_due(self):
        return bool(not self.cancelled_at and self.remind_at <= timezone.now())

    def clean(self):
        super().clean()

        if self.task_id:
            if self.task.batch.assignment_type != TaskAssignmentType.PERSONAL:
                raise ValidationError(
                    {"task": ("Reminders can only be created for personal tasks.")}
                )

            if self.task.assigned_to_id != self.user_id:
                raise ValidationError(
                    {"user": ("Only the personal-task owner can set its reminder.")}
                )

            if self.task.status in FINAL_TASK_STATUSES or self.task.archived_at:
                raise ValidationError(
                    {"task": ("A finished or archived task cannot receive a reminder.")}
                )

        valid_channels = {value for value, _label in NotificationChannel.choices}

        if not isinstance(self.channels, list) or not self.channels:
            raise ValidationError(
                {"channels": ("Select at least one reminder channel.")}
            )

        normalised_channels = []

        for channel in self.channels:
            channel_value = str(channel).upper()

            if channel_value not in valid_channels:
                raise ValidationError(
                    {"channels": (f"Unsupported reminder channel: {channel}")}
                )

            if channel_value not in normalised_channels:
                normalised_channels.append(channel_value)

        self.channels = normalised_channels

        if self._state.adding and self.remind_at <= timezone.now():
            raise ValidationError(
                {"remind_at": ("A new reminder must be scheduled in the future.")}
            )

        if self.cancelled_at is None and self.cancelled_by_id:
            raise ValidationError(
                {"cancelled_at": ("A cancellation time is required.")}
            )

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _("Task reminders cannot be deleted. Cancel the reminder instead.")
        )


class TaskActivity(models.Model):
    """
    Append-only operational history for a task or batch.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    task = models.ForeignKey(
        Task,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="activities",
    )

    objects = NoHardDeleteManager()

    batch = models.ForeignKey(
        TaskBatch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="activities",
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_activities",
    )

    activity_type = models.CharField(
        max_length=40,
        choices=TaskActivityType.choices,
        db_index=True,
    )

    previous_value = models.JSONField(
        null=True,
        blank=True,
    )

    new_value = models.JSONField(
        null=True,
        blank=True,
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-created_at"]

        # Activities must only be created by backend services.
        default_permissions = ("view",)

        constraints = [
            models.CheckConstraint(
                condition=(Q(task__isnull=False) | Q(batch__isnull=False)),
                name="task_activity_target_valid",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "task",
                    "-created_at",
                ],
                name="task_activity_task_idx",
            ),
            models.Index(
                fields=[
                    "batch",
                    "-created_at",
                ],
                name="task_activity_batch_idx",
            ),
            models.Index(
                fields=[
                    "activity_type",
                    "-created_at",
                ],
                name="task_activity_type_idx",
            ),
        ]

    def __str__(self):
        return f"{self.activity_type} at {self.created_at}"

    def clean(self):
        super().clean()

        if not self.task_id and not self.batch_id:
            raise ValidationError(
                _("Task activity must reference a task or task batch.")
            )

        if self.task_id and self.batch_id and self.task.batch_id != self.batch_id:
            raise ValidationError(
                _("The activity task and batch do not belong together.")
            )

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(_("Task activity records are append-only."))

        if self.task_id and not self.batch_id:
            self.batch_id = self.task.batch_id

        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Task activity records cannot be deleted."))
