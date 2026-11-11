"""Editable, unposted invoice documents and explicit delivery posting."""

import itertools
import string
from decimal import Decimal, ROUND_HALF_UP, localcontext
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction, connection
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from crm.models import Party
from .models import (
    SalesInvoice,
    SalesInvoiceItem,
    InvoiceRevision,
    InvoiceOperation,
    InvoiceClientCode,
    InvoiceSequence,
    InvoiceFulfilment,
)
from .operations import require
from .idempotency import lock_submission
from .party_posting import post_party_transaction

MAX_AMOUNT = Decimal("9999999999999999.99")


def item_amounts(items, discount_percent):
    """Round gross and discount per line; persisted amounts are always net."""
    with localcontext() as ctx:
        ctx.prec = 40
        gross = [
            (r["quantity"] * r["unit_price"]).quantize(
                Decimal(".01"), rounding=ROUND_HALF_UP
            )
            for r in items
        ]
        net = [
            value
            - (value * discount_percent / 100).quantize(
                Decimal(".01"), rounding=ROUND_HALF_UP
            )
            for value in gross
        ]
    return gross, net


def invoice_pricing(invoice):
    """Recover editable pricing from existing immutable submission metadata."""
    operation = (
        invoice.operations.filter(action__in=["CREATE", "EDIT"])
        .order_by("-created_at", "-request_key")
        .first()
    )
    rate = (
        Decimal(str(operation.payload.get("discount_percent", "0")))
        if operation
        else Decimal("0")
    )
    gross, _ = item_amounts(
        [
            {"quantity": row.quantity, "unit_price": row.unit_price}
            for row in invoice.items.all()
        ],
        rate,
    )
    with localcontext() as ctx:
        ctx.prec = 40
        gross_total = sum(gross, Decimal("0"))
        vat = (invoice.subtotal * invoice.tax_rate / 100).quantize(
            Decimal(".01"), rounding=ROUND_HALF_UP
        )
        return {
            "discount_percent": format(rate, "f"),
            "gross_subtotal": str(gross_total),
            "discount_amount": str(gross_total - invoice.subtotal),
            "vat": str(vat),
            "total": str(invoice.subtotal + vat),
        }


def today():
    return timezone.localdate(timezone=ZoneInfo("Africa/Lagos"))


class ItemInput(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    description = serializers.CharField(max_length=1000)
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=4, min_value=Decimal(".0001")
    )
    unit = serializers.CharField(max_length=40)
    unit_price = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0")
    )


class InvoiceInput(serializers.Serializer):
    expected_vat_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False
    )
    request_key = serializers.UUIDField()
    version = serializers.IntegerField(min_value=1, required=False)
    date = serializers.DateField()
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"])
    billed_to = serializers.CharField(max_length=255)
    billing_address = serializers.CharField(
        max_length=1000, allow_blank=True, default=""
    )
    recipient_email = serializers.EmailField(allow_blank=True, default="")
    notes = serializers.CharField(max_length=2000, allow_blank=True, default="")
    discount_percent = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        default=Decimal("0"),
    )
    items = ItemInput(many=True, min_length=1, max_length=100)

    def validate(self, attrs):
        ids = [r["id"] for r in attrs["items"] if r.get("id")]
        if len(ids) != len(set(ids)):
            raise ValidationError("Each item can appear only once.")
        _, amounts = item_amounts(attrs["items"], attrs["discount_percent"])
        with localcontext() as ctx:
            ctx.prec = 40
            subtotal = sum(amounts, Decimal("0"))
        if not 0 < subtotal <= MAX_AMOUNT:
            raise ValidationError(
                "Goods subtotal after discount must be positive and within the supported amount limit."
            )
        return attrs


class InvoiceActionInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    version = serializers.IntegerField(min_value=1)
    delivery_date = serializers.DateField(required=False)


def access(actor, action):
    require(
        actor,
        "ledger.view_salesledger",
        "ledger.view_salesinvoice",
        f"ledger.{action}_salesinvoice",
    )


def replay(actor, key, action, payload, invoice_id=None):
    previous = (
        InvoiceOperation.objects.filter(request_key=key)
        .select_related("invoice")
        .first()
    )
    if previous:
        if (
            previous.actor_id != actor.pk
            or previous.action != action
            or previous.payload != payload
            or (invoice_id and previous.invoice_id != invoice_id)
        ):
            raise ValidationError(
                "Submission identifier already used with different details."
            )
        return previous.invoice


