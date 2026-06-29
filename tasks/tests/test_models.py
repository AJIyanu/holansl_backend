from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from notifications.constants import NotificationChannel

from tasks.constants import (
    TaskActivityType,
    TaskAssignmentType,
    TaskStatus,
)
from tasks.models import (
    Task,
    TaskActivity,
    TaskBatch,
    TaskComment,
    TaskReminder,
)

from .base import TaskModelTestCase


class TaskBatchModelTests(TaskModelTestCase):
    def test_personal_batch_is_valid(self):
        batch = self.create_personal_batch()
        batch.full_clean()

        self.assertEqual(
            batch.assignment_type,
            TaskAssignmentType.PERSONAL,
        )

    def test_due_time_cannot_be_before_start_time(
        self,
    ):
        now = timezone.now()

        batch = TaskBatch(
            title="Invalid dates",
            assignment_type=(TaskAssignmentType.PERSONAL),
            start_at=now,
            due_at=now - timedelta(minutes=1),
            created_by=self.user,
        )

        with self.assertRaises(ValidationError):
            batch.full_clean()

    def test_department_batch_requires_snapshot(
        self,
    ):
        batch = TaskBatch(
            title="Department assignment",
            assignment_type=(TaskAssignmentType.DEPARTMENT),
            source_department=self.department,
            created_by=self.user,
        )

        with self.assertRaises(ValidationError):
            batch.full_clean()

    def test_department_batch_accepts_snapshot(
        self,
    ):
        batch = TaskBatch(
            title="Department assignment",
            assignment_type=(TaskAssignmentType.DEPARTMENT),
            source_department=self.department,
            source_department_name=(self.department.name),
            source_department_code=(self.department.code),
            created_by=self.user,
        )

        batch.full_clean()

    def test_cancelled_batch_requires_reason(self):
        batch = self.create_personal_batch()

        batch.cancelled_at = timezone.now()
        batch.cancellation_reason = ""

        with self.assertRaises(ValidationError):
            batch.full_clean()

    def test_batch_cannot_be_hard_deleted(self):
        batch = self.create_personal_batch()

        with self.assertRaises(ValidationError):
            batch.delete()


class TaskModelTests(TaskModelTestCase):
    def test_batch_cannot_contain_duplicate_assignee(
        self,
    ):
        batch = self.create_personal_batch()

        self.create_personal_task(batch=batch)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Task.objects.create(
                batch=batch,
                assigned_to=self.user,
                assignee_name=(self.user.get_full_name()),
                assignee_email=self.user.email,
            )

    def test_completed_task_requires_completed_at(
        self,
    ):
        task = self.create_personal_task()

        task.status = TaskStatus.COMPLETED

        with self.assertRaises(ValidationError):
            task.full_clean()

    def test_cancelled_task_requires_time_and_reason(
        self,
    ):
        task = self.create_personal_task()

        task.status = TaskStatus.CANCELLED
        task.cancelled_at = timezone.now()

        with self.assertRaises(ValidationError):
            task.full_clean()

    def test_active_task_cannot_be_archived(self):
        task = self.create_personal_task()

        task.archived_at = timezone.now()

        with self.assertRaises(ValidationError):
            task.full_clean()

    def test_completed_task_can_be_archived(self):
        task = self.create_personal_task()

        task.status = TaskStatus.COMPLETED
        task.completed_at = timezone.now()
        task.archived_at = timezone.now()

        task.full_clean()

    def test_task_cannot_be_hard_deleted(self):
        task = self.create_personal_task()

        with self.assertRaises(ValidationError):
            task.delete()


