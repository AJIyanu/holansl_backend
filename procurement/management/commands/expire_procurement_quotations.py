"""Record due quotation expiry events; schedule through the deployment operator."""

from django.core.management.base import BaseCommand
from django.utils import timezone
from procurement.models import ClientQuotationRevision
from procurement.services import expire_revision


class Command(BaseCommand):
    help = "Record quotation expiry once; --dry-run lists the count without changes."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        revisions = ClientQuotationRevision.objects.filter(
            expires_at__lte=timezone.now(),
            expired_recorded_at__isnull=True,
            issues__isnull=False,
        ).distinct()
        if options["dry_run"]:
            self.stdout.write(f"Due revisions: {revisions.count()}")
            return
        count = sum(expire_revision(revision) for revision in revisions.iterator())
        self.stdout.write(f"Recorded expiry for {count} revisions.")
