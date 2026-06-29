from datetime import timedelta

from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.test import override_settings
from django.utils import timezone

from notifications.constants import (
    DeliveryStatus,
    NotificationChannel,
)
from notifications.models import (
    NotificationDelivery,
)

from tasks.constants import (
    TaskAssignmentType,
    TaskStatus,
)
from tasks.services.assignments import (
    create_task_assignment,
)
from tasks.services.lifecycle import (
    transition_task_status,
)
from tasks.services.reminders import (
    cancel_task_reminder,
    create_task_reminder,
    get_reminder_capabilities,
    update_task_reminder,
)

from .base import TaskModelTestCase


@override_settings(
    TASK_REMINDERS_ENABLED=True,
    TASK_DASHBOARD_REMINDERS_ENABLED=True,
    TASK_SCHEDULED_EXTERNAL_DELIVERY_ENABLED=False,
    NOTIFICATION_PROCESSING_MODE="outbox",
)
class TaskReminderServiceTests(TaskModelTestCase):
    def test_capabilities_allow_dashboard_only(self):
        capabilities = get_reminder_capabilities()

        self.assertTrue(capabilities["channels"]["DASHBOARD"]["available"])

        self.assertFalse(capabilities["channels"]["EMAIL"]["available"])

        self.assertFalse(capabilities["channels"]["WHATSAPP"]["available"])

    def test_owner_can_create_dashboard_reminder(
        self,
    ):
        task = self.create_personal_task()

        reminder = create_task_reminder(
            task=task,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=[NotificationChannel.DASHBOARD],
        )

        self.assertEqual(
            reminder.user,
            self.user,
        )

        self.assertIsNotNone(reminder.notification)

        self.assertEqual(
            reminder.notification.scheduled_at,
            reminder.remind_at,
        )

        delivery = NotificationDelivery.objects.get(
            notification_recipient__notification=reminder.notification
        )

        self.assertEqual(
            delivery.channel,
            NotificationChannel.DASHBOARD,
        )

        self.assertEqual(
            delivery.status,
            DeliveryStatus.DELIVERED,
        )

    def test_email_rejected_without_scheduler(
        self,
    ):
        task = self.create_personal_task()

        with self.assertRaises(ValidationError):
            create_task_reminder(
                task=task,
                user=self.user,
                remind_at=(timezone.now() + timedelta(minutes=30)),
                channels=[NotificationChannel.EMAIL],
            )

    def test_reminder_cannot_be_after_due_time(
        self,
    ):
        task = self.create_personal_task()

        with self.assertRaises(ValidationError):
            create_task_reminder(
                task=task,
                user=self.user,
                remind_at=(task.batch.due_at + timedelta(minutes=1)),
                channels=[NotificationChannel.DASHBOARD],
            )

    def test_reminder_cannot_be_created_for_assigned_task(
        self,
    ):
        manager = self.create_staff(
            username="reminder-manager",
            department=self.department,
        )

        self.create_department_leader(profile=manager)

        self.grant_permission(
            manager.user,
            "assign_task",
        )

        result = create_task_assignment(
            creator=manager.user,
            assignment_type=(TaskAssignmentType.USERS),
            title="Assigned task",
            user_ids=[self.user.id],
        )

        with self.assertRaises(ValidationError):
            create_task_reminder(
                task=result.tasks[0],
                user=self.user,
                remind_at=(timezone.now() + timedelta(minutes=30)),
                channels=[NotificationChannel.DASHBOARD],
            )

    def test_other_user_cannot_create_reminder(
        self,
    ):
        task = self.create_personal_task()

        with self.assertRaises(PermissionDenied):
            create_task_reminder(
                task=task,
                user=self.other_user,
                remind_at=(timezone.now() + timedelta(minutes=30)),
                channels=[NotificationChannel.DASHBOARD],
            )

    def test_reminder_can_be_rescheduled(
        self,
    ):
        task = self.create_personal_task()

        reminder = create_task_reminder(
            task=task,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=[NotificationChannel.DASHBOARD],
        )

        old_notification = reminder.notification

        updated = update_task_reminder(
            reminder=reminder,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=60)),
        )

        old_notification.refresh_from_db()

        self.assertIsNotNone(old_notification.expires_at)

        self.assertNotEqual(
            updated.notification_id,
            old_notification.id,
        )

    def test_reminder_can_be_cancelled(
        self,
    ):
        task = self.create_personal_task()

        reminder = create_task_reminder(
            task=task,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=[NotificationChannel.DASHBOARD],
        )

        cancelled = cancel_task_reminder(
            reminder=reminder,
            user=self.user,
            reason="No longer needed.",
        )

        self.assertIsNotNone(cancelled.cancelled_at)

        cancelled.notification.refresh_from_db()

        self.assertIsNotNone(cancelled.notification.expires_at)

    def test_completing_task_cancels_reminder(
        self,
    ):
        task = self.create_personal_task()

        reminder = create_task_reminder(
            task=task,
            user=self.user,
            remind_at=(timezone.now() + timedelta(minutes=30)),
            channels=[NotificationChannel.DASHBOARD],
        )

        transition_task_status(
            task=task,
            actor=self.user,
            new_status=TaskStatus.COMPLETED,
        )

        reminder.refresh_from_db()

        self.assertIsNotNone(reminder.cancelled_at)