class TaskCommentModelTests(TaskModelTestCase):
    def test_remove_keeps_comment_and_records_removal(
        self,
    ):
        task = self.create_personal_task()

        comment = TaskComment.objects.create(
            task=task,
            author=self.user,
            body="Original comment",
        )

        comment.remove(
            removed_by=self.other_user,
            reason="Contains incorrect information.",
        )

        comment.refresh_from_db()

        self.assertTrue(comment.is_removed)

        self.assertEqual(
            comment.removed_by,
            self.other_user,
        )

        self.assertEqual(
            comment.body,
            "Original comment",
        )

        self.assertEqual(
            comment.removal_reason,
            "Contains incorrect information.",
        )

    def test_comment_removal_requires_reason(self):
        task = self.create_personal_task()

        comment = TaskComment.objects.create(
            task=task,
            author=self.user,
            body="Comment",
        )

        with self.assertRaises(ValidationError):
            comment.remove(
                removed_by=self.other_user,
                reason="",
            )

    def test_comment_cannot_be_hard_deleted(self):
        task = self.create_personal_task()

        comment = TaskComment.objects.create(
            task=task,
            author=self.user,
            body="Comment",
        )

        with self.assertRaises(ValidationError):
            comment.delete()


class TaskReminderModelTests(TaskModelTestCase):
    def test_owner_can_create_future_personal_reminder(
        self,
    ):
        task = self.create_personal_task()

        reminder = TaskReminder(
            task=task,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=[
                "dashboard",
                "EMAIL",
                "EMAIL",
            ],
        )

        reminder.full_clean()

        self.assertEqual(
            reminder.channels,
            [
                NotificationChannel.DASHBOARD,
                NotificationChannel.EMAIL,
            ],
        )

    def test_reminder_cannot_be_created_for_assigned_task(
        self,
    ):
        batch = TaskBatch.objects.create(
            title="Assigned task",
            assignment_type=(TaskAssignmentType.USERS),
            created_by=self.other_user,
        )

        task = Task.objects.create(
            batch=batch,
            assigned_to=self.user,
            assignee_name=(self.user.get_full_name()),
            assignee_email=self.user.email,
        )

        reminder = TaskReminder(
            task=task,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=[NotificationChannel.DASHBOARD],
        )

        with self.assertRaises(ValidationError):
            reminder.full_clean()

    def test_other_user_cannot_create_reminder(
        self,
    ):
        task = self.create_personal_task()

        reminder = TaskReminder(
            task=task,
            user=self.other_user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=[NotificationChannel.DASHBOARD],
        )

        with self.assertRaises(ValidationError):
            reminder.full_clean()

    def test_new_reminder_must_be_in_future(self):
        task = self.create_personal_task()

        reminder = TaskReminder(
            task=task,
            user=self.user,
            remind_at=(timezone.now() - timedelta(minutes=1)),
            channels=[NotificationChannel.DASHBOARD],
        )

        with self.assertRaises(ValidationError):
            reminder.full_clean()

    def test_reminder_requires_supported_channel(
        self,
    ):
        task = self.create_personal_task()

        reminder = TaskReminder(
            task=task,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=["SMS"],
        )

        with self.assertRaises(ValidationError):
            reminder.full_clean()


class TaskActivityModelTests(TaskModelTestCase):
    def test_activity_requires_task_or_batch(self):
        activity = TaskActivity(
            actor=self.user,
            activity_type=(TaskActivityType.TASK_CREATED),
        )

        with self.assertRaises(ValidationError):
            activity.full_clean()

    def test_task_activity_records_batch(self):
        task = self.create_personal_task()

        activity = TaskActivity.objects.create(
            task=task,
            actor=self.user,
            activity_type=(TaskActivityType.TASK_CREATED),
        )

        self.assertEqual(
            activity.batch_id,
            task.batch_id,
        )

    def test_activity_is_append_only(self):
        task = self.create_personal_task()

        activity = TaskActivity.objects.create(
            task=task,
            actor=self.user,
            activity_type=(TaskActivityType.TASK_CREATED),
        )

        activity.metadata = {"changed": True}

        with self.assertRaises(ValidationError):
            activity.save()

        with self.assertRaises(ValidationError):
            activity.delete()
