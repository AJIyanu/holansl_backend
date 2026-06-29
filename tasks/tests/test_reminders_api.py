from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient

from notifications.constants import (
    NotificationChannel,
)

from tasks.services.reminders import (
    create_task_reminder,
)

from .base import TaskModelTestCase


@override_settings(
    TASK_REMINDERS_ENABLED=True,
    TASK_DASHBOARD_REMINDERS_ENABLED=True,
    TASK_SCHEDULED_EXTERNAL_DELIVERY_ENABLED=False,
    NOTIFICATION_PROCESSING_MODE="outbox",
)
class TaskReminderApiTests(TaskModelTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def test_capabilities_endpoint(self):
        self.authenticate(self.user)

        response = self.client.get(reverse("task-reminder-capabilities"))

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertTrue(response.data["channels"]["DASHBOARD"]["available"])

        self.assertFalse(response.data["channels"]["EMAIL"]["available"])

    def test_create_dashboard_reminder(self):
        task = self.create_personal_task()

        self.authenticate(self.user)

        response = self.client.post(
            reverse("task-reminder-list"),
            {
                "task_id": str(task.id),
                "remind_at": (timezone.now() + timedelta(minutes=30)).isoformat(),
                "channels": ["DASHBOARD"],
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )

        self.assertEqual(
            response.data["task"]["id"],
            str(task.id),
        )

        self.assertEqual(
            response.data["channels"],
            ["DASHBOARD"],
        )

    def test_email_channel_returns_validation_error(
        self,
    ):
        task = self.create_personal_task()

        self.authenticate(self.user)

        response = self.client.post(
            reverse("task-reminder-list"),
            {
                "task_id": str(task.id),
                "remind_at": (timezone.now() + timedelta(minutes=30)).isoformat(),
                "channels": ["EMAIL"],
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_list_only_returns_current_user_reminders(
        self,
    ):
        own_task = self.create_personal_task()

        own_reminder = create_task_reminder(
            task=own_task,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=[NotificationChannel.DASHBOARD],
        )

        other_task = self.create_personal_task(
            batch=self.create_personal_batch(
                created_by=self.other_user,
                created_by_name=(self.other_user.get_full_name()),
                created_by_email=(self.other_user.email),
            ),
            assigned_to=self.other_user,
            assignee_name=(self.other_user.get_full_name()),
            assignee_email=self.other_user.email,
            assignee_employee_id=(self.other_profile.employee_id),
        )

        create_task_reminder(
            task=other_task,
            user=self.other_user,
            remind_at=(timezone.now() + timedelta(minutes=45)),
            channels=[NotificationChannel.DASHBOARD],
        )

        self.authenticate(self.user)

        response = self.client.get(reverse("task-reminder-list"))

        returned_ids = {row["id"] for row in response.data["results"]}

        self.assertIn(
            str(own_reminder.id),
            returned_ids,
        )

        self.assertEqual(
            len(returned_ids),
            1,
        )

    def test_patch_reminder(self):
        task = self.create_personal_task()

        reminder = create_task_reminder(
            task=task,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=[NotificationChannel.DASHBOARD],
        )

        self.authenticate(self.user)

        new_time = timezone.now() + timedelta(minutes=60)

        response = self.client.patch(
            reverse(
                "task-reminder-detail",
                args=[reminder.id],
            ),
            {
                "remind_at": new_time.isoformat(),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertNotEqual(
            response.data["notification_id"],
            str(reminder.notification_id),
        )

    def test_cancel_reminder(self):
        task = self.create_personal_task()

        reminder = create_task_reminder(
            task=task,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=[NotificationChannel.DASHBOARD],
        )

        self.authenticate(self.user)

        response = self.client.post(
            reverse(
                "task-reminder-cancel",
                args=[reminder.id],
            ),
            {"reason": "No longer needed."},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["state"],
            "CANCELLED",
        )

    def test_other_user_cannot_retrieve_reminder(
        self,
    ):
        task = self.create_personal_task()

        reminder = create_task_reminder(
            task=task,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=[NotificationChannel.DASHBOARD],
        )

        self.authenticate(self.other_user)

        response = self.client.get(
            reverse(
                "task-reminder-detail",
                args=[reminder.id],
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )
