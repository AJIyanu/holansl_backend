"""Confirmed itemized receipts: one atomic goods payment, actual cash and tax split."""

from decimal import Decimal, ROUND_HALF_UP, localcontext
from types import SimpleNamespace
from django.conf import settings
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from crm.models import Party
from .invoices import InvoiceInput, item_amounts, assign_reference, today, lock_invoice
from .operations import require
from .idempotency import lock_submission
from .models import SalesReceipt, PartyLedgerTransaction, LedgerAccount, LedgerOperation
from .party_posting import post_party_transaction
from .balances import remaining


class ReceiptInput(InvoiceInput):
    account_id = serializers.UUIDField()
    payment_method = serializers.ChoiceField(
        choices=["CASH", "BANK_TRANSFER", "CARD", "CHEQUE"]
    )
    bank_name = serializers.CharField(max_length=255, allow_blank=True, default="")
    payment_reference = serializers.CharField(
        max_length=100, allow_blank=True, default=""
    )
    vat_paid = serializers.BooleanField(default=False)
    withholding_applied = serializers.BooleanField(default=False)
    invoice_id = serializers.UUIDField(required=False, allow_null=True)
    invoice_version = serializers.IntegerField(min_value=1, required=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["date"] > today():
            raise ValidationError("A receipt cannot acknowledge a future payment.")
        return attrs


def access(actor, action):
    require(
        actor,
        "ledger.view_salesledger",
        "ledger.view_salesreceipt",
        f"ledger.{action}_salesreceipt",
    )
    if action == "add":
        require(actor, "ledger.add_salestransaction", "ledger.add_cashbookentry")


def amounts(subtotal, vat_paid, withholding_applied):
    with localcontext() as ctx:
        ctx.prec = 40
        vat = (
            (subtotal * Decimal(".075")).quantize(
                Decimal(".01"), rounding=ROUND_HALF_UP
            )
            if vat_paid
            else Decimal("0.00")
        )
        withholding = (
            (subtotal * Decimal(".02")).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
            if withholding_applied
            else Decimal("0.00")
        )
        received = subtotal + vat - withholding
    if received > Decimal("9999999999999999.99"):
        raise ValidationError("Amount received exceeds the supported amount limit.")
    return vat, withholding, received


@transaction.atomic
def create_receipt(*, actor, party_id, payload):
    access(actor, "add")
    form = ReceiptInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    canonical = {"party_id": str(party_id), **form.data}
    lock_submission(d["request_key"])
    previous = SalesReceipt.objects.filter(request_key=d["request_key"]).first()
    if previous:
        if previous.actor_id != actor.pk or previous.submission != canonical:
            raise ValidationError(
                "Submission identifier already used with different receipt details."
            )
        return previous
    if (
        PartyLedgerTransaction.objects.filter(request_key=d["request_key"]).exists()
        or LedgerOperation.objects.filter(request_key=d["request_key"]).exists()
    ):
        raise ValidationError("Submission identifier belongs to another posting.")
    invoice = None
    if d.get("invoice_id"):
        require(actor, "ledger.view_salesinvoice")
        from .models import SalesInvoice

        if not SalesInvoice.objects.filter(
            pk=d["invoice_id"], party_id=party_id
        ).exists():
            raise ValidationError("Select an invoice belonging to this client.")
        invoice = lock_invoice(d["invoice_id"])
        if invoice.currency != d["currency"] or invoice.version != d.get(
            "invoice_version"
        ):
            raise ValidationError(
                "Invoice currency or version changed. Reload the invoice before confirming."
            )
        if invoice.posted_transaction_id and hasattr(
            invoice.posted_transaction, "reversal"
        ):
            raise ValidationError("A reversed invoice cannot be used for this receipt.")
    party = Party.objects.active().with_role("CLIENT").filter(pk=party_id).first()
    if not party:
        raise ValidationError("Choose an active client.")
    account = LedgerAccount.objects.filter(
        pk=d["account_id"], currency=d["currency"], is_active=True
    ).first()
    if not account:
        raise ValidationError(
            "Choose an active receiving account in the receipt currency."
        )
    if (d["payment_method"] == "CASH") != (account.kind == "CASH"):
        raise ValidationError(
            "Payment method must match the selected cash or bank account."
        )
    if account.kind == "BANK" and not d["bank_name"]:
        raise ValidationError("Enter the name of the bank paid into.")
    gross, net = item_amounts(d["items"], d["discount_percent"])
    subtotal = sum(net, Decimal("0"))
    vat, withholding, received = amounts(
        subtotal, d["vat_paid"], d["withholding_applied"]
    )
    reference = assign_reference(SimpleNamespace(party=party))
    # Numbering lock precedes the party lock, matching invoice confirmation/delivery.
    Party.objects.select_for_update().get(pk=party.pk)
    allocations = []
    if (
        invoice
        and invoice.posted_transaction_id
        and invoice.posted_transaction.date <= d["date"]
    ):
        value = min(subtotal, remaining(invoice.posted_transaction))
        if value > 0:
            allocations = [
                {
                    "document_id": str(invoice.posted_transaction_id),
                    "amount": str(value),
                }
            ]
    posted = post_party_transaction(
        actor=actor,
        receipt_breakdown=(vat, withholding),
        payload={
            "request_key": str(d["request_key"]),
            "party_id": str(party_id),
            "ledger_kind": "sales",
            "transaction_type": "PAYMENT",
            "currency": d["currency"],
            "account_id": str(account.pk),
            "date": str(d["date"]),
            "amount": str(subtotal),
            "description": f"Payment receipt {reference}",
            "reference": reference,
            "allocations": allocations,
        },
    )
    from .receipt_words import amount_in_words

    snapshot = {
        "title": "Receipt",
        "reference": reference,
        "date": str(d["date"]),
        "currency": d["currency"],
        "billed_to": d["billed_to"],
        "billing_address": d["billing_address"],
        "recipient_email": d["recipient_email"],
        "notes": d["notes"],
        "company": dict(settings.PROCUREMENT_DOCUMENT_COMPANY),
        "subtotal": str(subtotal),
        "gross_subtotal": str(sum(gross, Decimal("0"))),
        "discount_percent": str(d["discount_percent"]),
        "discount_amount": str(sum(gross, Decimal("0")) - subtotal),
        "vat": str(vat),
        "vat_rate": "7.5",
        "vat_paid": d["vat_paid"],
        "withholding": str(withholding),
        "withholding_rate": "2",
        "withholding_applied": d["withholding_applied"],
        "total": str(received),
        "amount_in_words": amount_in_words(received, d["currency"]),
        "payment_method": d["payment_method"],
        "bank_name": d["bank_name"] if account.kind == "BANK" else "",
        "account_name": account.name,
        "payment_reference": d["payment_reference"],
        "invoice_reference": invoice.reference or "Unconfirmed invoice"
        if invoice
        else "",
        "invoice_version": invoice.version if invoice else None,
        "allocations": allocations,
        "items": [
            {
                "reference": f"{reference}.{i}",
                "description": row["description"],
                "quantity": str(row["quantity"]),
                "unit": row["unit"],
                "unit_price": str(row["unit_price"]),
                "amount": str(n),
                "gross_amount": str(g),
            }
            for i, (row, g, n) in enumerate(zip(d["items"], gross, net), 1)
        ],
    }
    return SalesReceipt.objects.create(
        request_key=d["request_key"],
        party=party,
        invoice=invoice,
        transaction=posted,
        reference=reference,
        date=d["date"],
        currency=d["currency"],
        subtotal=subtotal,
        vat=vat,
        withholding=withholding,
        received=received,
        snapshot=snapshot,
        submission=canonical,
        actor=actor,
    )
