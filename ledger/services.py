from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import CashBookEntry, CashBookJournal


def post(entry, actor):
    counterpart = {"RECEIPT": "INCOME", "EXPENSE": "EXPENSE", "CAPITAL": "CAPITAL"}[
        entry.kind
    ]
    expense = entry.kind == "EXPENSE"
    return CashBookJournal.objects.create(
        entry=entry,
        currency=entry.currency,
        date=entry.date,
        amount=entry.amount,
        debit_account=counterpart if expense else "CASH",
        credit_account="CASH" if expense else counterpart,
        description=entry.description,
        reference=entry.reference,
        actor=actor,
    )


def reverse(entry, actor, reason):
    original = entry.journal.filter(reverses__isnull=True, reversal__isnull=True).get()
    CashBookJournal.objects.create(
        entry=entry,
        currency=original.currency,
        date=original.date,
        amount=original.amount,
        debit_account=original.credit_account,
        credit_account=original.debit_account,
        description=original.description,
        reference=original.reference,
        reason=reason,
        reverses=original,
        actor=actor,
    )


@transaction.atomic
def create_entry(data, actor):
    entry, created = CashBookEntry.objects.get_or_create(
        request_key=data["request_key"],
        defaults={**data, "created_by": actor, "updated_by": actor},
    )
    if created:
        post(entry, actor)
    elif (
        entry.created_by_id != actor.pk
        or entry.voided_at
        or any(getattr(entry, field) != value for field, value in data.items())
    ):
        raise ValidationError(
            {"request_key": "This submission identifier has already been used."}
        )
    return entry


@transaction.atomic
def revise_entry(entry_id, data, actor, *, void=False):
    entry = CashBookEntry.objects.select_for_update().get(pk=entry_id)
    if entry.tax_entries.exists():
        raise ValidationError(
            "Tax-linked cash entries require an approved tax correction."
        )
    if hasattr(entry, "staff_transaction"):
        raise ValidationError(
            "Staff-linked cash entries cannot be edited or voided independently of the staff ledger."
        )
    if hasattr(entry, "party_transaction"):
        raise ValidationError(
            "Correct linked payments through the party ledger; independent cash-book changes are not allowed."
        )
    if entry.voided_at:
        raise ValidationError("This entry has already been voided.")
    if data.pop("version") != entry.version:
        raise ValidationError("This entry changed. Refresh before trying again.")
    reason = data.pop("reason")
    reverse(entry, actor, reason)
    if void:
        entry.voided_at = timezone.now()
        entry.void_reason = reason
    else:
        for field, value in data.items():
            setattr(entry, field, value)
    entry.version += 1
    entry.updated_by = actor
    entry.save()
    if not void:
        post(entry, actor)
    return entry
