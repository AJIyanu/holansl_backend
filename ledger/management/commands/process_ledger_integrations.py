from django.core.management.base import BaseCommand
from ledger.models import IntegrationIntent
from ledger.integrations.workflow import process_one


class Command(BaseCommand):
    help = "Retry committed ledger approval tasks/notifications; use the configured deployment scheduler."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=50)

    def handle(self, *args, **options):
        limit = options["batch_size"]
        if not 1 <= limit <= 500:
            from django.core.management.base import CommandError

            raise CommandError("batch-size must be between 1 and 500")
        keys = list(
            IntegrationIntent.objects.filter(processed_at=None)
            .order_by("created_at")
            .values_list("pk", flat=True)[:limit]
        )
        for pk in keys:
            process_one(pk)
        self.stdout.write(f"Processed {len(keys)} pending workflow attempts.")
