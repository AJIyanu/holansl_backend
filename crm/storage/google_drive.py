"""
Google Drive implementation of the CRM document-storage interface.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from django.conf import settings
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from crm.models import PartyDocument

from .base import (
    DocumentStorageBackend,
    DocumentStorageError,
    DownloadedDocument,
    StoredDocument,
)


class GoogleDriveDocumentStorage(DocumentStorageBackend):
    """Store CRM documents in a configured Google Drive root folder."""

    DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
    DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
    FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
    SCOPES = [
        "https://www.googleapis.com/auth/drive",
    ]

    def __init__(self) -> None:
        """
        Initialise provider configuration.

        Raises:
            DocumentStorageError: If the root folder is not configured.
        """

        self.root_folder_id = getattr(
            settings,
            "GOOGLE_DRIVE_ROOT_FOLDER_ID",
            "",
        ).strip()

        self.timeout = getattr(
            settings,
            "GOOGLE_DRIVE_REQUEST_TIMEOUT_SECONDS",
            120,
        )

        if not self.root_folder_id:
            raise DocumentStorageError("GOOGLE_DRIVE_ROOT_FOLDER_ID is not configured.")

    def _load_credentials_info(self) -> dict:
        """
        Load service-account credentials from JSON or a configured file.

        Returns:
            dict: Parsed Google service-account credential information.

        Raises:
            DocumentStorageError: If credentials are missing or invalid.
        """

        raw_json = getattr(
            settings,
            "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON",
            "",
        ).strip()

        credential_file = getattr(
            settings,
            "GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE",
            "",
        ).strip()

        try:
            if raw_json:
                return json.loads(raw_json)

            if credential_file:
                return json.loads(
                    Path(credential_file).read_text(
                        encoding="utf-8",
                    )
                )
        except (
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise DocumentStorageError(
                "Google Drive service-account credentials could not be loaded."
            ) from exc

        raise DocumentStorageError(
            "Configure GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON "
            "or GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE."
        )

    def _session(self) -> AuthorizedSession:
        """
        Create an authorised Google API HTTP session.

        Returns:
            AuthorizedSession: Authenticated Drive API session.

        Raises:
            DocumentStorageError: If credentials cannot be created.
        """

        try:
            credentials = service_account.Credentials.from_service_account_info(
                self._load_credentials_info(),
                scopes=self.SCOPES,
            )
        except (TypeError, ValueError) as exc:
            raise DocumentStorageError("Google Drive credentials are invalid.") from exc

        return AuthorizedSession(credentials)

    @staticmethod
    def _escape_query_value(value: str) -> str:
        """
        Escape a value used in a Google Drive search expression.

        Args:
            value: Raw query value.

        Returns:
            str: Escaped query-safe value.
        """

        return value.replace(
            "\\",
            "\\\\",
        ).replace(
            "'",
            "\\'",
        )

    def _find_folder(
        self,
        *,
        session: AuthorizedSession,
        parent_id: str,
        folder_name: str,
    ) -> str | None:
        """
        Find a named child folder under a parent folder.

        Args:
            session: Authorised Google API session.
            parent_id: Parent Drive folder ID.
            folder_name: Exact child folder name.

        Returns:
            str | None: Matching folder ID or None.

        Raises:
            DocumentStorageError: If the Drive search fails.
        """

        escaped_parent = self._escape_query_value(
            parent_id,
        )
        escaped_name = self._escape_query_value(
            folder_name,
        )

        query = (
            f"'{escaped_parent}' in parents and "
            f"name = '{escaped_name}' and "
            f"mimeType = '{self.FOLDER_MIME_TYPE}' and "
            "trashed = false"
        )

        response = session.get(
            self.DRIVE_FILES_URL,
            params={
                "q": query,
                "fields": "files(id,name)",
                "pageSize": 10,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
            timeout=self.timeout,
        )

        if not response.ok:
            raise DocumentStorageError(
                "Google Drive folder search failed: "
                f"{response.status_code} {response.text}"
            )

        files = response.json().get(
            "files",
            [],
        )

        return files[0]["id"] if files else None

    def _create_folder(
        self,
        *,
        session: AuthorizedSession,
        parent_id: str,
        folder_name: str,
    ) -> str:
        """
        Create a Drive folder under the supplied parent.

        Args:
            session: Authorised Google API session.
            parent_id: Parent Drive folder ID.
            folder_name: Folder name to create.

        Returns:
            str: Newly created folder ID.

        Raises:
            DocumentStorageError: If folder creation fails.
        """

        response = session.post(
            self.DRIVE_FILES_URL,
            params={
                "supportsAllDrives": "true",
                "fields": "id,name",
            },
            json={
                "name": folder_name,
                "mimeType": self.FOLDER_MIME_TYPE,
                "parents": [
                    parent_id,
                ],
            },
            timeout=self.timeout,
        )

        if not response.ok:
            raise DocumentStorageError(
                "Google Drive folder creation failed: "
                f"{response.status_code} {response.text}"
            )

        return response.json()["id"]

    def _ensure_folder(
        self,
        *,
        session: AuthorizedSession,
        parent_id: str,
        folder_name: str,
    ) -> str:
        """
        Return an existing folder or create it.

        Args:
            session: Authorised Google API session.
            parent_id: Parent Drive folder ID.
            folder_name: Child folder name.

        Returns:
            str: Existing or newly created folder ID.
        """

        folder_id = self._find_folder(
            session=session,
            parent_id=parent_id,
            folder_name=folder_name,
        )

        if folder_id:
            return folder_id

        return self._create_folder(
            session=session,
            parent_id=parent_id,
            folder_name=folder_name,
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
        Upload a file through a Google Drive resumable-upload session.

        Args:
            document_id: UUID reserved for the PartyDocument.
            party_id: Party UUID used as the stable folder name.
            category: CRM document category.
            filename: Sanitised filename.
            mime_type: MIME type sent to Drive.
            size_bytes: Exact file length.
            file_object: Seekable binary stream.

        Returns:
            StoredDocument: Drive file, folder and logical path identifiers.

        Raises:
            DocumentStorageError: If folder creation or upload fails.
        """

        session = self._session()

        party_folder_id = self._ensure_folder(
            session=session,
            parent_id=self.root_folder_id,
            folder_name=str(party_id),
        )

        category_folder_id = self._ensure_folder(
            session=session,
            parent_id=party_folder_id,
            folder_name=category,
        )

        metadata = {
            "name": filename,
            "parents": [
                category_folder_id,
            ],
            "appProperties": {
                "holansl_party_id": str(party_id),
                "holansl_document_id": str(document_id),
            },
        }

        initiation_response = session.post(
            self.DRIVE_UPLOAD_URL,
            params={
                "uploadType": "resumable",
                "supportsAllDrives": "true",
                "fields": "id,name,mimeType,size,parents",
            },
            json=metadata,
            headers={
                "X-Upload-Content-Type": mime_type,
                "X-Upload-Content-Length": str(
                    size_bytes,
                ),
            },
            timeout=self.timeout,
        )

        if not initiation_response.ok:
            raise DocumentStorageError(
                "Google Drive upload session could not be started: "
                f"{initiation_response.status_code} "
                f"{initiation_response.text}"
            )

        upload_url = initiation_response.headers.get(
            "Location",
        )

        if not upload_url:
            raise DocumentStorageError(
                "Google Drive did not return a resumable upload URL."
            )

        file_object.seek(0)

        upload_response = session.put(
            upload_url,
            data=file_object,
            headers={
                "Content-Type": mime_type,
                "Content-Length": str(size_bytes),
            },
            timeout=self.timeout,
        )

        if not upload_response.ok:
            raise DocumentStorageError(
                "Google Drive file upload failed: "
                f"{upload_response.status_code} "
                f"{upload_response.text}"
            )

        payload = upload_response.json()

        return StoredDocument(
            provider=(PartyDocument.StorageProvider.GOOGLE_DRIVE),
            external_file_id=payload["id"],
            external_folder_id=category_folder_id,
            storage_path=(f"{party_id}/{category}/{filename}"),
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
        Download a Drive file into a seekable temporary stream.

        Args:
            external_file_id: Google Drive file ID.
            storage_path: Logical path retained for provider neutrality.
            filename: Filename supplied to the API client.
            mime_type: Stored MIME type.

        Returns:
            DownloadedDocument: Temporary file stream and metadata.

        Raises:
            DocumentStorageError: If Drive cannot return the file.
        """

        session = self._session()

        response = session.get(
            f"{self.DRIVE_FILES_URL}/{external_file_id}",
            params={
                "alt": "media",
                "supportsAllDrives": "true",
            },
            stream=True,
            timeout=self.timeout,
        )

        if not response.ok:
            raise DocumentStorageError(
                f"Google Drive download failed: {response.status_code} {response.text}"
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
        Permanently delete a Drive file.

        Args:
            external_file_id: Google Drive file ID.
            storage_path: Logical path retained for provider neutrality.

        Returns:
            None.

        Raises:
            DocumentStorageError: If Drive rejects deletion.
        """

        session = self._session()

        response = session.delete(
            f"{self.DRIVE_FILES_URL}/{external_file_id}",
            params={
                "supportsAllDrives": "true",
            },
            timeout=self.timeout,
        )

        if response.status_code not in {
            200,
            204,
            404,
        }:
            raise DocumentStorageError(
                f"Google Drive deletion failed: {response.status_code} {response.text}"
            )

    def check_configuration(self) -> dict:
        """
        Verify access to the configured Drive root folder.

        Returns:
            dict: Provider, root ID and root-folder name.

        Raises:
            DocumentStorageError: If the root folder is inaccessible.
        """

        session = self._session()

        response = session.get(
            f"{self.DRIVE_FILES_URL}/{self.root_folder_id}",
            params={
                "supportsAllDrives": "true",
                "fields": "id,name,mimeType",
            },
            timeout=self.timeout,
        )

        if not response.ok:
            raise DocumentStorageError(
                "Google Drive root-folder check failed: "
                f"{response.status_code} {response.text}"
            )

        payload = response.json()

        if (
            payload.get(
                "mimeType",
            )
            != self.FOLDER_MIME_TYPE
        ):
            raise DocumentStorageError(
                "GOOGLE_DRIVE_ROOT_FOLDER_ID does not identify a Google Drive folder."
            )

        return {
            "provider": (PartyDocument.StorageProvider.GOOGLE_DRIVE),
            "root_folder_id": payload["id"],
            "root_folder_name": payload["name"],
        }
