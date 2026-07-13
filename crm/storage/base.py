"""
Base interfaces and result objects for CRM document storage providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import BinaryIO
from uuid import UUID


class DocumentStorageError(RuntimeError):
    """Raised when an external document provider cannot complete an operation."""


@dataclass(frozen=True)
class StoredDocument:
    """Describe a successfully uploaded external document."""

    provider: str
    external_file_id: str
    external_folder_id: str = ""
    storage_path: str = ""


@dataclass
class DownloadedDocument:
    """Contain a downloaded file stream and its response metadata."""

    file_object: BinaryIO
    filename: str
    mime_type: str
    size_bytes: int | None = None


class DocumentStorageBackend(ABC):
    """Define operations required from every CRM document provider."""

    @abstractmethod
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
        Upload one CRM document.

        Args:
            document_id: UUID reserved for the PartyDocument record.
            party_id: CRM party UUID used to organise storage.
            category: CRM document category.
            filename: Sanitised original filename.
            mime_type: File MIME type.
            size_bytes: Exact file size.
            file_object: Seekable binary stream positioned at its beginning.

        Returns:
            StoredDocument: Provider identifiers needed by the database.

        Raises:
            DocumentStorageError: If the provider rejects the upload.
        """

    @abstractmethod
    def download(
        self,
        *,
        external_file_id: str,
        storage_path: str,
        filename: str,
        mime_type: str,
    ) -> DownloadedDocument:
        """
        Download one CRM document.

        Args:
            external_file_id: Provider-specific object identifier.
            storage_path: Optional provider-specific path.
            filename: Filename returned to the API client.
            mime_type: Stored MIME type.

        Returns:
            DownloadedDocument: Seekable downloaded stream and metadata.

        Raises:
            DocumentStorageError: If the provider cannot return the file.
        """

    @abstractmethod
    def delete(
        self,
        *,
        external_file_id: str,
        storage_path: str,
    ) -> None:
        """
        Delete one externally stored document.

        Args:
            external_file_id: Provider-specific object identifier.
            storage_path: Optional provider-specific object path.

        Returns:
            None.

        Raises:
            DocumentStorageError: If deletion fails.
        """

    @abstractmethod
    def check_configuration(self) -> dict:
        """
        Validate provider credentials and root storage access.

        Returns:
            dict: Non-sensitive provider status information.

        Raises:
            DocumentStorageError: If configuration or access is invalid.
        """
