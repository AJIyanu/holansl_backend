from unittest.mock import Mock, patch

from django.test import override_settings
from django.utils import timezone

from notifications.constants import (
    DeliveryStatus,
    NotificationChannel,
    NotificationEventMode,
)
from notifications.data import (
    ProviderResult,
    RecipientSpec,
)
from notifications.exceptions import (
    TemporaryDeliveryError,
)
from notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationPreference,
    NotificationRecipient,
)
from notifications.services import notify
from notifications.services.delivery import (
    dispatch_delivery,
)

from .base import NotificationTestCase


@override_settings(
    NOTIFICATION_PROCESSING_MODE="outbox",
    NOTIFICATION_EMAIL_ENABLED=True,
    RESEND_API_KEY="test-key",
    RESEND_FROM_EMAIL=("HolanSL <noreply@holansl.com>"),
)
class NotificationServiceTests(NotificationTestCase):
    def setUp(self):
        self.user_one = self.create_user("user-one")

        self.user_two = self.create_user("user-two")

    def test_shared_mode_creates_one_event_and_many_recipients(
        self,
    ):
        result = notify(
            recipients=[
                self.user_one,
                self.user_two,
            ],
            notification_type="general.message",
            category="general",
            title="Test",
            message="Shared notification",
            channels=[
                NotificationChannel.DASHBOARD,
                NotificationChannel.EMAIL,
            ],
            event_mode=(NotificationEventMode.SHARED),
        )

        self.assertEqual(
            len(result.notification_ids),
            1,
        )

        self.assertEqual(
            Notification.objects.count(),
            1,
        )

        self.assertEqual(
            NotificationRecipient.objects.count(),
            2,
        )

        self.assertEqual(
            NotificationDelivery.objects.count(),
            4,
        )

    def test_individual_mode_creates_one_event_per_recipient(
        self,
    ):
        result = notify(
            recipients=[
                RecipientSpec(
                    user=self.user_one,
                    action_url="/one",
                ),
                RecipientSpec(
                    user=self.user_two,
                    action_url="/two",
                ),
            ],
            notification_type="general.message",
            category="general",
            title="Test",
            message="Individual notification",
            channels=[NotificationChannel.DASHBOARD],
            event_mode=(NotificationEventMode.INDIVIDUAL),
        )

        self.assertEqual(
            len(result.notification_ids),
            2,
        )

        self.assertEqual(
            Notification.objects.count(),
            2,
        )

        self.assertEqual(
            NotificationRecipient.objects.count(),
            2,
        )

    def test_deduplication_key_returns_existing_shared_event(
        self,
    ):
        first = notify(
            recipients=[self.user_one],
            notification_type="general.message",
            category="general",
            title="Test",
            message="Message",
            deduplication_key="same-event",
        )

        second = notify(
            recipients=[self.user_one],
            notification_type="general.message",
            category="general",
            title="Test",
            message="Message",
            deduplication_key="same-event",
        )

        self.assertTrue(first.created)
        self.assertFalse(second.created)

        self.assertEqual(
            Notification.objects.count(),
            1,
        )

    def test_preference_can_skip_email_but_keep_dashboard(
        self,
    ):
        NotificationPreference.objects.create(
            user=self.user_one,
            category="general",
            email_enabled=False,
        )

        notify(
            recipients=[self.user_one],
            notification_type="general.message",
            category="general",
            title="Test",
            message="Message",
            channels=[
                NotificationChannel.DASHBOARD,
                NotificationChannel.EMAIL,
            ],
        )

        dashboard = NotificationDelivery.objects.get(
            channel=(NotificationChannel.DASHBOARD)
        )

        email = NotificationDelivery.objects.get(channel=NotificationChannel.EMAIL)

        self.assertEqual(
            dashboard.status,
            DeliveryStatus.DELIVERED,
        )

        self.assertEqual(
            email.status,
            DeliveryStatus.SKIPPED,
        )

        self.assertEqual(
            email.error_code,
            "disabled_by_preference",
        )

    @patch("notifications.services.delivery.get_provider")
    def test_dispatch_delivery_records_provider_success(
        self,
        get_provider,
    ):
        notify(
            recipients=[self.user_one],
            notification_type="general.message",
            category="general",
            title="Test",
            message="Message",
            channels=[NotificationChannel.EMAIL],
        )

        delivery = NotificationDelivery.objects.get()

        provider = Mock()

        provider.send.return_value = ProviderResult(
            status=DeliveryStatus.SENT,
            provider_message_id="email-123",
        )

        get_provider.return_value = provider

        self.assertTrue(dispatch_delivery(delivery.id))

        delivery.refresh_from_db()

        self.assertEqual(
            delivery.status,
            DeliveryStatus.SENT,
        )

        self.assertEqual(
            delivery.provider_message_id,
            "email-123",
        )

        self.assertEqual(
            delivery.attempt_count,
            1,
        )

    @patch("notifications.services.delivery.get_provider")
    def test_temporary_failure_is_scheduled_for_retry(
        self,
        get_provider,
    ):
        notify(
            recipients=[self.user_one],
            notification_type="general.message",
            category="general",
            title="Test",
            message="Message",
            channels=[NotificationChannel.EMAIL],
        )

        delivery = NotificationDelivery.objects.get()

        provider = Mock()

        provider.send.side_effect = TemporaryDeliveryError("Temporary outage")

        get_provider.return_value = provider

        self.assertFalse(dispatch_delivery(delivery.id))

        delivery.refresh_from_db()

        self.assertEqual(
            delivery.status,
            DeliveryStatus.RETRYING,
        )

        self.assertGreater(
            delivery.next_attempt_at,
            timezone.now(),
        )
