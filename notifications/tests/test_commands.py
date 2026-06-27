from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase


class NotificationCommandTests(TestCase):
    @patch(
        "notifications.management.commands."
        "process_notification_deliveries."
        "process_due_deliveries"
    )
    def test_process_delivery_command(
        self,
        process_due,
    ):
        process_due.return_value = {
            "selected": 0,
            "processed": 0,
            "succeeded": 0,
            "failed_or_deferred": 0,
        }

        output = StringIO()

        call_command(
            "process_notification_deliveries",
            batch_size=25,
            stdout=output,
        )

        self.assertIn(
            "completed",
            output.getvalue().lower(),
        )

        process_due.assert_called_once_with(batch_size=25)
