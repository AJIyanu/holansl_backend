"""Private supplier receipt evidence, versioned independently of financial records."""

import logging
from uuid import uuid4
from pathlib import Path
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from crm.document_services import (
    sanitize_document_filename,
    calculate_document_checksum,
)
from crm.storage import get_document_storage, DocumentStorageError
from .models import PurchasePayment, PurchaseReceiptEvidence
from .idempotency import lock_submission
from .purchases import access

logger = logging.getLogger(__name__)
MAX_BYTES = 10 * 1024 * 1024


def validated_file(file):
    if not file:
        return None
    if not 0 < file.size <= MAX_BYTES:
        raise ValidationError(
            "Receipt scans must be non-empty and no larger than 10 MB."
        )
    name = sanitize_document_filename(file.name)
    suffix = Path(name).suffix.lower()
    try:
        if suffix == ".pdf":
            import pypdfium2 as pdfium

            if file.read(5) != b"%PDF-":
                raise ValueError()
            file.seek(0)
            with pdfium.PdfDocument(file.read()) as document:
                if not 1 <= len(document) <= 100:
                    raise ValueError()
            mime = "application/pdf"
        else:
            from PIL import Image

            with Image.open(file) as image:
                formats = {
                    "JPEG": ([".jpg", ".jpeg"], "image/jpeg"),
                    "PNG": ([".png"], "image/png"),
                    "WEBP": ([".webp"], "image/webp"),
                }
                allowed, mime = formats[image.format]
                if suffix not in allowed or image.width * image.height > 25_000_000:
                    raise ValueError()
                image.verify()
    except Exception:
        raise ValidationError(
            "Upload a valid PDF (up to 100 pages), JPEG, PNG or WebP receipt scan."
        ) from None
    finally:
        file.seek(0)
    return {
        "filename": name,
        "mime_type": mime,
        "size_bytes": file.size,
        "checksum": calculate_document_checksum(file),
    }


def cleanup(uploaded):
    for storage, stored in uploaded:
        try:
            storage.delete(
                external_file_id=stored.external_file_id,
                storage_path=stored.storage_path,
            )
        except DocumentStorageError:
            logger.error(
                "Supplier receipt upload compensation failed; operator review required."
            )


def persist_evidence(
    *,
    payment,
    actor,
    key,
    number,
    version,
    payload,
    file,
    info,
    uploaded,
    previous=None,
):
    identity = uuid4()
    fields = {}
    if file:
        try:
            storage = get_document_storage()
            stored = storage.upload(
                document_id=identity,
                party_id=payment.party_id,
                category="supplier_receipt",
                filename=info["filename"],
                mime_type=info["mime_type"],
                size_bytes=info["size_bytes"],
                file_object=file,
            )
        except DocumentStorageError:
            raise ValidationError(
                "Unable to store the receipt scan. No payment or evidence changes were saved."
            ) from None
        uploaded.append((storage, stored))
        fields = {
            **info,
            "storage_provider": stored.provider,
            "external_file_id": stored.external_file_id,
            "storage_path": stored.storage_path,
        }
    elif previous:
        fields = {
            name: getattr(previous, name)
            for name in [
                "filename",
                "mime_type",
                "size_bytes",
                "checksum",
                "storage_provider",
                "external_file_id",
                "storage_path",
            ]
        }
    return PurchaseReceiptEvidence.objects.create(
        id=identity,
        payment=payment,
        request_key=key,
        receipt_number=number,
        version=version,
        payload=payload,
        actor=actor,
        **fields,
    )


def update_evidence(*, actor, pk, payload, uploaded_file=None):
    access(actor, "purchasepayment", "change")
    if set(payload) - {"request_key", "version", "receipt_number"}:
        raise ValidationError(
            "Only the supplier receipt number and scan can be changed after confirmation."
        )

    class Input(serializers.Serializer):
        request_key = serializers.UUIDField()
        version = serializers.IntegerField(min_value=1)
        receipt_number = serializers.CharField(max_length=100, allow_blank=True)

    form = Input(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    info = validated_file(uploaded_file)
    canonical = {**form.data, "file": info}
    uploaded = []
    try:
        with transaction.atomic():
            lock_submission(d["request_key"])
            payment = PurchasePayment.objects.select_for_update().get(pk=pk)
            old = PurchaseReceiptEvidence.objects.filter(
                request_key=d["request_key"]
            ).first()
            if old:
                if (
                    old.payment_id != pk
                    or old.actor_id != actor.pk
                    or old.payload != canonical
                ):
                    raise ValidationError(
                        "Submission identifier was used with different receipt evidence."
                    )
                return old
            previous = payment.evidence.first()
            if previous.version != d["version"]:
                raise ValidationError(
                    "Receipt evidence changed. Refresh before updating."
                )
            return persist_evidence(
                payment=payment,
                actor=actor,
                key=d["request_key"],
                number=d["receipt_number"],
                version=previous.version + 1,
                payload=canonical,
                file=uploaded_file,
                info=info,
                uploaded=uploaded,
                previous=previous,
            )
    except Exception:
        cleanup(uploaded)
        raise
