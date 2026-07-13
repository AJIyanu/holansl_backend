"""
Tests for CRM sensitive records and provider-independent document storage.
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import (
    SimpleUploadedFile,
)
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import (
    Party,
    PartyBankAccount,
    PartyDocument,
    PartyIdentifier,
)
from .storage.base import (
    DownloadedDocument,
    StoredDocument,
)


TEST_ENCRYPTION_KEY = Fernet.generate_key().decode(
    "utf-8",
)


class FakeDocumentStorage:
    """Provide a deterministic in-memory storage provider for API tests."""

    def __init__(self):
        """Initialise operation tracking."""

        self.uploaded = []
        self.deleted = []

    def upload(
        self,
        *,
        document_id,
        party_id,
        category,
        filename,
        mime_type,
        size_bytes,
        file_object,
    ):
        """
        Record a test upload.

        Args:
            document_id: Reserved document UUID.
            party_id: Party UUID.
            category: Document category.
            filename: Uploaded filename.
            mime_type: Uploaded MIME type.
            size_bytes: Uploaded size.
            file_object: Binary upload stream.

        Returns:
            StoredDocument: Deterministic fake provider result.
        """

        self.uploaded.append(
            {
                "document_id": document_id,
                "party_id": party_id,
                "filename": filename,
                "size_bytes": size_bytes,
            }
        )

        return StoredDocument(
            provider=(PartyDocument.StorageProvider.SUPABASE),
            external_file_id=(f"fake/{document_id}/{filename}"),
            storage_path=(f"fake/{document_id}/{filename}"),
        )

    def download(
        self,
        *,
        external_file_id,
        storage_path,
        filename,
        mime_type,
    ):
        """
        Return deterministic test content.

        Args:
            external_file_id: Fake provider identifier.
            storage_path: Fake provider path.
            filename: Download filename.
            mime_type: Download MIME type.

        Returns:
            DownloadedDocument: In-memory test document.
        """

        content = b"test document content"

        return DownloadedDocument(
            file_object=BytesIO(content),
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
        )

    def delete(
        self,
        *,
        external_file_id,
        storage_path,
    ):
        """
        Record a test deletion.

        Args:
            external_file_id: Fake provider identifier.
            storage_path: Fake provider path.

        Returns:
            None.
        """

        self.deleted.append(
            external_file_id,
        )

    def check_configuration(self):
        """
        Return successful fake-provider status.

        Returns:
            dict: Fake provider details.
        """

        return {
            "provider": "FAKE",
        }


@override_settings(
    CRM_FIELD_ENCRYPTION_KEYS=[
        TEST_ENCRYPTION_KEY,
    ],
    CRM_SENSITIVE_HASH_KEY=("test-only-sensitive-hash-key"),
    CRM_DOCUMENT_MAX_SIZE_BYTES=(1024 * 1024),
    CRM_DOCUMENT_ALLOWED_MIME_TYPES={
        "application/pdf",
        "text/plain",
    },
    CRM_NOTIFICATION_CHANNELS=[
        "DASHBOARD",
    ],
)
class CRMSensitiveDocumentAPITests(APITestCase):
    """Verify encryption, permissions and storage orchestration."""

    def setUp(self):
        """Create users, permissions, party and fake storage."""

        User = get_user_model()

        self.user = User.objects.create_user(
            username="crm.secure.user",
            email="crm.secure.user@holansl.com",
            password="TestPassword123!",
        )

        self.superuser = User.objects.create_superuser(
            username="crm.secure.admin",
            email="crm.secure.admin@holansl.com",
            password="TestPassword123!",
        )

        self.party = Party.objects.create(
            display_name="Secure CRM Party",
            entity_kind=(Party.EntityKind.ORGANISATION),
        )

        self.fake_storage = FakeDocumentStorage()

        self.storage_patch = patch(
            ("crm.document_services.get_document_storage"),
            return_value=self.fake_storage,
        )

        self.storage_patch.start()
        self.addCleanup(
            self.storage_patch.stop,
        )

        self.client.force_authenticate(
            self.user,
        )

    def _grant_permissions(
        self,
        *codenames,
    ):
        """
        Grant CRM permissions to the ordinary test user.

        Args:
            *codenames: CRM permission codenames.

        Returns:
            None.
        """

        permissions = Permission.objects.filter(
            content_type__app_label="crm",
            codename__in=codenames,
        )

        self.user.user_permissions.add(
            *permissions,
        )

        if hasattr(
            self.user,
            "_perm_cache",
        ):
            del self.user._perm_cache

    def test_identifier_is_encrypted_and_masked(self):
        """Confirm ordinary responses never include identifier plaintext."""

        self._grant_permissions(
            "add_partyidentifier",
            "view_partyidentifier",
            "manage_sensitive_partyidentifier",
        )

        response = self.client.post(
            reverse(
                "party-identifier-list",
            ),
            {
                "party": str(self.party.id),
                "identifier_type": "TAX_ID",
                "label": "Nigeria TIN",
                "value": "1234-5678-9012",
                "issuing_country": "NG",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )

        identifier = PartyIdentifier.objects.get(
            pk=response.data["id"],
        )

        self.assertNotIn(
            "1234-5678-9012",
            identifier.encrypted_value,
        )

        self.assertEqual(
            response.data["masked_value"],
            "••••9012",
        )

        self.assertNotIn(
            "value",
            response.data,
        )

    def test_identifier_reveal_requires_separate_permission(
        self,
    ):
        """Confirm plaintext reveal uses its own permission."""

        identifier = PartyIdentifier(
            party=self.party,
            identifier_type=(PartyIdentifier.IdentifierType.TAX_ID),
        )
        identifier.set_value(
            "TIN-99887766",
        )
        identifier.save()

        self._grant_permissions(
            "view_partyidentifier",
        )

        denied = self.client.get(
            reverse(
                "party-identifier-reveal",
                args=[
                    identifier.id,
                ],
            )
        )

        self.assertEqual(
            denied.status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self._grant_permissions(
            "view_sensitive_partyidentifier",
        )

        allowed = self.client.get(
            reverse(
                "party-identifier-reveal",
                args=[
                    identifier.id,
                ],
            )
        )

        self.assertEqual(
            allowed.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            allowed.data["value"],
            "TIN-99887766",
        )

    def test_bank_account_is_encrypted(self):
        """Confirm payment values are encrypted at rest."""

        self._grant_permissions(
            "add_partybankaccount",
            "view_partybankaccount",
            "manage_sensitive_partybankaccount",
        )

        with self.captureOnCommitCallbacks(
            execute=True,
        ):
            with patch(
                "crm.views.dispatch_bank_account_change",
            ):
                response = self.client.post(
                    reverse(
                        "party-bank-account-list",
                    ),
                    {
                        "party": str(
                            self.party.id,
                        ),
                        "payment_method": ("BANK_TRANSFER"),
                        "account_name": ("Secure CRM Party"),
                        "bank_name": "Test Bank",
                        "account_number": ("0123456789"),
                        "currency": "NGN",
                        "country_code": "NG",
                    },
                    format="json",
                )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )

        account = PartyBankAccount.objects.get(
            pk=response.data["id"],
        )

        self.assertNotIn(
            "0123456789",
            account.encrypted_account_number,
        )

        self.assertEqual(
            response.data["masked_account_number"],
            "••••6789",
        )

    def test_document_upload_uses_storage_service(self):
        """Confirm document metadata is created after provider upload."""

        self._grant_permissions(
            "add_partydocument",
            "view_partydocument",
        )

        uploaded_file = SimpleUploadedFile(
            "certificate.pdf",
            b"%PDF-test-content",
            content_type="application/pdf",
        )

        response = self.client.post(
            reverse(
                "party-document-list",
            ),
            {
                "party": str(self.party.id),
                "category": "REGISTRATION",
                "description": ("Registration certificate"),
                "file": uploaded_file,
            },
            format="multipart",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )

        document = PartyDocument.objects.get(
            pk=response.data["id"],
        )

        self.assertEqual(
            document.storage_provider,
            PartyDocument.StorageProvider.SUPABASE,
        )

        self.assertEqual(
            len(self.fake_storage.uploaded),
            1,
        )

    def test_confidential_document_is_hidden(self):
        """Confirm confidential documents are filtered from ordinary readers."""

        self._grant_permissions(
            "view_partydocument",
        )

        PartyDocument.objects.create(
            party=self.party,
            category=PartyDocument.Category.BANK,
            original_filename="bank.pdf",
            mime_type="application/pdf",
            size_bytes=100,
            checksum_sha256="a" * 64,
            storage_provider=(PartyDocument.StorageProvider.SUPABASE),
            external_file_id="bank.pdf",
            storage_path="bank.pdf",
            is_confidential=True,
        )

        response = self.client.get(
            reverse(
                "party-document-list",
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["count"],
            0,
        )

    def test_document_download_requires_download_permission(
        self,
    ):
        """Confirm document reading and downloading use different permissions."""

        document = PartyDocument.objects.create(
            party=self.party,
            category=PartyDocument.Category.CONTRACT,
            original_filename="contract.pdf",
            mime_type="application/pdf",
            size_bytes=100,
            checksum_sha256="b" * 64,
            storage_provider=(PartyDocument.StorageProvider.SUPABASE),
            external_file_id="contract.pdf",
            storage_path="contract.pdf",
        )

        self._grant_permissions(
            "view_partydocument",
        )

        denied = self.client.get(
            reverse(
                "party-document-download",
                args=[
                    document.id,
                ],
            )
        )

        self.assertEqual(
            denied.status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self._grant_permissions(
            "download_partydocument",
        )

        allowed = self.client.get(
            reverse(
                "party-document-download",
                args=[
                    document.id,
                ],
            )
        )

        self.assertEqual(
            allowed.status_code,
            status.HTTP_200_OK,
        )

    def test_document_delete_soft_deletes_metadata(
        self,
    ):
        """Confirm remote deletion retains an inactive database record."""

        document = PartyDocument.objects.create(
            party=self.party,
            category=PartyDocument.Category.OTHER,
            original_filename="record.txt",
            mime_type="text/plain",
            size_bytes=10,
            checksum_sha256="c" * 64,
            storage_provider=(PartyDocument.StorageProvider.SUPABASE),
            external_file_id="record.txt",
            storage_path="record.txt",
        )

        self._grant_permissions(
            "view_partydocument",
            "delete_partydocument",
        )

        response = self.client.delete(
            reverse(
                "party-document-detail",
                args=[
                    document.id,
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_204_NO_CONTENT,
        )

        document.refresh_from_db()

        self.assertFalse(
            document.is_active,
        )

        self.assertIsNotNone(
            document.deleted_at,
        )

        self.assertEqual(
            self.fake_storage.deleted,
            [
                "record.txt",
            ],
        )
