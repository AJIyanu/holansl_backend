"""Reserve one email attempt per UUID; retain its exact revision and safe outcome."""

import hashlib
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from notifications.services.document_email import send_document_email
from .invoices import access, lock_invoice, assert_version
from .idempotency import lock_submission
from .models import InvoiceEmail
from .invoice_documents import render_invoice_pdf


class EmailInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    version = serializers.IntegerField(min_value=1)
    destination = serializers.EmailField()
    subject = serializers.CharField(max_length=250, required=False, allow_blank=True)

    def validate_subject(self, value):
        if "\r" in value or "\n" in value:
            raise ValidationError("Email subject must be a single line.")
        return value

    message = serializers.CharField(max_length=10000, required=False, allow_blank=True)


def default_message(name):
    return f"Dear {name},\n\nPlease find attached our invoice for the items detailed in the document.\n\nKindly review the invoice and contact us if you require any clarification.\n\nThank you for choosing HolanSL.\n\nKind regards,\nAccounts Department\nHolanSL"


def send_invoice(*, actor, invoice_id, payload):
    access(actor, "email")
    form = EmailInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    with transaction.atomic():
        lock_submission(d["request_key"])
        invoice = lock_invoice(invoice_id)
        # Retry checks precede current-version checks: edits cannot resend an old email.
        existing = (
            InvoiceEmail.objects.filter(request_key=d["request_key"])
            .select_related("revision")
            .first()
        )
        if existing:
            source = existing.revision.snapshot
            subject = d.get("subject") or f"Invoice {source['reference']} — HolanSL"
            message = d.get("message") or default_message(source["billed_to"])
            if (
                existing.revision.invoice_id != invoice.pk
                or existing.actor_id != actor.pk
                or existing.revision.version != d["version"]
                or existing.destination != d["destination"]
                or existing.subject != subject
                or existing.message != message
            ):
                raise ValidationError(
                    "Submission identifier already used with different email details."
                )
            return existing
        assert_version(invoice, d["version"])
        if not invoice.confirmed_revision_id:
            raise ValidationError("Confirm this invoice before emailing it.")
        revision = invoice.confirmed_revision
        pdf = render_invoice_pdf(revision.snapshot)
        record = InvoiceEmail.objects.create(
            request_key=d["request_key"],
            revision=revision,
            destination=d["destination"],
            subject=d.get("subject") or f"Invoice {invoice.reference} — HolanSL",
            message=d.get("message") or default_message(invoice.billed_to),
            pdf_sha256=hashlib.sha256(pdf).hexdigest(),
            actor=actor,
        )
    try:
        result = send_document_email(
            destination=record.destination,
            subject=record.subject,
            message=record.message,
            filename=f"{invoice.reference}.pdf",
            content=pdf,
            request_key=record.request_key,
        )
    except Exception:
        # Do not persist provider errors: they may contain confidential message bodies.
        record.status = "UNKNOWN"
    else:
        record.status = "SENT"
        record.provider_message_id = result.provider_message_id
    record.completed_at = timezone.now()
    record.save(update_fields=["status", "provider_message_id", "completed_at"])
    return record
