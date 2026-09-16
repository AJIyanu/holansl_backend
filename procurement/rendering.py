"""Immutable generated files in verified private storage, with ledger issue gates."""

import hashlib
import json
import logging
import uuid
from io import BytesIO

import pypdfium2 as pdfium
from django.db import transaction
from rest_framework.exceptions import APIException
from crm.storage import get_document_storage, DocumentStorageError
from . import models as m, services as s
from .document_registry import permission, require_ledger_approval
from .document_layout import company_profile, render_pdf
from .documents import StorageUnavailable

logger = logging.getLogger(__name__)


class TemplateUnavailable(APIException):
    status_code = 503
    default_code = "document_configuration_missing"
    default_detail = "Configure the current company office address before generating an official document. Drafts remain available."


@transaction.atomic
def generate(user, version, data):
    s.lock_request(version.register.request, user)
    permission(user, "generate_documentregister")
    if not data["draft"] and version.register.kind != "WB":
        require_ledger_approval(version.quotation_revision)
    company = company_profile()
    if not data["draft"] and not company["address"]:
        raise TemplateUnavailable()
    print_options = {
        "show_logistics": data.get("show_logistics", False),
        "show_additional_charges": data.get("show_additional_charges", False),
    }
    # Configuration changes create new assets; historical files remain untouched.
    template = (
        "sample-v2-"
        + hashlib.sha256(
            json.dumps(
                {"company": company, "print_options": print_options}, sort_keys=True
            ).encode()
        ).hexdigest()[:16]
    )
    criteria = dict(
        version=version,
        format=data["format"],
        draft=data["draft"],
        template_version=template,
    )
    if m.DocumentAsset.objects.filter(**criteria).exists():
        return m.DocumentVersion.objects.get(pk=version.pk)
    content = render_pdf(
        version.snapshot, company=company, draft=data["draft"], **print_options
    )
    if data["format"] == "PDF":
        files = [(0, content, "application/pdf")]
    else:
        files = []
        with pdfium.PdfDocument(content) as pdf:
            for index in range(len(pdf)):
                page = pdf[index]
                try:
                    bitmap = page.render(scale=2)
                    try:
                        output = BytesIO()
                        bitmap.to_pil().convert("RGB").save(
                            output, format="JPEG", quality=92
                        )
                        files.append((index + 1, output.getvalue(), "image/jpeg"))
                    finally:
                        bitmap.close()
                finally:
                    page.close()
    storage = get_document_storage("supabase")
    stored_files = []
    try:
        for page, raw, mime in files:
            document_id = uuid.uuid4()
            suffix = "-DRAFT" if data["draft"] else ""
            page_suffix = f"-p{page:03d}" if page else ""
            filename = f"{version.register.number}-r{version.revision:02d}{suffix}-{template}{page_suffix}.{data['format'].lower()}"
            stored = storage.upload(
                document_id=document_id,
                party_id=version.register.request.client_id,
                category=f"PROCUREMENT/{version.register.year}/{version.register.kind}/{version.register.number}/r{version.revision:02d}",
                filename=filename,
                mime_type=mime,
                size_bytes=len(raw),
                file_object=BytesIO(raw),
            )
            stored_files.append(stored)
            document = s.make(
                m.ProcurementDocument,
                user,
                id=document_id,
                request=version.register.request,
                category="WAYBILL" if version.register.kind == "WB" else "QUOTATION",
                filename=filename,
                mime_type=mime,
                size_bytes=len(raw),
                checksum=hashlib.sha256(raw).hexdigest(),
                storage_provider=stored.provider,
                external_file_id=stored.external_file_id,
                storage_path=stored.storage_path,
            )
            s.make(m.DocumentAsset, user, document=document, page=page, **criteria)
            # JPG is a per-page export. The complete official PDF is dispatch evidence.
            if (
                version.register.kind == "WB"
                and not data["draft"]
                and data["format"] == "PDF"
            ):
                delivery = version.register.delivery
                if not delivery.waybill_id and delivery.dispatched_at is None:
                    delivery.waybill = document
                    delivery.save(update_fields=["waybill"])
    except Exception as exc:
        for stored in stored_files:
            try:
                storage.delete(
                    external_file_id=stored.external_file_id,
                    storage_path=stored.storage_path,
                )
            except DocumentStorageError:
                logger.error(
                    "Generated document cleanup failed; version_id=%s", version.pk
                )
        if isinstance(exc, DocumentStorageError):
            raise StorageUnavailable() from None
        raise
    return m.DocumentVersion.objects.get(pk=version.pk)
