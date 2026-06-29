from datetime import timedelta
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient

from notifications.constants import (
    NotificationChannel,
)

from tasks.constants import (
    TaskActivityType,
    TaskStatus,
)
from tasks.models import (
    Task,
    TaskActivity,
)
from tasks.serializers import (
    TaskActivitySerializer,
)
from tasks.services.comments import (
    add_task_comment,
    edit_task_comment,
)
from tasks.services.reminders import (
    cancel_task_reminder,
    create_task_reminder,
)

from .base import TaskModelTestCase


@override_settings(
    TASK_REMINDERS_ENABLED=True,
    TASK_DASHBOARD_REMINDERS_ENABLED=True,
    TASK_SCHEDULED_EXTERNAL_DELIVERY_ENABLED=False,
    NOTIFICATION_PROCESSING_MODE="outbox",
)
class TaskHardeningTests(TaskModelTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_bulk_task_delete_is_blocked(self):
        task = self.create_personal_task()

        with self.assertRaises(ValidationError):
            Task.objects.filter(pk=task.pk).delete()

        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

    def test_cancelled_reminder_time_can_be_reused(
        self,
    ):
        task = self.create_personal_task()

        remind_at = timezone.now() + timedelta(minutes=30)

        first_reminder = create_task_reminder(
            task=task,
            user=self.user,
            remind_at=remind_at,
            channels=[NotificationChannel.DASHBOARD],
        )

        cancel_task_reminder(
            reminder=first_reminder,
            user=self.user,
            reason="Rescheduling.",
        )

        second_reminder = create_task_reminder(
            task=task,
            user=self.user,
            remind_at=remind_at,
            channels=[NotificationChannel.DASHBOARD],
        )

        self.assertNotEqual(
            first_reminder.id,
            second_reminder.id,
        )

    def test_comment_edit_activity_does_not_store_body(
        self,
    ):
        task = self.create_personal_task()

        comment = add_task_comment(
            task=task,
            actor=self.user,
            body="Original sensitive comment.",
        )

        edit_task_comment(
            comment=comment,
            actor=self.user,
            body="Updated sensitive comment.",
        )

        activity = TaskActivity.objects.get(
            task=task,
            activity_type=(TaskActivityType.COMMENT_EDITED),
        )

        self.assertNotIn(
            "body",
            activity.previous_value,
        )

        self.assertNotIn(
            "body",
            activity.new_value,
        )

    def test_activity_serializer_redacts_old_comment_body(
        self,
    ):
        task = self.create_personal_task()

        activity = TaskActivity.objects.create(
            task=task,
            actor=self.user,
            activity_type=(TaskActivityType.COMMENT_EDITED),
            previous_value={"body": "Old private text"},
            new_value={"body": "New private text"},
            metadata={"comment_body": "Private metadata text"},
        )

        data = TaskActivitySerializer(activity).data

        self.assertEqual(
            data["previous_value"]["body"],
            "[REDACTED]",
        )

        self.assertEqual(
            data["new_value"]["body"],
            "[REDACTED]",
        )

        self.assertEqual(
            data["metadata"]["comment_body"],
            "[REDACTED]",
        )

    def test_reminder_serializer_rejects_other_users_task(
        self,
    ):
        other_batch = self.create_personal_batch(
            created_by=self.other_user,
            created_by_name=(
                self.other_user.get_full_name() or self.other_user.username
            ),
            created_by_email=(self.other_user.email),
        )

        other_task = self.create_personal_task(
            batch=other_batch,
            assigned_to=self.other_user,
            assignee_name=(self.other_user.get_full_name() or self.other_user.username),
            assignee_email=(self.other_user.email),
            assignee_employee_id=(self.other_profile.employee_id),
        )

        self.client.force_authenticate(self.user)

        response = self.client.post(
            reverse("task-reminder-list"),
            {
                "task_id": str(other_task.id),
                "remind_at": (timezone.now() + timedelta(minutes=30)).isoformat(),
                "channels": ["DASHBOARD"],
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_integrity_command_cancels_invalid_reminder(
        self,
    ):
        task = self.create_personal_task()

        reminder = create_task_reminder(
            task=task,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=[NotificationChannel.DASHBOARD],
        )

        Task.objects.filter(pk=task.pk).update(
            status=TaskStatus.COMPLETED,
            completed_at=timezone.now(),
        )

        output = StringIO()

        call_command(
            "check_task_integrity",
            "--fix",
            stdout=output,
        )

        reminder.refresh_from_db()

        self.assertIsNotNone(reminder.cancelled_at)

        self.assertIn(
            "Task integrity issues repaired",
            output.getvalue(),
        )

    def test_invalid_activity_type_returns_error(
        self,
    ):
        task = self.create_personal_task()

        self.client.force_authenticate(self.user)

        response = self.client.get(
            reverse(
                "task-activity",
                args=[task.id],
            ),
            {
                "activity_type": "NOT_A_REAL_ACTIVITY",
            },
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
