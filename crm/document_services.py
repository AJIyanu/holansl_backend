"""
CRM document business services.

This module validates files, coordinates remote storage and database records,
streams downloads and safely removes stored documents.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from accounts.models import AuditLog

from .exceptions import CRMConflict
from .models import (
    Party,
    PartyDocument,
)
from .notifications import (
    cancel_document_expiry_notification,
    schedule_document_expiry_notification,
)
from .services import log_crm_event
from .storage import (
    DocumentStorageError,
    DownloadedDocument,
    get_document_storage,
)

_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._() -]+")


def sanitize_document_filename(
    filename: str,
) -> str:
    """
    Remove path components and unsafe characters from a filename.

    Args:
        filename: Original uploaded filename.

    Returns:
        str: Safe filename limited to 255 characters.

    Raises:
        ValidationError: If no usable filename remains.
    """

    basename = Path(
        str(filename or ""),
    ).name.strip()

    basename = _UNSAFE_FILENAME_RE.sub(
        "_",
        basename,
    )

    basename = re.sub(
        r"\s+",
        " ",
        basename,
    ).strip(" .")

    if not basename:
        raise ValidationError({"file": ("The uploaded file has an invalid filename.")})

    return basename[:255]


def calculate_document_checksum(
    uploaded_file,
) -> str:
    """
    Calculate a SHA-256 checksum without consuming the upload permanently.

    Args:
        uploaded_file: Django UploadedFile or compatible seekable object.

    Returns:
        str: Lowercase hexadecimal SHA-256 digest.
    """

    digest = hashlib.sha256()

    uploaded_file.seek(0)

    if hasattr(
        uploaded_file,
        "chunks",
    ):
        chunks = uploaded_file.chunks()
    else:
        chunks = iter(
            lambda: uploaded_file.read(
                1024 * 1024,
            ),
            b"",
        )

    for chunk in chunks:
        digest.update(chunk)

    uploaded_file.seek(0)

    return digest.hexdigest()


def validate_document_upload(
    uploaded_file,
) -> tuple[str, str, int]:
    """
    Validate document filename, size and MIME type.

    Args:
        uploaded_file: Django UploadedFile supplied by the API.

    Returns:
        tuple[str, str, int]: Safe filename, MIME type and file size.

    Raises:
        ValidationError: If the file is empty, too large or disallowed.
    """

    filename = sanitize_document_filename(
        uploaded_file.name,
    )

    size_bytes = int(
        getattr(
            uploaded_file,
            "size",
            0,
        )
        or 0
    )

    if size_bytes <= 0:
        raise ValidationError(
            {
                "file": "The uploaded file is empty.",
            }
        )

    maximum_size = getattr(
        settings,
        "CRM_DOCUMENT_MAX_SIZE_BYTES",
        20 * 1024 * 1024,
    )

    if size_bytes > maximum_size:
        raise ValidationError(
            {
                "file": (
                    "The uploaded file exceeds the configured "
                    f"limit of {maximum_size} bytes."
                )
            }
        )

    mime_type = (
        getattr(
            uploaded_file,
            "content_type",
            "",
        )
        or mimetypes.guess_type(
            filename,
        )[0]
        or "application/octet-stream"
    ).lower()

    allowed_types = getattr(
        settings,
        "CRM_DOCUMENT_ALLOWED_MIME_TYPES",
        set(),
    )

    if allowed_types and mime_type not in allowed_types:
        raise ValidationError({"file": (f"Files of type {mime_type} are not allowed.")})

    return (
        filename,
        mime_type,
        size_bytes,
    )


@transaction.atomic
def create_party_document_record(
    *,
    document_id,
    party: Party,
    category: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    checksum_sha256: str,
    stored_document,
    description: str,
    is_confidential: bool,
    verification_status: str,
    expires_at,
    user,
) -> PartyDocument:
    """
    Create the database record for an externally uploaded document.

    Args:
        document_id: Reserved PartyDocument UUID.
        party: CRM party that owns the document.
        category: CRM document category.
        filename: Sanitised original filename.
        mime_type: Validated MIME type.
        size_bytes: Exact file length.
        checksum_sha256: File digest.
        stored_document: StoredDocument returned by the provider.
        description: Optional document description.
        is_confidential: Whether stronger permissions are required.
        verification_status: Initial verification status.
        expires_at: Optional expiry date.
        user: User uploading the document.

    Returns:
        PartyDocument: Persisted document metadata record.
    """

    return PartyDocument.objects.create(
        id=document_id,
        party=party,
        category=category,
        original_filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        checksum_sha256=checksum_sha256,
        storage_provider=(stored_document.provider),
        external_file_id=(stored_document.external_file_id),
        external_folder_id=(stored_document.external_folder_id),
        storage_path=(stored_document.storage_path),
        description=description,
        is_confidential=is_confidential,
        verification_status=verification_status,
        expires_at=expires_at,
        uploaded_by=user,
    )


def upload_party_document(
    *,
    party: Party,
    uploaded_file,
    category: str,
    description: str,
    is_confidential: bool,
    verification_status: str,
    expires_at,
    user,
    request=None,
) -> PartyDocument:
    """
    Validate, upload and register one CRM party document.

    Args:
        party: CRM party that owns the document.
        uploaded_file: Django UploadedFile.
        category: CRM document category.
        description: Optional document description.
        is_confidential: Whether access requires confidential permission.
        verification_status: Initial verification state.
        expires_at: Optional document expiry date.
        user: Authenticated uploading user.
        request: Optional request used for audit metadata.

    Returns:
        PartyDocument: Newly created document record.

    Raises:
        ValidationError: If the upload is invalid.
        CRMConflict: If an identical active document already exists.
        DocumentStorageError: If the external upload fails.
    """

    (
        filename,
        mime_type,
        size_bytes,
    ) = validate_document_upload(
        uploaded_file,
    )

    checksum = calculate_document_checksum(
        uploaded_file,
    )

    duplicate = PartyDocument.objects.filter(
        party=party,
        category=category,
        checksum_sha256=checksum,
        is_active=True,
    ).first()

    if duplicate is not None:
        raise CRMConflict(
            {
                "detail": (
                    "An identical active document already exists "
                    "for this party and category."
                ),
                "code": "duplicate_document",
                "document_id": str(
                    duplicate.id,
                ),
            }
        )

    document_id = uuid.uuid4()
    storage = get_document_storage()

    uploaded_file.seek(0)

    stored_document = storage.upload(
        document_id=document_id,
        party_id=party.id,
        category=category,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        file_object=uploaded_file.file,
    )

    try:
        document = create_party_document_record(
            document_id=document_id,
            party=party,
            category=category,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            checksum_sha256=checksum,
            stored_document=stored_document,
            description=description,
            is_confidential=is_confidential,
            verification_status=(verification_status),
            expires_at=expires_at,
            user=user,
        )
    except Exception:
        try:
            storage.delete(
                external_file_id=(stored_document.external_file_id),
                storage_path=(stored_document.storage_path),
            )
        except DocumentStorageError:
            pass

        raise

    log_crm_event(
        user=user,
        event_type=AuditLog.EventType.CREATE,
        resource="partydocument",
        action="upload",
        object_id=document.id,
        request=request,
        metadata={
            "party_id": str(party.id),
            "category": category,
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "checksum_sha256": checksum,
            "storage_provider": (document.storage_provider),
            "is_confidential": (is_confidential),
        },
    )

    if document.expires_at:
        transaction.on_commit(
            lambda: schedule_document_expiry_notification(
                document_id=document.id,
                actor_id=(user.id if user else None),
            )
        )

    return document


def download_party_document(
    *,
    document: PartyDocument,
    user,
    request=None,
) -> DownloadedDocument:
    """
    Download and audit an active CRM document.

    Args:
        document: Active PartyDocument to download.
        user: Authenticated downloading user.
        request: Optional request used for audit metadata.

    Returns:
        DownloadedDocument: Temporary binary stream and metadata.

    Raises:
        CRMConflict: If the document is inactive.
        DocumentStorageError: If the provider cannot return the file.
    """

    if not document.is_active:
        raise CRMConflict("Deleted CRM documents cannot be downloaded.")

    storage = get_document_storage()

    downloaded = storage.download(
        external_file_id=document.external_file_id,
        storage_path=document.storage_path,
        filename=document.original_filename,
        mime_type=document.mime_type,
    )

    log_crm_event(
        user=user,
        event_type=AuditLog.EventType.READ,
        resource="partydocument",
        action="download",
        object_id=document.id,
        request=request,
        metadata={
            "party_id": str(
                document.party_id,
            ),
            "filename": (document.original_filename),
            "storage_provider": (document.storage_provider),
        },
    )

    return downloaded


@transaction.atomic
def delete_party_document(
    *,
    document: PartyDocument,
    user,
    request=None,
) -> PartyDocument:
    """
    Delete the external file and retain a soft-deleted metadata record.

    Args:
        document: Active PartyDocument selected for deletion.
        user: Authenticated deleting user.
        request: Optional request used for audit metadata.

    Returns:
        PartyDocument: Updated inactive document record.

    Raises:
        DocumentStorageError: If the provider cannot delete the file.
    """

    locked_document = (
        PartyDocument.objects.select_for_update()
        .select_related(
            "party",
        )
        .get(
            pk=document.pk,
        )
    )

    if not locked_document.is_active:
        return locked_document

    storage = get_document_storage()

    storage.delete(
        external_file_id=(locked_document.external_file_id),
        storage_path=(locked_document.storage_path),
    )

    cancel_document_expiry_notification(
        document=locked_document,
        actor=user,
        reason="CRM document was deleted.",
    )

    locked_document.is_active = False
    locked_document.deleted_at = timezone.now()
    locked_document.deleted_by = user
    locked_document.expiry_notification_key = ""
    locked_document.save(
        update_fields={
            "is_active",
            "deleted_at",
            "deleted_by",
            "expiry_notification_key",
            "updated_at",
        }
    )

    log_crm_event(
        user=user,
        event_type=AuditLog.EventType.DELETE,
        resource="partydocument",
        action="delete",
        object_id=locked_document.id,
        request=request,
        metadata={
            "party_id": str(
                locked_document.party_id,
            ),
            "filename": (locked_document.original_filename),
            "storage_provider": (locked_document.storage_provider),
        },
    )

    return locked_document
