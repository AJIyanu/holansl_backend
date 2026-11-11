"""Append-only corrections and settlement operations. All writes lock the party."""

from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from crm.models import Party
from .models import (
    PartyLedgerTransaction as Txn,
    PartyPaymentAllocation,
    AllocationRelease,
    LedgerOperation,
    CashBookEntry,
    CashBookJournal,
    InvoiceFulfilment,
    RevenueRecognition,
)
from .party_posting import AllocationInput
from .balances import remaining, allocations, validate_effective_date


class OperationInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    action = serializers.ChoiceField(
        choices=[
            "ALLOCATE",
            "CREDIT",
            "REFUND",
            "REVERSE",
            "ADJUST_UP",
            "ADJUST_DOWN",
            "DISPATCHED",
            "REJECTED",
            "ACCEPTED",
        ]
    )
    transaction_id = serializers.UUIDField(required=False)
    date = serializers.DateField()
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0.01"), required=False
    )
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"], default="NGN")
    reason = serializers.CharField(max_length=500)
    allocations = AllocationInput(many=True, max_length=100, default=list)


def require(actor, *permissions):
    if (
        not actor.is_authenticated
        or not actor.is_active
        or not (actor.is_staff or actor.is_superuser)
        or not actor.has_perms(permissions)
    ):
        raise PermissionDenied()


def release(record, actor, date, reason):
    for allocation in allocations(record):
        if date < allocation.effective_date:
            raise ValidationError(
                "Correction cannot precede an allocation it releases."
            )
        AllocationRelease.objects.create(
            allocation=allocation, actor=actor, date=date, reason=reason
        )


def recognized(invoice):
    rows = invoice.recognitions.all()
    return sum((-r.amount if r.is_reversal else r.amount for r in rows), Decimal("0"))


def recognize(invoice, actor, date, amount, source_key, reason, reverse=False):
    if amount <= 0:
        return
    debit, credit = (
        ("INCOME", "DEFERRED_REVENUE") if reverse else ("DEFERRED_REVENUE", "INCOME")
    )
    journal = CashBookJournal.objects.create(
        currency=invoice.currency,
        date=date,
        amount=amount,
        debit_account=debit,
        credit_account=credit,
        description=reason,
        reference=invoice.reference,
        actor=actor,
    )
    RevenueRecognition.objects.create(
        invoice=invoice,
        actor=actor,
        date=date,
        amount=amount,
        is_reversal=reverse,
        source_key=source_key,
        evidence=reason,
        journal=journal,
    )


