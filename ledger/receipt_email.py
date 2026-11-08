"""Send a frozen receipt PDF once per submission key, through the shared provider."""

import hashlib
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from notifications.services.document_email import send_document_email
from .invoice_email import EmailInput
from .receipts import access
from .idempotency import lock_submission
from .models import ReceiptEmail, SalesReceipt
from .invoice_documents import render_invoice_pdf


class ReceiptEmailInput(EmailInput):
    version = None


def default_message(name):
    return f"Dear {name},\n\nPlease find attached your payment receipt for the items detailed in the document. We acknowledge receipt of your payment with thanks.\n\nPlease contact us if you require any clarification.\n\nThank you for choosing HolanSL.\n\nKind regards,\nAccounts Department\nHolanSL"


def send_receipt(*, actor, receipt_id, payload):
    access(actor, "email")
    form = ReceiptEmailInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    with transaction.atomic():
        lock_submission(d["request_key"])
        receipt = SalesReceipt.objects.select_related("transaction").get(pk=receipt_id)
        subject = d.get("subject") or f"Receipt {receipt.reference} — HolanSL"
        message = d.get("message") or default_message(receipt.snapshot["billed_to"])
        existing = ReceiptEmail.objects.filter(request_key=d["request_key"]).first()
        if existing:
            if (
                existing.receipt_id != receipt.pk
                or existing.actor_id != actor.pk
                or existing.destination != d["destination"]
                or existing.subject != subject
                or existing.message != message
            ):
                raise ValidationError(
                    "Submission identifier already used with different email details."
                )
            return existing
        if hasattr(receipt.transaction, "reversal"):
            raise serializers.ValidationError(
                "This receipt's payment has been reversed."
            )
        pdf = render_invoice_pdf(receipt.snapshot)
        record = ReceiptEmail.objects.create(
            request_key=d["request_key"],
            receipt=receipt,
            destination=d["destination"],
            subject=subject,
            message=message,
            pdf_sha256=hashlib.sha256(pdf).hexdigest(),
            actor=actor,
        )
    try:
        result = send_document_email(
            destination=record.destination,
            subject=record.subject,
            message=record.message,
            filename=f"{receipt.reference}.pdf",
            content=pdf,
            request_key=record.request_key,
        )
    except Exception:
        record.status = "UNKNOWN"
    else:
        record.status = "SENT"
        record.provider_message_id = result.provider_message_id
    record.completed_at = timezone.now()
    record.save(update_fields=["status", "provider_message_id", "completed_at"])
    return record