def record_operation(actor, invoice, action, key, payload):
    InvoiceOperation.objects.create(
        invoice=invoice, actor=actor, action=action, request_key=key, payload=payload
    )


def lock_invoice(invoice_id):
    return (
        SalesInvoice.objects.select_for_update(of=("self",))
        .select_related("party", "confirmed_revision")
        .get(pk=invoice_id)
    )


def assert_version(invoice, version):
    if invoice.version != version:
        raise ValidationError("This invoice changed. Refresh before trying again.")


@transaction.atomic
def save_invoice(*, actor, party_id, payload, invoice_id=None):
    access(actor, "change" if invoice_id else "add")
    form = InvoiceInput(data=payload)
    form.is_valid(raise_exception=True)
    canonical = {
        "party_id": str(party_id),
        **form.data,
    }
    # Keep historical zero-discount submissions replayable with their old keys.
    if form.validated_data["discount_percent"] == 0:
        canonical.pop("discount_percent", None)
    d = dict(form.validated_data)
    key = d.pop("request_key")
    lock_submission(key)
    previous = replay(
        actor, key, "EDIT" if invoice_id else "CREATE", canonical, invoice_id
    )
    if previous:
        return previous
    expected_rate = d.pop("expected_vat_rate", None)
    items = d.pop("items")
    discount_percent = d.pop("discount_percent")
    version = d.pop("version", None)
    if invoice_id:
        invoice = lock_invoice(invoice_id)
        if invoice.party_id != party_id:
            raise ValidationError("Invoice belongs to a different client.")
        assert_version(invoice, version)
        if invoice.delivered_at:
            raise ValidationError(
                "Delivered invoices cannot be changed through this action."
            )
        invoice.version += 1
        invoice.confirmed_revision = None
        for name, value in d.items():
            setattr(invoice, name, value)
    else:
        party = Party.objects.active().with_role("CLIENT").filter(pk=party_id).first()
        if not party:
            raise ValidationError("Choose an active client.")
        if any(item.get("id") for item in items):
            raise ValidationError("New invoices cannot reuse item identifiers.")
        from .tax_posting import policy_rate

        invoice = SalesInvoice(
            party=party, created_by=actor, tax_rate=policy_rate("VAT", d["date"]), **d
        )
    _, amounts = item_amounts(items, discount_percent)
    if expected_rate is not None and expected_rate != invoice.tax_rate:
        raise ValidationError(
            "VAT policy changed. Reload the form and review the current rate."
        )
    subtotal = sum(amounts, Decimal("0"))
    if invoice_id:
        from .tax_posting import validate_document_edit, rounded

        original_invoice = SalesInvoice.objects.get(pk=invoice_id)
        validate_document_edit(
            original_invoice,
            "sales",
            subtotal,
            rounded(subtotal * invoice.tax_rate / 100),
            invoice.currency,
        )
    invoice.subtotal = subtotal
    invoice.updated_by = actor
    invoice.save()
    old_ids = set(invoice.items.values_list("id", flat=True))
    if any(item.get("id") and item["id"] not in old_ids for item in items):
        raise ValidationError("An item belongs to another invoice.")
    invoice.items.all().delete()
    SalesInvoiceItem.objects.bulk_create(
        [
            SalesInvoiceItem(invoice=invoice, position=i, amount=amount, **item)
            for i, (item, amount) in enumerate(zip(items, amounts), 1)
        ]
    )
    record_operation(actor, invoice, "EDIT" if invoice_id else "CREATE", key, canonical)
    return invoice


def assign_reference(invoice):
    # One short transaction lock protects client-code allocation and the daily counter.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('ledger.invoice.numbering', 0))"
        )
    code = InvoiceClientCode.objects.filter(party=invoice.party).first()
    if not code:
        preferred = "".join(
            c for c in invoice.party.display_name.upper() if c in string.ascii_uppercase
        )[:3].ljust(3, "X")
        occupied = set(InvoiceClientCode.objects.values_list("code", flat=True))
        candidate = preferred
        if candidate in occupied:
            candidate = next(
                (
                    "".join(chars)
                    for chars in itertools.product(string.ascii_uppercase, repeat=3)
                    if "".join(chars) not in occupied
                ),
                None,
            )
        if not candidate:
            raise ValidationError(
                "All three-letter invoice client codes are allocated."
            )
        code = InvoiceClientCode.objects.create(party=invoice.party, code=candidate)
    day = today()
    sequence, _ = InvoiceSequence.objects.get_or_create(date=day)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    return f"HOL-{code.code}-{day:%y%m%d}-{sequence.last_number:03d}"


