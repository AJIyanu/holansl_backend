"""Currency-separated staff balances and immutable complete statement snapshots."""

from datetime import date
from django.conf import settings
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from .models import StaffLedgerEntry as Entry, StaffStatement
from .staff_ledger import access, lock_staff, staff_name, ensure_key_unused, ZERO
from .idempotency import lock_submission
from .invoices import today


class PeriodInput(serializers.Serializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"], default="NGN")

    def validate(self, d):
        d.setdefault("date_from", date(today().year, 1, 1))
        d.setdefault("date_to", date(today().year, 12, 31))
        if d["date_from"] > d["date_to"]:
            raise ValidationError("Start date must not follow end date.")
        return d


def report(staff, period, *, include_transactions=True):
    rows = Entry.objects.filter(
        staff=staff, currency=period["currency"], date__lte=period["date_to"]
    ).select_related("actor")
    receivable = payable = ZERO
    opening_r = opening_p = paid = received = expenses = ZERO
    sources = {}
    transactions = []
    for row in rows.iterator(chunk_size=2000):
        receivable += row.receivable_delta
        payable += row.payable_delta
        if row.kind == "PAY" and row.category in ["ADVANCE", "LOAN"]:
            sources[row.pk] = {
                "category": row.category,
                "receivable": row.receivable_delta,
                "payable": row.payable_delta,
                "retired": False,
            }
        if row.related_entry_id in sources:
            source = sources[row.related_entry_id]
            source["receivable"] += row.receivable_delta
            source["payable"] += row.payable_delta
            source["retired"] |= row.kind == "RETIRE"
        if row.date < period["date_from"]:
            opening_r, opening_p = receivable, payable
            continue
        paid += row.amount if row.kind == "PAY" else ZERO
        received += row.amount if row.kind == "RECEIVE" else ZERO
        expenses += row.amount if row.kind == "RETIRE" else ZERO
        if not include_transactions:
            continue
        transactions.append(
            {
                "id": str(row.pk),
                "date": str(row.date),
                "kind": row.kind,
                "category": row.category,
                "description": row.description,
                "reference": row.reference,
                "amount": str(row.amount),
                "receivable_delta": str(row.receivable_delta),
                "payable_delta": str(row.payable_delta),
                "receivable_balance": str(receivable),
                "payable_balance": str(payable),
                "actor_name": row.actor.username,
            }
        )
    totals = dict(
        opening_receivable=opening_r,
        opening_payable=opening_p,
        paid=paid,
        received=received,
        approved_expenses=expenses,
        closing_receivable=receivable,
        closing_payable=payable,
        loans=ZERO,
        unretired_advances=ZERO,
        change_due=ZERO,
        reimbursement_due=ZERO,
    )
    for source in sources.values():
        key = (
            "loans"
            if source["category"] == "LOAN"
            else "change_due"
            if source["retired"]
            else "unretired_advances"
        )
        totals[key] += source["receivable"]
        totals["reimbursement_due"] += source["payable"]
    return {
        "staff": {"id": str(staff.pk), "name": staff_name(staff)},
        "company": getattr(settings, "PROCUREMENT_DOCUMENT_COMPANY", {})
        or {"name": "Holan Integrated Services Limited"},
        "currency": period["currency"],
        "date_from": str(period["date_from"]),
        "date_to": str(period["date_to"]),
        "summary": {key: str(value) for key, value in totals.items()},
        "transactions": transactions,
    }


@transaction.atomic
def statement(*, actor, staff_id, payload):
    access(actor, "print")

    class Input(PeriodInput):
        request_key = serializers.UUIDField()

    form = Input(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    lock_submission(d["request_key"])
    prior = StaffStatement.objects.filter(request_key=d["request_key"]).first()
    if prior:
        if (
            prior.actor_id != actor.pk
            or str(prior.staff_id) != str(staff_id)
            or prior.date_from != d["date_from"]
            or prior.date_to != d["date_to"]
            or prior.currency != d["currency"]
        ):
            raise ValidationError(
                "Submission identifier was used for another statement."
            )
        return prior
    ensure_key_unused(d["request_key"])
    staff = lock_staff(staff_id)
    return StaffStatement.objects.create(
        staff=staff, actor=actor, snapshot=report(staff, d), **d
    )
