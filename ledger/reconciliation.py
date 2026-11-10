"""Frozen, complete party statements with immutable staff reviews."""

from decimal import Decimal
from django.conf import settings
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from crm.models import Party
from .models import Reconciliation, ReconciliationReview, PartyLedgerTransaction
from .operations import require
from .balances import remaining_balances


class StatementInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"])

    def validate(self, attrs):
        if attrs["date_from"] > attrs["date_to"]:
            raise serializers.ValidationError("End date must follow start date.")
        return attrs


@transaction.atomic
def snapshot(*, actor, party_id, kind, payload):
    require(
        actor,
        f"ledger.view_{kind}ledger",
        "ledger.add_reconciliation",
        "ledger.view_reconciliation",
    )
    form = StatementInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    from .idempotency import lock_submission

    lock_submission(d["request_key"])
    party = Party.objects.select_for_update().get(pk=party_id)
    previous = Reconciliation.objects.filter(request_key=d["request_key"]).first()
    if previous:
        if (
            previous.party_id != party.pk
            or previous.ledger_kind != kind
            or previous.created_by_id != actor.pk
            or any(getattr(previous, k) != v for k, v in d.items())
        ):
            raise ValidationError(
                "Submission identifier already used with different details."
            )
        return previous
    records = list(
        PartyLedgerTransaction.objects.filter(
            party=party,
            ledger_kind=kind,
            currency=d["currency"],
            date__lte=d["date_to"],
        )
        .select_related("created_by", "cash_entry")
        .order_by("date", "created_at", "id")
    )
    opening = sum(
        (r.amount * r.effect for r in records if r.date < d["date_from"]), Decimal("0")
    )
    balance, charges, credits, payments = (
        opening,
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
    )
    invoices, refunds = Decimal("0"), Decimal("0")
    rows, outstanding, advances = [], [], []
    balances = remaining_balances(records, d["date_to"])
    for r in records:
        if r.date >= d["date_from"]:
            signed = r.amount * r.effect
            balance += signed
            charges += r.amount if r.effect == 1 else 0
            credits += r.amount if r.effect == -1 else 0
            payments += r.amount if r.transaction_type == "PAYMENT" else 0
            invoices += r.amount if r.transaction_type in ("INVOICE", "BILL") else 0
            refunds += r.amount if r.transaction_type == "REFUND" else 0
            rows.append(
                {
                    "id": str(r.pk),
                    "date": str(r.date),
                    "type": r.transaction_type,
                    "reference": r.reference,
                    "description": r.description,
                    "debit": f"{r.amount if r.effect == 1 else 0:.2f}",
                    "credit": f"{r.amount if r.effect == -1 else 0:.2f}",
                    "balance": f"{balance:.2f}",
                    "recorded_by": r.created_by.username,
                }
            )
        available = balances.get(r.pk, Decimal("0"))
        if available > 0:
            item = {
                "id": str(r.pk),
                "date": str(r.date),
                "reference": r.reference,
                "amount": f"{available:.2f}",
            }
            (advances if r.transaction_type == "PAYMENT" else outstanding).append(item)
    content = {
        "title": "Reconciliation and Statement",
        "party": {"id": str(party.pk), "name": party.display_name},
        "company": dict(getattr(settings, "PROCUREMENT_DOCUMENT_COMPANY", {})),
        "ledger_kind": kind,
        "currency": d["currency"],
        "date_from": str(d["date_from"]),
        "date_to": str(d["date_to"]),
        "opening_balance": f"{opening:.2f}",
        "charges": f"{charges:.2f}",
        "credits": f"{credits:.2f}",
        "payments": f"{payments:.2f}",
        "invoices": f"{invoices:.2f}",
        "refunds": f"{refunds:.2f}",
        "closing_balance": f"{balance:.2f}",
        "transactions": rows,
        "outstanding": outstanding,
        "advances": advances,
    }
    from .tax_reconciliation import enrich

    enrich(
        content, records, party.pk, kind, d["date_from"], d["date_to"], d["currency"]
    )
    # Freeze the actual supplier receipt number, not the internal PAY-UUID.
    from .models import PurchaseReceiptEvidence

    evidence = {}
    if kind == "purchase":
        for item in (
            PurchaseReceiptEvidence.objects.filter(
                payment__transaction_id__in=[r.pk for r in records]
            )
            .select_related("payment")
            .order_by("payment_id", "-version")
            .distinct("payment_id")
        ):
            evidence[str(item.payment.transaction_id)] = item.receipt_number
    for row in content["transactions"]:
        row["document_reference"] = (
            evidence.get(row["id"], "")
            if kind == "purchase" and row["type"] == "PAYMENT"
            else row["reference"]
        )
    content["print_version"] = 2
    return Reconciliation.objects.create(
        party=party, ledger_kind=kind, created_by=actor, snapshot=content, **d
    )


class ReviewInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    status = serializers.ChoiceField(choices=["AGREED", "DISPUTED"])
    notes = serializers.CharField(max_length=1000)


@transaction.atomic
def review(*, actor, statement, payload):
    require(
        actor,
        f"ledger.view_{statement.ledger_kind}ledger",
        "ledger.view_reconciliation",
        "ledger.confirm_reconciliation",
    )
    form = ReviewInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    from .idempotency import lock_submission

    lock_submission(d["request_key"])
    row, created = ReconciliationReview.objects.get_or_create(
        request_key=d["request_key"],
        defaults={**d, "actor": actor, "reconciliation": statement},
    )
    if not created and (
        row.actor_id != actor.pk
        or row.reconciliation_id != statement.pk
        or any(getattr(row, k) != v for k, v in d.items())
    ):
        raise ValidationError(
            "Submission identifier already used with different details."
        )
    return row
