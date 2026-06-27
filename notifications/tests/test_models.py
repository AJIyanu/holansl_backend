from datetime import time

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from notifications.constants import (
    NotificationChannel,
)
from notifications.models import (
    Notification,
    NotificationPreference,
    NotificationRecipient,
    NotificationTemplate,
)

from .base import User


class NotificationModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="model-user",
            email="model-user@holansl.com",
            password="TestPassword123!",
        )

        self.notification = Notification.objects.create(
            notification_type="general.message",
            category="general",
            title="Test",
            message="Test message",
        )

    def test_notification_recipient_is_unique_per_event_and_user(
        self,
    ):
        NotificationRecipient.objects.create(
            notification=self.notification,
            recipient=self.user,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            NotificationRecipient.objects.create(
                notification=self.notification,
                recipient=self.user,
            )

    def test_preference_requires_complete_quiet_hours(
        self,
    ):
        preference = NotificationPreference(
            user=self.user,
            quiet_hours_start=time(22, 0),
        )

        with self.assertRaises(ValidationError):
            preference.full_clean()

    def test_email_template_requires_subject(self):
        template = NotificationTemplate(
            key="test.email",
            name="Test email",
            channel=NotificationChannel.EMAIL,
            title_template="Test",
            body_text_template="Body",
        )

        with self.assertRaises(ValidationError):
            template.full_clean()
