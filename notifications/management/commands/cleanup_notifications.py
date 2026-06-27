from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from notifications.models import (
    Notification,
    NotificationRecipient,
)


class Command(BaseCommand):
    help = "Removes expired or long-archived notification inbox records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention-days",
            type=int,
            default=getattr(
                settings,
                "NOTIFICATION_RETENTION_DAYS",
                365,
            ),
        )

    def handle(self, *args, **options):
        retention_days = max(
            1,
            options["retention_days"],
        )

        cutoff = timezone.now() - timedelta(days=retention_days)

        recipient_count, _ = NotificationRecipient.objects.filter(
            Q(archived_at__lt=cutoff) | Q(dismissed_at__lt=cutoff)
        ).delete()

        notification_count, _ = (
            Notification.objects.filter(recipients__isnull=True)
            .filter(Q(expires_at__lt=timezone.now()) | Q(created_at__lt=cutoff))
            .delete()
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Notification cleanup completed: "
                f"recipient_rows={recipient_count}, "
                "notification_rows="
                f"{notification_count}."
            )
        )
