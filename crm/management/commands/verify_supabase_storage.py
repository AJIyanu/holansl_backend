from django.core.management.base import BaseCommand, CommandError

from crm.storage.base import DocumentStorageError
from crm.storage.factory import get_document_storage


class Command(BaseCommand):
    help = (
        "Verifies that the configured Supabase document bucket exists and is private."
    )

    def handle(self, *args, **options):
        try:
            details = get_document_storage("supabase").check_configuration()
        except DocumentStorageError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Supabase Storage verified: {details['bucket']} is private."
            )
        )
