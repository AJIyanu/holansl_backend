from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from django.test import SimpleTestCase, override_settings

from crm.document_services import download_party_document
from crm.storage import (
    DocumentStorageError,
    clear_document_storage_cache,
    get_document_storage,
)
from crm.storage.supabase import SupabaseDocumentStorage


@override_settings(
    CRM_DOCUMENT_STORAGE_PROVIDER="supabase",
    SUPABASE_STORAGE_URL="https://storage.example.invalid",
    SUPABASE_STORAGE_SECRET_KEY="test-secret",
    SUPABASE_STORAGE_BUCKET="private-documents",
)
class StorageRoutingTests(SimpleTestCase):
    def setUp(self):
        clear_document_storage_cache()
        self.addCleanup(clear_document_storage_cache)

    def test_default_and_explicit_legacy_provider(self):
        with patch("crm.storage.factory.GoogleDriveDocumentStorage") as drive:
            self.assertIsInstance(get_document_storage(), SupabaseDocumentStorage)
            self.assertIs(get_document_storage("GOOGLE_DRIVE"), drive.return_value)
            self.assertIsInstance(
                get_document_storage("SUPABASE"), SupabaseDocumentStorage
            )

    def test_no_fallback_when_supabase_is_unconfigured(self):
        with (
            override_settings(SUPABASE_STORAGE_SECRET_KEY=""),
            patch("crm.storage.factory.GoogleDriveDocumentStorage") as drive,
        ):
            with self.assertRaises(DocumentStorageError):
                get_document_storage()
            drive.assert_not_called()

    def test_unknown_record_provider_fails_closed(self):
        with self.assertRaises(DocumentStorageError):
            get_document_storage("UNKNOWN")

    @patch("crm.document_services.log_crm_event")
    @patch("crm.document_services.get_document_storage")
    def test_download_uses_recorded_provider(self, factory, audit):
        for provider in ("GOOGLE_DRIVE", "SUPABASE"):
            document = SimpleNamespace(
                is_active=True,
                storage_provider=provider,
                external_file_id="old-id",
                storage_path="old-path",
                original_filename="doc.pdf",
                mime_type="application/pdf",
                id=uuid4(),
                party_id=uuid4(),
            )
            download_party_document(document=document, user=None)
            factory.assert_called_with(provider)

    @patch("crm.storage.supabase.requests.get")
    @patch("crm.storage.supabase.requests.post")
    def test_public_or_unknown_bucket_never_uploads(self, post, get):
        for payload in ({"public": True}, {}, []):
            get.return_value = Mock(ok=True, json=Mock(return_value=payload))
            with self.assertRaises(DocumentStorageError):
                self.upload()
        post.assert_not_called()

    def upload(self):
        return SupabaseDocumentStorage().upload(
            document_id=uuid4(),
            party_id=uuid4(),
            category="OTHER",
            filename="doc.pdf",
            mime_type="application/pdf",
            size_bytes=4,
            file_object=BytesIO(b"data"),
        )

    @patch("crm.storage.supabase.requests.get")
    @patch("crm.storage.supabase.requests.post")
    def test_private_upload_no_overwrite_or_public_url(self, post, get):
        get.return_value = Mock(ok=True, json=Mock(return_value={"public": False}))
        post.return_value = Mock(status_code=201)
        stored = self.upload()
        self.assertEqual(stored.provider, "SUPABASE")
        self.assertEqual(post.call_args.kwargs["headers"]["x-upsert"], "false")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    @patch(
        "crm.storage.supabase.requests.get",
        side_effect=requests.Timeout("secret-provider-detail"),
    )
    def test_network_failure_is_sanitized(self, get):
        with self.assertRaises(DocumentStorageError) as caught:
            SupabaseDocumentStorage().check_configuration()
        self.assertNotIn("secret-provider-detail", str(caught.exception))

    @patch("crm.storage.supabase.requests.get")
    def test_download_uses_authenticated_endpoint_after_privacy_check(self, get):
        get.side_effect = [
            Mock(ok=True, json=Mock(return_value={"public": False})),
            Mock(ok=True, iter_content=Mock(return_value=[b"data"])),
        ]
        result = SupabaseDocumentStorage().download(
            external_file_id="doc.pdf",
            storage_path="doc.pdf",
            filename="doc.pdf",
            mime_type="application/pdf",
        )
        self.addCleanup(result.file_object.close)
        self.assertEqual(result.file_object.read(), b"data")
        self.assertIn("/object/authenticated/", get.call_args.args[0])

    @patch("crm.storage.supabase.requests.get")
    def test_public_bucket_download_never_requests_object(self, get):
        get.return_value = Mock(ok=True, json=Mock(return_value={"public": True}))
        with self.assertRaises(DocumentStorageError):
            SupabaseDocumentStorage().download(
                external_file_id="doc.pdf",
                storage_path="doc.pdf",
                filename="doc.pdf",
                mime_type="application/pdf",
            )
        get.assert_called_once()
