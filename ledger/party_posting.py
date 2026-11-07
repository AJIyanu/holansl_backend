"""Validated atomic posting of shared party transactions, cash and journal entries."""

from decimal import Decimal

from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError

from crm.models import Party
from .models import (
    PartyLedgerTransaction,
    CashBookEntry,
    CashBookJournal,
    PartyPaymentAllocation,
    LedgerAccount,
    PRIMARY_CASH_ID,
)


class AllocationInput(serializers.Serializer):
    document_id = serializers.UUIDField()
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0.01")
    )


class PostingInput(serializers.Serializer):
    allocations = AllocationInput(many=True, default=list, max_length=100)
    request_key = serializers.UUIDField()
    party_id = serializers.UUIDField()
    ledger_kind = serializers.ChoiceField(choices=["sales", "purchase"])
    transaction_type = serializers.ChoiceField(choices=["INVOICE", "BILL", "PAYMENT"])
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"], default="NGN")
    account_id = serializers.UUIDField(required=False)
    date = serializers.DateField()
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0.01")
    )
    description = serializers.CharField(max_length=500)
    reference = serializers.CharField(max_length=100, allow_blank=True, default="")


@transaction.atomic
def post_party_transaction(*, actor, payload):
    serializer = PostingInput(data=payload)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    account_id = data.pop("account_id", None)
    allocations = data.pop("allocations")
    if len({item["document_id"] for item in allocations}) != len(allocations):
        raise ValidationError("Select each document only once.")
    data["allocation_request"] = sorted(
        [
            {"document_id": str(item["document_id"]), "amount": str(item["amount"])}
            for item in allocations
        ],
        key=lambda item: item["document_id"],
    )
    kind, event = data["ledger_kind"], data["transaction_type"]
    if event != "PAYMENT" and allocations:
        raise ValidationError("Only payments can be allocated.")
    if sum((item["amount"] for item in allocations), Decimal("0")) > data["amount"]:
        raise ValidationError("Allocations exceed the payment amount.")
    required = [f"ledger.view_{kind}ledger", f"ledger.add_{kind}transaction"]
    if event == "PAYMENT":
        required.append("ledger.add_cashbookentry")
    if (
        not actor.is_authenticated
        or not actor.is_active
        or not (actor.is_staff or actor.is_superuser)
        or not actor.has_perms(required)
    ):
        raise PermissionDenied()
    if (kind == "sales" and event == "BILL") or (
        kind == "purchase" and event == "INVOICE"
    ):
        raise ValidationError("Transaction type does not match the ledger.")
    # Lock the party before lookup/create. All postings for this party serialize,
    # including simultaneous retries of the same submission.
    from .idempotency import lock_submission

    lock_submission(data["request_key"])
    party = Party.objects.select_for_update().filter(pk=data["party_id"]).first()
    if not party:
        raise ValidationError("Party is not available.")
    existing = PartyLedgerTransaction.objects.filter(
        request_key=data["request_key"]
    ).first()
    if existing:
        if account_id and (
            not existing.cash_entry_id or existing.cash_entry.account_id != account_id
        ):
            raise ValidationError("Submission account differs from the original.")
        if existing.created_by_id != actor.pk or any(
            getattr(existing, field) != value for field, value in data.items()
        ):
            raise ValidationError(
                "Submission identifier was already used with different details."
            )
        return existing
    from .models import LedgerOperation

    if LedgerOperation.objects.filter(request_key=data["request_key"]).exists():
        raise ValidationError("Submission identifier belongs to another operation.")
    role = "CLIENT" if kind == "sales" else "SUPPLIER"
    if not Party.objects.active().with_role(role).filter(pk=party.pk).exists():
        raise ValidationError(
            "An active party with the matching CRM role is required for posting."
        )
    if (
        event != "PAYMENT"
        and data["reference"]
        and PartyLedgerTransaction.objects.filter(
            party=party,
            ledger_kind=kind,
            transaction_type=event,
            reference__iexact=data["reference"],
        ).exists()
    ):
        raise ValidationError(
            {"reference": "This document reference already exists for this party."}
        )
    # The parent party lock serializes allocations as well as new postings.
    for item in allocations:
        document = PartyLedgerTransaction.objects.filter(
            pk=item["document_id"],
            party=party,
            ledger_kind=kind,
            currency=data["currency"],
            transaction_type="INVOICE" if kind == "sales" else "BILL",
        ).first()
        if not document:
            raise ValidationError(
                "An allocation document is not available for this party and ledger."
            )
        if document.date > data["date"]:
            raise ValidationError(
                "A payment cannot be allocated to a document dated after it. Record an advance instead."
            )
        from .balances import remaining, validate_effective_date

        validate_effective_date(document, data["date"])

        if item["amount"] > remaining(document):
            raise ValidationError(
                "An allocation exceeds the document's outstanding amount. Refresh the outstanding documents."
            )
    cash = None
    if event != "PAYMENT" and account_id:
        raise ValidationError("An account is only needed for a payment.")
    if event == "PAYMENT":
        account = LedgerAccount.objects.filter(
            pk=account_id or PRIMARY_CASH_ID, currency=data["currency"], is_active=True
        ).first()
        if not account:
            raise ValidationError(
                "Choose an active cash or bank account in this currency."
            )
        cash = CashBookEntry.objects.create(
            account=account,
            currency=data["currency"],
            date=data["date"],
            amount=data["amount"],
            description=data["description"],
            reference=data["reference"],
            kind="RECEIPT" if kind == "sales" else "EXPENSE",
            created_by=actor,
            updated_by=actor,
        )
    record, created = PartyLedgerTransaction.objects.get_or_create(
        request_key=data["request_key"],
        defaults={
            **data,
            "effect": -1 if event == "PAYMENT" else 1,
            "cash_entry": cash,
            "created_by": actor,
        },
    )
    if not created:
        raise ValidationError(
            "Submission identifier was already used with different details."
        )
    if event == "INVOICE":
        debit, credit = "RECEIVABLE", "DEFERRED_REVENUE"
    elif event == "BILL":
        debit, credit = "PURCHASE_CLEARING", "PAYABLE"
    elif kind == "sales":
        debit, credit = "CASH", "RECEIVABLE"
    else:
        debit, credit = "PAYABLE", "CASH"
    CashBookJournal.objects.create(
        entry=cash,
        party_transaction=record,
        currency=record.currency,
        date=record.date,
        amount=record.amount,
        debit_account=debit,
        credit_account=credit,
        description=record.description,
        reference=record.reference,
        actor=actor,
    )
    PartyPaymentAllocation.objects.bulk_create(
        [
            PartyPaymentAllocation(
                payment=record,
                document_id=item["document_id"],
                amount=item["amount"],
                effective_date=record.date,
            )
            for item in allocations
        ]
    )
    return record
