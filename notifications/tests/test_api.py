from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse

from notifications.constants import (
    NotificationChannel,
)
from notifications.services import notify

from .base import NotificationTestCase


@override_settings(NOTIFICATION_PROCESSING_MODE="outbox")
class NotificationApiTests(NotificationTestCase):
    def setUp(self):
        self.user_one = self.create_user("api-one")

        self.user_two = self.create_user("api-two")

        self.admin = self.create_user(
            "api-admin",
            superuser=True,
        )

        notify(
            recipients=[self.user_one],
            notification_type="general.message",
            category="general",
            title="User one",
            message=("Only user one should see this."),
            channels=[NotificationChannel.DASHBOARD],
        )

        notify(
            recipients=[self.user_two],
            notification_type="general.message",
            category="general",
            title="User two",
            message=("Only user two should see this."),
            channels=[NotificationChannel.DASHBOARD],
        )

    def test_inbox_is_row_level_scoped_to_authenticated_user(
        self,
    ):
        self.client.force_authenticate(self.user_one)

        response = self.client.get(reverse("notification-inbox-list"))

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data["count"],
            1,
        )

        self.assertEqual(
            response.data["results"][0]["title"],
            "User one",
        )

    def test_user_can_mark_own_notification_read(
        self,
    ):
        self.client.force_authenticate(self.user_one)

        list_response = self.client.get(reverse("notification-inbox-list"))

        recipient_id = list_response.data["results"][0]["id"]

        response = self.client.post(
            reverse(
                "notification-inbox-read",
                args=[recipient_id],
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertTrue(response.data["is_read"])

    def test_superuser_can_dispatch_notification(
        self,
    ):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            reverse("notification-dispatch"),
            {
                "recipient_ids": [str(self.user_one.id)],
                "notification_type": "general.message",
                "category": "general",
                "title": "Admin message",
                "message": ("A message from administration."),
                "channels": ["DASHBOARD"],
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            201,
        )

        self.assertEqual(
            response.data["notifications_created"],
            1,
        )

    @override_settings(
        NOTIFICATION_CRON_SECRET="cron-secret",
        NOTIFICATION_PROCESSING_BATCH_SIZE=100,
    )
    @patch("notifications.views.process_due_deliveries")
    def test_internal_processor_requires_valid_secret(
        self,
        process_due,
    ):
        process_due.return_value = {
            "selected": 0,
            "processed": 0,
            "succeeded": 0,
            "failed_or_deferred": 0,
        }

        self.client.force_authenticate(user=None)

        denied = self.client.post(reverse("notification-process-deliveries"))

        self.assertEqual(
            denied.status_code,
            403,
        )

        allowed = self.client.post(
            reverse("notification-process-deliveries"),
            HTTP_X_NOTIFICATION_CRON_SECRET=("cron-secret"),
        )

        self.assertEqual(
            allowed.status_code,
            200,
        )

        process_due.assert_called_once_with(batch_size=100)
