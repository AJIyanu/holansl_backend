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


def get_document_storage(provider=None) -> DocumentStorageBackend:
    """
    Return the configured document-storage provider.

    Returns:
        DocumentStorageBackend: Google Drive or Supabase implementation.

    Raises:
        DocumentStorageError: If the configured provider is unsupported.
    """

    provider = (
        (
            provider
            if provider is not None
            else getattr(
                settings,
                "CRM_DOCUMENT_STORAGE_PROVIDER",
                "supabase",
            )
        )
        .strip()
        .lower()
    )
    return _get_document_storage(provider)


@lru_cache(maxsize=2)
def _get_document_storage(provider):

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

    _get_document_storage.cache_clear()
