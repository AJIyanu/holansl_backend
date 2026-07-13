"""
Supabase private-bucket implementation of CRM document storage.
"""

from __future__ import annotations

import tempfile
from typing import BinaryIO
from urllib.parse import quote
from uuid import UUID

import requests
from django.conf import settings

from crm.models import PartyDocument

from .base import (
    DocumentStorageBackend,
    DocumentStorageError,
    DownloadedDocument,
    StoredDocument,
)


class SupabaseDocumentStorage(DocumentStorageBackend):
    """Store CRM documents inside a private Supabase Storage bucket."""

    def __init__(self) -> None:
        """
        Initialise Supabase Storage configuration.

        Raises:
            DocumentStorageError: If URL, key or bucket is missing.
        """

        base_url = getattr(
            settings,
            "SUPABASE_STORAGE_URL",
            "",
        ).rstrip("/")

        self.service_role_key = getattr(
            settings,
            "SUPABASE_STORAGE_SECRET_KEY",
            "",
        ).strip()

        self.bucket = getattr(
            settings,
            "SUPABASE_STORAGE_BUCKET",
            "",
        ).strip()

        self.timeout = getattr(
            settings,
            "SUPABASE_STORAGE_REQUEST_TIMEOUT_SECONDS",
            120,
        )

        if not base_url:
            raise DocumentStorageError("SUPABASE_STORAGE_URL is not configured.")

        if not self.service_role_key:
            raise DocumentStorageError("SUPABASE_STORAGE_SECRET_KEY is not configured.")

        if not self.bucket:
            raise DocumentStorageError("SUPABASE_STORAGE_BUCKET is not configured.")

        self.api_url = f"{base_url}/storage/v1"

    def _headers(
        self,
        *,
        content_type: str | None = None,
    ) -> dict[str, str]:
        """
        Build authenticated Supabase Storage request headers.

        Args:
            content_type: Optional MIME type for an upload request.

        Returns:
            dict[str, str]: HTTP headers containing service-role credentials.
        """

        headers = {
            "Authorization": (f"Bearer {self.service_role_key}"),
            "apikey": self.service_role_key,
        }

        if content_type:
            headers["Content-Type"] = content_type

        return headers

    def _object_url(
        self,
        path: str,
        *,
        authenticated: bool = False,
    ) -> str:
        """
        Build a Supabase object URL.

        Args:
            path: Object path inside the configured bucket.
            authenticated: Whether to use the private download endpoint.

        Returns:
            str: Fully qualified Storage API URL.
        """

        prefix = "object/authenticated" if authenticated else "object"

        return (
            f"{self.api_url}/{prefix}/"
            f"{quote(self.bucket, safe='')}/"
            f"{quote(path, safe='/')}"
        )

    def upload(
        self,
        *,
        document_id: UUID,
        party_id: UUID,
        category: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
        file_object: BinaryIO,
    ) -> StoredDocument:
        """
        Upload a file to the configured private bucket.

        Args:
            document_id: UUID reserved for the PartyDocument.
            party_id: CRM party UUID.
            category: CRM document category.
            filename: Sanitised filename.
            mime_type: File MIME type.
            size_bytes: Exact file size.
            file_object: Seekable binary stream.

        Returns:
            StoredDocument: Supabase object path and provider metadata.

        Raises:
            DocumentStorageError: If the upload fails.
        """

        object_path = f"parties/{party_id}/{category}/{document_id}/{filename}"

        file_object.seek(0)

        response = requests.post(
            self._object_url(object_path),
            headers={
                **self._headers(
                    content_type=mime_type,
                ),
                "x-upsert": "false",
                "Content-Length": str(size_bytes),
            },
            data=file_object,
            timeout=self.timeout,
        )

        if response.status_code not in {
            200,
            201,
        }:
            raise DocumentStorageError(
                "Supabase Storage upload failed: "
                f"{response.status_code} {response.text}"
            )

        return StoredDocument(
            provider=(PartyDocument.StorageProvider.SUPABASE),
            external_file_id=object_path,
            storage_path=object_path,
        )

    def download(
        self,
        *,
        external_file_id: str,
        storage_path: str,
        filename: str,
        mime_type: str,
    ) -> DownloadedDocument:
        """
        Download a private Supabase object.

        Args:
            external_file_id: Stored object path.
            storage_path: Preferred object path when available.
            filename: Filename returned to the API client.
            mime_type: Stored MIME type.

        Returns:
            DownloadedDocument: Temporary binary stream and metadata.

        Raises:
            DocumentStorageError: If the object cannot be downloaded.
        """

        object_path = storage_path or external_file_id

        response = requests.get(
            self._object_url(
                object_path,
                authenticated=True,
            ),
            headers=self._headers(),
            stream=True,
            timeout=self.timeout,
        )

        if not response.ok:
            raise DocumentStorageError(
                "Supabase Storage download failed: "
                f"{response.status_code} {response.text}"
            )

        temporary_file = tempfile.SpooledTemporaryFile(
            max_size=8 * 1024 * 1024,
            mode="w+b",
        )

        size_bytes = 0

        for chunk in response.iter_content(
            chunk_size=1024 * 1024,
        ):
            if chunk:
                temporary_file.write(chunk)
                size_bytes += len(chunk)

        temporary_file.seek(0)

        return DownloadedDocument(
            file_object=temporary_file,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
        )

    def delete(
        self,
        *,
        external_file_id: str,
        storage_path: str,
    ) -> None:
        """
        Delete a Supabase Storage object.

        Args:
            external_file_id: Stored object path.
            storage_path: Preferred object path when available.

        Returns:
            None.

        Raises:
            DocumentStorageError: If Supabase rejects deletion.
        """

        object_path = storage_path or external_file_id

        response = requests.delete(
            (f"{self.api_url}/object/{quote(self.bucket, safe='')}"),
            headers={
                **self._headers(
                    content_type="application/json",
                ),
            },
            json={
                "prefixes": [
                    object_path,
                ],
            },
            timeout=self.timeout,
        )

        if response.status_code not in {
            200,
            204,
            404,
        }:
            raise DocumentStorageError(
                "Supabase Storage deletion failed: "
                f"{response.status_code} {response.text}"
            )

    def check_configuration(self) -> dict:
        """
        Verify that the configured private bucket is accessible.

        Returns:
            dict: Provider and non-sensitive bucket details.

        Raises:
            DocumentStorageError: If the bucket cannot be read.
        """

        response = requests.get(
            (f"{self.api_url}/bucket/{quote(self.bucket, safe='')}"),
            headers=self._headers(),
            timeout=self.timeout,
        )

        if not response.ok:
            raise DocumentStorageError(
                "Supabase Storage bucket check failed: "
                f"{response.status_code} {response.text}"
            )

        payload = response.json()

        return {
            "provider": (PartyDocument.StorageProvider.SUPABASE),
            "bucket": payload.get(
                "name",
                self.bucket,
            ),
            "public": payload.get(
                "public",
                False,
            ),
        }
