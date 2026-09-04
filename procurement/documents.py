"""Private procurement files using the existing verified storage adapters."""

import logging
import uuid

from rest_framework.exceptions import APIException
from crm.document_services import validate_document_upload, calculate_document_checksum
from crm.storage import get_document_storage, DocumentStorageError
from . import models as m, services as s

logger = logging.getLogger(__name__)


class StorageUnavailable(APIException):
    status_code = 503
    default_detail = "Private document storage is unavailable. Retry later."
    default_code = "storage_unavailable"


def upload(user, data):
    request = s.lock_request(data["request"], user, submitted=False)
    file = data["file"]
    filename, mime, size = validate_document_upload(file)
    checksum = calculate_document_checksum(file)
    storage = get_document_storage("supabase")
    document_id = uuid.uuid4()
    try:
        stored = storage.upload(
            document_id=document_id,
            party_id=request.client_id,
            category="PROCUREMENT_" + data["category"],
            filename=filename,
            mime_type=mime,
            size_bytes=size,
            file_object=file,
        )
    except DocumentStorageError:
        raise StorageUnavailable() from None
    try:
        return s.make(
            m.ProcurementDocument,
            user,
            id=document_id,
            request=request,
            category=data["category"],
            filename=filename,
            mime_type=mime,
            size_bytes=size,
            checksum=checksum,
            storage_provider=stored.provider,
            external_file_id=stored.external_file_id,
            storage_path=stored.storage_path,
        )
    except Exception:
        try:
            storage.delete(
                external_file_id=stored.external_file_id,
                storage_path=stored.storage_path,
            )
        except DocumentStorageError:
            logger.error(
                "Procurement document cleanup failed; document_id=%s", document_id
            )
        raise


def download(document):
    try:
        return get_document_storage(document.storage_provider).download(
            external_file_id=document.external_file_id,
            storage_path=document.storage_path,
            filename=document.filename,
            mime_type=document.mime_type,
        )
    except DocumentStorageError:
        raise StorageUnavailable() from None
