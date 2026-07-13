"""Public CRM document-storage service exports."""

from .base import (
    DocumentStorageBackend,
    DocumentStorageError,
    DownloadedDocument,
    StoredDocument,
)
from .factory import (
    clear_document_storage_cache,
    get_document_storage,
)


__all__ = [
    "DocumentStorageBackend",
    "DocumentStorageError",
    "DownloadedDocument",
    "StoredDocument",
    "clear_document_storage_cache",
    "get_document_storage",
]
