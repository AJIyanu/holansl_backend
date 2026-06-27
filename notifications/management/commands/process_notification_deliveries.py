from django.core.management.base import BaseCommand

from notifications.services.delivery import (
    process_due_deliveries,
)


class Command(BaseCommand):
    help = "Processes pending and retrying notification outbox deliveries."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
        )

        parser.add_argument(
            "--max-batches",
            type=int,
            default=1,
            help=("Maximum number of batches to process in this run."),
        )

    def handle(self, *args, **options):
        batch_size = max(
            1,
            min(options["batch_size"], 500),
        )

        max_batches = max(
            1,
            options["max_batches"],
        )

        totals = {
            "selected": 0,
            "processed": 0,
            "succeeded": 0,
            "failed_or_deferred": 0,
        }

        for _index in range(max_batches):
            result = process_due_deliveries(batch_size=batch_size)

            for key in totals:
                totals[key] += result[key]

            if result["selected"] < batch_size:
                break

        self.stdout.write(
            self.style.SUCCESS(
                "Notification delivery processing "
                "completed: "
                f"selected={totals['selected']}, "
                f"processed={totals['processed']}, "
                f"succeeded={totals['succeeded']}, "
                "failed_or_deferred="
                f"{totals['failed_or_deferred']}."
            )
        )
