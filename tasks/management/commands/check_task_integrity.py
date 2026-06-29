from django.core.management.base import (
    BaseCommand,
    CommandError,
)
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from notifications.services import (
    cancel_scheduled_notification,
)

from tasks.constants import (
    TaskActivityType,
    TaskStatus,
)
from tasks.models import (
    ACTIVE_TASK_STATUSES,
    FINAL_TASK_STATUSES,
    Task,
    TaskActivity,
    TaskReminder,
)


class Command(BaseCommand):
    help = (
        "Checks task, reminder and batch lifecycle "
        "integrity. Use --fix to repair supported issues."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help=("Repair invalid reminders and active tasks under cancelled batches."),
        )

    def invalid_reminders(self):
        return (
            TaskReminder.objects.select_related(
                "task",
                "task__batch",
                "notification",
            )
            .filter(cancelled_at__isnull=True)
            .filter(
                Q(task__status__in=FINAL_TASK_STATUSES)
                | Q(task__archived_at__isnull=False)
                | Q(task__batch__cancelled_at__isnull=False)
                | Q(
                    task__batch__due_at__isnull=False,
                    remind_at__gt=F("task__batch__due_at"),
                )
            )
        )

    def inconsistent_batch_tasks(self):
        return Task.objects.select_related(
            "batch",
            "batch__cancelled_by",
        ).filter(
            batch__cancelled_at__isnull=False,
            status__in=ACTIVE_TASK_STATUSES,
        )

    def handle(self, *args, **options):
        fix = options["fix"]

        invalid_reminder_count = self.invalid_reminders().count()

        inconsistent_task_count = self.inconsistent_batch_tasks().count()

        issue_count = invalid_reminder_count + inconsistent_task_count

        if issue_count == 0:
            self.stdout.write(self.style.SUCCESS("Task integrity check passed."))
            return

        self.stdout.write(
            self.style.WARNING(
                (f"Found {invalid_reminder_count} invalid active reminder(s).")
            )
        )

        self.stdout.write(
            self.style.WARNING(
                (
                    f"Found {inconsistent_task_count} "
                    "active task(s) under cancelled batches."
                )
            )
        )

        if not fix:
            raise CommandError(
                ("Task integrity issues were found. Run the command again with --fix.")
            )

        with transaction.atomic():
            self.repair_reminders()
            self.repair_cancelled_batch_tasks()

        remaining_issues = (
            self.invalid_reminders().count() + self.inconsistent_batch_tasks().count()
        )

        if remaining_issues:
            raise CommandError(
                (f"{remaining_issues} task integrity issue(s) remain after repair.")
            )

        self.stdout.write(self.style.SUCCESS("Task integrity issues repaired."))

    def repair_reminders(self):
        now = timezone.now()

        for reminder in self.invalid_reminders():
            reason = "Automatically cancelled by the task-integrity check."

            reminder.cancelled_at = now
            reminder.cancelled_by = None

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
                    reason=reason,
                )

            TaskActivity.objects.create(
                task=reminder.task,
                actor=None,
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
                    "integrity_repair": True,
                },
            )

    def repair_cancelled_batch_tasks(self):
        now = timezone.now()

        for task in self.inconsistent_batch_tasks():
            reason = task.batch.cancellation_reason or (
                "The parent task assignment was cancelled."
            )

            previous_status = task.status

            task.status = TaskStatus.CANCELLED
            task.completed_at = None
            task.cancelled_at = task.batch.cancelled_at or now
            task.cancelled_by = task.batch.cancelled_by
            task.cancellation_reason = reason

            task.save(
                update_fields=[
                    "status",
                    "completed_at",
                    "cancelled_at",
                    "cancelled_by",
                    "cancellation_reason",
                    "updated_at",
                ]
            )

            TaskActivity.objects.create(
                task=task,
                actor=None,
                activity_type=(TaskActivityType.TASK_CANCELLED),
                previous_value={
                    "status": previous_status,
                },
                new_value={
                    "status": TaskStatus.CANCELLED,
                    "cancelled_at": task.cancelled_at.isoformat(),
                    "cancellation_reason": reason,
                },
                metadata={
                    "automatic": True,
                    "integrity_repair": True,
                },
            )