def document_snapshot(invoice):
    return {
        **invoice_pricing(invoice),
        "title": "Invoice",
        "reference": invoice.reference,
        "date": str(invoice.date),
        "currency": invoice.currency,
        "billed_to": invoice.billed_to,
        "billing_address": invoice.billing_address,
        "notes": invoice.notes,
        "company": dict(settings.LEDGER_DOCUMENT_COMPANY),
        "version": invoice.version,
        "vat_rate": str(invoice.tax_rate),
        "delivery_status": "Delivered" if invoice.delivered_at else "Not delivered",
        "delivery_date": str(invoice.delivered_at) if invoice.delivered_at else None,
        "items": [
            {
                "id": str(r.pk),
                "reference": f"{invoice.reference}.{r.position}",
                "description": r.description,
                "quantity": str(r.quantity),
                "unit": r.unit,
                "unit_price": str(r.unit_price),
                "amount": str(r.amount),
                "gross_amount": str(
                    item_amounts(
                        [{"quantity": r.quantity, "unit_price": r.unit_price}],
                        Decimal("0"),
                    )[0][0]
                ),
            }
            for r in invoice.items.all()
        ],
        "subtotal": str(invoice.subtotal),
    }


def freeze(invoice, actor):
    revision = InvoiceRevision.objects.create(
        invoice=invoice,
        version=invoice.version,
        snapshot=document_snapshot(invoice),
        actor=actor,
    )
    invoice.confirmed_revision = revision
    invoice.updated_by = actor
    invoice.save()
    return invoice


@transaction.atomic
def invoice_action(*, actor, invoice_id, action, payload):
    access(actor, action)
    form = InvoiceActionInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    canonical = dict(form.data)
    lock_submission(d["request_key"])
    previous = replay(actor, d["request_key"], action, canonical, invoice_id)
    if previous:
        return previous
    invoice = lock_invoice(invoice_id)
    assert_version(invoice, d["version"])
    if action == "confirm":
        if invoice.delivered_at:
            raise ValidationError("This invoice has already been delivered.")
        if not invoice.confirmed_revision_id:
            if not invoice.reference:
                invoice.reference = assign_reference(invoice)
            freeze(invoice, actor)
    elif action == "deliver":
        if invoice.delivered_at:
            raise ValidationError("This invoice has already been delivered.")
        if not invoice.confirmed_revision_id:
            raise ValidationError(
                "Confirm the current invoice details before marking it delivered."
            )
        delivered = d.get("delivery_date")
        if not delivered or delivered < invoice.date or delivered > today():
            raise ValidationError(
                "Delivery date must be between the invoice date and today."
            )
        # A delivery permission grants this specific posting, not arbitrary entries.
        require(actor, "ledger.add_salestransaction")
        posted = post_party_transaction(
            actor=actor,
            payload={
                "request_key": str(d["request_key"]),
                "party_id": str(invoice.party_id),
                "ledger_kind": "sales",
                "transaction_type": "INVOICE",
                "currency": invoice.currency,
                "date": str(delivered),
                "amount": str(invoice.subtotal),
                "description": f"Delivered invoice {invoice.reference}",
                "reference": invoice.reference,
            },
        )
        invoice.posted_transaction = posted
        invoice.delivered_at = delivered
        from .tax_posting import delivery_tax

        delivery_tax(invoice, "sales")
        invoice.version += 1
        InvoiceFulfilment.objects.create(
            invoice=posted,
            request_key=d["request_key"],
            status="DISPATCHED",
            date=delivered,
            amount=invoice.subtotal,
            evidence=f"Delivery confirmed for {invoice.reference}",
            actor=actor,
        )
        freeze(invoice, actor)
    record_operation(actor, invoice, action, d["request_key"], canonical)
    return invoice
