import json
from django.core.management.base import BaseCommand
from ledger.tax_backfill import preview, apply_links


class Command(BaseCommand):
    help = "Preview historical tax links and delivery gaps; --apply adds tax links without reposting cash/journals."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply historical links only after operator review.",
        )

    def handle(self, *args, **options):
        if options["apply"]:
            self.stdout.write(f"Created {apply_links()} historical tax links.")
        self.stdout.write(json.dumps(preview(), indent=2))
