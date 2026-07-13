"""
Factory for selecting the configured CRM document-storage backend.
"""

from functools import lru_cache

from django.conf import settings

from .base import (
    DocumentStorageBackend,
    DocumentStorageError,
)
from .google_drive import (
    GoogleDriveDocumentStorage,
)
from .supabase import (
    SupabaseDocumentStorage,
)


@lru_cache(maxsize=1)
def get_document_storage() -> DocumentStorageBackend:
    """
    Return the configured document-storage provider.

    Returns:
        DocumentStorageBackend: Google Drive or Supabase implementation.

    Raises:
        DocumentStorageError: If the configured provider is unsupported.
    """

    provider = (
        getattr(
            settings,
            "CRM_DOCUMENT_STORAGE_PROVIDER",
            "google_drive",
        )
        .strip()
        .lower()
    )

    if provider == "google_drive":
        return GoogleDriveDocumentStorage()

    if provider == "supabase":
        return SupabaseDocumentStorage()

    raise DocumentStorageError(f"Unsupported CRM_DOCUMENT_STORAGE_PROVIDER: {provider}")


def clear_document_storage_cache() -> None:
    """
    Clear the cached provider instance.

    Returns:
        None.
    """

    get_document_storage.cache_clear()
