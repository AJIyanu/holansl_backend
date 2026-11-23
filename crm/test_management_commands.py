from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from crm.storage.base import DocumentStorageError


class VerifySupabaseStorageCommandTests(SimpleTestCase):
    @patch("crm.management.commands.verify_supabase_storage.get_document_storage")
    def test_reports_private_bucket(self, get_document_storage):
        storage = Mock()
        storage.check_configuration.return_value = {
            "provider": "SUPABASE",
            "bucket": "crm-documents",
            "public": False,
        }
        get_document_storage.return_value = storage
        stdout = StringIO()

        call_command("verify_supabase_storage", stdout=stdout)

        get_document_storage.assert_called_once_with("supabase")
        self.assertIn("crm-documents is private", stdout.getvalue())

    @patch("crm.management.commands.verify_supabase_storage.get_document_storage")
    def test_fails_when_storage_cannot_be_verified(self, get_document_storage):
        storage = Mock()
        storage.check_configuration.side_effect = DocumentStorageError(
            "Supabase Storage bucket must be private."
        )
        get_document_storage.return_value = storage

        with self.assertRaisesMessage(CommandError, "bucket must be private"):
            call_command("verify_supabase_storage")