@transaction.atomic
def operate(*, actor, party_id, kind, payload):
    form = OperationInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    action = d["action"]
    permission = (
        "recognize_salesledger"
        if action in ("ACCEPTED", "DISPATCHED", "REJECTED")
        else f"correct_{kind}ledger"
    )
    require(actor, f"ledger.view_{kind}ledger", f"ledger.{permission}")
    if action in ("REFUND", "REVERSE"):
        require(actor, "ledger.add_cashbookentry")
    from .idempotency import lock_submission

    lock_submission(d["request_key"])
    party = Party.objects.select_for_update().get(pk=party_id)
    # Serializer output is canonical: decimals and dates become stable strings.
    canonical = dict(form.data)
    previous = LedgerOperation.objects.filter(request_key=d["request_key"]).first()
    if previous:
        if (
            previous.actor_id != actor.pk
            or previous.party_id != party.pk
            or previous.payload != {"kind": kind, **canonical}
        ):
            raise ValidationError(
                "Submission identifier already used with different details."
            )
        return previous.result
    if Txn.objects.filter(request_key=d["request_key"]).exists():
        raise ValidationError("Submission identifier belongs to another posting.")
    source = None
    if d.get("transaction_id"):
        source = Txn.objects.filter(
            pk=d["transaction_id"],
            party=party,
            ledger_kind=kind,
            currency=d["currency"],
        ).first()
    if action not in ("ADJUST_UP", "ADJUST_DOWN") and not source:
        raise ValidationError(
            "Select a transaction in this party, ledger and currency."
        )
    if source and (d["date"] < source.date or hasattr(source, "reversal")):
        raise ValidationError("Transaction is reversed or correction date precedes it.")
    if source:
        validate_effective_date(source, d["date"])
    amount = d.get("amount")
    if action == "ALLOCATE":
        if source.transaction_type != "PAYMENT":
            raise ValidationError("Only a payment can be allocated.")
        from .models import TaxEntry

        tax_source = TaxEntry.objects.filter(transaction=source, kind="PAYMENT").first()
        items = d["allocations"]
        if tax_source and (tax_source.sales_vat_cash or tax_source.purchase_vat_cash):
            target_keys = set(
                TaxEntry.objects.filter(
                    transaction_id__in=[i["document_id"] for i in items],
                    kind__in=["DELIVERY", "OPENING"],
                ).values_list("document_key", flat=True)
            )
            if items and (target_keys != {tax_source.document_key} or len(items) != 1):
                raise ValidationError(
                    "VAT-bearing payments must remain linked to their original invoice or bill. Reverse and reissue with the correct document to change tax ownership."
                )
        if len({i["document_id"] for i in items}) != len(items):
            raise ValidationError("Select each document only once.")
        release(source, actor, d["date"], d["reason"])
        available = remaining(source)
        if sum((i["amount"] for i in items), Decimal("0")) > available:
            raise ValidationError("Allocations exceed available payment.")
        for item in items:
            doc = Txn.objects.filter(
                pk=item["document_id"],
                party=party,
                ledger_kind=kind,
                currency=source.currency,
                transaction_type="INVOICE" if kind == "sales" else "BILL",
            ).first()
            if not doc or doc.date > d["date"] or item["amount"] > remaining(doc):
                raise ValidationError(
                    "Allocation exceeds outstanding balance or has an invalid date."
                )
            validate_effective_date(doc, d["date"])
            PartyPaymentAllocation.objects.create(
                payment=source,
                document=doc,
                amount=item["amount"],
                effective_date=d["date"],
            )
        result = {
            "id": str(source.pk),
            "message": "Allocations updated; cash was unchanged.",
        }
    elif action in ("ACCEPTED", "DISPATCHED", "REJECTED"):
        if kind != "sales" or source.transaction_type != "INVOICE":
            raise ValidationError(
                "Goods evidence is available only for sales invoices."
            )
        if not amount:
            raise ValidationError("Enter the goods value covered by this evidence.")
        previous_amount = source.fulfilments.filter(status=action).aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0")
        if previous_amount + amount > source.amount:
            raise ValidationError("Evidence exceeds the invoice amount.")
        InvoiceFulfilment.objects.create(
            invoice=source,
            request_key=d["request_key"],
            status=action,
            amount=amount,
            date=d["date"],
            evidence=d["reason"],
            actor=actor,
        )
        if action == "ACCEPTED":
            credits = source.adjustments.filter(
                transaction_type="CREDIT", reversal__isnull=True
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            if recognized(source) + amount > source.amount - credits:
                raise ValidationError("Acceptance exceeds uncredited goods value.")
            recognize(
                source, actor, d["date"], amount, str(d["request_key"]), d["reason"]
            )
        result = {"id": str(source.pk), "message": "Goods evidence recorded."}
    else:
        if action == "REVERSE":
            if source.transaction_type not in (
                "INVOICE",
                "BILL",
                "PAYMENT",
                "ADJUST_UP",
                "ADJUST_DOWN",
            ):
                raise ValidationError(
                    "This transaction cannot be reversed. Use an explicit adjusting entry."
                )
            if source.adjustments.exists():
                raise ValidationError(
                    "This entry has linked corrections; review them before reversing."
                )
            if source.transaction_type == "INVOICE":
                if (
                    recognized(source) > 0
                    or source.fulfilments.filter(status="ACCEPTED").exists()
                ):
                    raise ValidationError(
                        "Accepted goods cannot be cancelled. Record an authorized credit note instead."
                    )
                dispatched = source.fulfilments.filter(status="DISPATCHED").aggregate(
                    total=Sum("amount")
                )["total"] or Decimal("0")
                rejected = source.fulfilments.filter(status="REJECTED").aggregate(
                    total=Sum("amount")
                )["total"] or Decimal("0")
                if dispatched > rejected:
                    raise ValidationError(
                        "Delivered goods need documented rejection before cancellation."
                    )
            release(source, actor, d["date"], d["reason"])
            amount, effect = source.amount, -source.effect
            debit, credit = source.journal.credit_account, source.journal.debit_account
        else:
            if not amount:
                raise ValidationError("Enter an amount.")
            effect = -1 if action in ("CREDIT", "ADJUST_DOWN") else 1
            if action in ("CREDIT", "REFUND"):
                expected = ("INVOICE", "BILL") if action == "CREDIT" else ("PAYMENT",)
                if source.transaction_type not in expected or amount > remaining(
                    source
                ):
                    raise ValidationError(
                        "Amount exceeds the available balance. Release allocations first if necessary."
                    )
            counterpart = "RECEIVABLE" if kind == "sales" else "PAYABLE"
            other = (
                "CASH"
                if action == "REFUND"
                else (
                    "DEFERRED_REVENUE"
                    if kind == "sales" and action == "CREDIT"
                    else "PURCHASE_CLEARING"
                    if action == "CREDIT"
                    else "ADJUSTMENT"
                )
            )
            debit, credit = (
                (counterpart, other)
                if (kind == "sales") == (effect == 1)
                else (other, counterpart)
            )
        cash = None
        receipt_correction = (
            source
            and (
                hasattr(source, "receipt_document")
                or hasattr(source, "purchase_payment")
            )
            and action in ("REFUND", "REVERSE")
        )
        refund_vat, refund_wht = Decimal("0"), Decimal("0")
        if receipt_correction and action == "REFUND":
            from .receipt_accounting import refund_split

            refund_vat, refund_wht = refund_split(source, amount)
            if amount + refund_vat - refund_wht <= 0:
                raise ValidationError(
                    "This refund rounds to zero cash after withholding. Choose a larger goods amount."
                )
        if action == "REFUND" or (action == "REVERSE" and source.cash_entry_id):
            original = source.cash_entry
            cash = CashBookEntry.objects.create(
                account=original.account,
                currency=original.currency,
                date=d["date"],
                kind="EXPENSE" if original.kind != "EXPENSE" else "RECEIPT",
                amount=(
                    original.amount
                    if action == "REVERSE"
                    else amount + refund_vat - refund_wht
                ),
                description=d["reason"],
                reference=source.reference,
                created_by=actor,
                updated_by=actor,
            )
        record = Txn.objects.create(
            request_key=d["request_key"],
            party=party,
            ledger_kind=kind,
            transaction_type="REVERSAL" if action == "REVERSE" else action,
            effect=effect,
            currency=d["currency"],
            date=d["date"],
            amount=amount,
            description=d["reason"],
            reason=d["reason"],
            created_by=actor,
            related_transaction=source,
            reverses=source if action == "REVERSE" else None,
            cash_entry=cash,
        )
        if receipt_correction:
            from .receipt_accounting import correction_journals

            correction_journals(
                record=record,
                source=source,
                action=action,
                vat=refund_vat,
                withholding=refund_wht,
                actor=actor,
            )
        else:
            CashBookJournal.objects.create(
                party_transaction=record,
                entry=cash,
                currency=record.currency,
                date=record.date,
                amount=amount,
                debit_account=debit,
                credit_account=credit,
                description=d["reason"],
                reason=d["reason"],
                actor=actor,
                reverses=source.journal if action == "REVERSE" else None,
            )
        if action == "CREDIT" and kind == "sales":
            recognize(
                source,
                actor,
                d["date"],
                min(amount, recognized(source)),
                str(d["request_key"]),
                d["reason"],
                reverse=True,
            )
        from .tax_posting import correction_tax

        correction_tax(
            record, source, action, refund_vat, refund_wht
        ) if source else None
        result = {"id": str(record.pk), "message": "Correction posted."}
    LedgerOperation.objects.create(
        request_key=d["request_key"],
        party=party,
        actor=actor,
        payload={"kind": kind, **canonical},
        result=result,
    )
    return result
