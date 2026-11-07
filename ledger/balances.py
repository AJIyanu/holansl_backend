"""Currency-specific balances, including dated releases and corrections."""

from decimal import Decimal
from django.db.models import Q, Sum
from .models import PartyPaymentAllocation

ZERO = Decimal("0")


def allocations(record, as_of=None):
    key = "payment" if record.transaction_type == "PAYMENT" else "document"
    rows = PartyPaymentAllocation.objects.filter(**{key: record})
    if as_of:
        return rows.filter(effective_date__lte=as_of).filter(
            Q(release__isnull=True) | Q(release__date__gt=as_of)
        )
    return rows.filter(release__isnull=True)


def remaining(record, as_of=None):
    if record.transaction_type not in ("INVOICE", "BILL", "PAYMENT"):
        return ZERO
    reversed_rows = record.__class__.objects.filter(reverses=record)
    if as_of:
        reversed_rows = reversed_rows.filter(date__lte=as_of)
    if reversed_rows.exists():
        return ZERO
    corrections = record.adjustments.filter(
        transaction_type="REFUND" if record.transaction_type == "PAYMENT" else "CREDIT"
    )
    if as_of:
        corrections = corrections.filter(date__lte=as_of).filter(
            Q(reversal__isnull=True) | Q(reversal__date__gt=as_of)
        )
    else:
        corrections = corrections.filter(reversal__isnull=True)
    used = allocations(record, as_of).aggregate(total=Sum("amount"))["total"] or ZERO
    corrected = corrections.aggregate(total=Sum("amount"))["total"] or ZERO
    return record.amount - used - corrected


def validate_effective_date(record, date):
    """A correction cannot rewrite the order of existing settlement events."""
    from django.db.models import Max
    from rest_framework.exceptions import ValidationError

    relation = PartyPaymentAllocation.objects.filter(
        Q(payment=record) | Q(document=record)
    )
    dates = relation.aggregate(
        allocated=Max("effective_date"), released=Max("release__date")
    )
    correction_date = record.adjustments.aggregate(last=Max("date"))["last"]
    if record.transaction_type == "INVOICE":
        dates["recognized"] = record.recognitions.aggregate(last=Max("date"))["last"]
        dates["fulfilment"] = record.fulfilments.aggregate(last=Max("date"))["last"]
    if any(
        value and date < value
        for value in [record.date, correction_date, *dates.values()]
    ):
        raise ValidationError(
            "Effective date must not precede this transaction’s existing settlements or corrections."
        )


def remaining_balances(records, as_of=None):
    """Resolve a page or complete statement in three queries, not three per row."""
    from .models import PartyLedgerTransaction

    amounts = {
        r.pk: r.amount
        for r in records
        if r.transaction_type in ("INVOICE", "BILL", "PAYMENT")
    }
    if not amounts:
        return {}
    links = PartyPaymentAllocation.objects.filter(
        Q(payment_id__in=amounts) | Q(document_id__in=amounts)
    )
    corrections = PartyLedgerTransaction.objects.filter(
        related_transaction_id__in=amounts, transaction_type__in=("CREDIT", "REFUND")
    )
    reversed_rows = PartyLedgerTransaction.objects.filter(reverses_id__in=amounts)
    if as_of:
        links = links.filter(effective_date__lte=as_of).filter(
            Q(release__isnull=True) | Q(release__date__gt=as_of)
        )
        corrections = corrections.filter(date__lte=as_of).filter(
            Q(reversal__isnull=True) | Q(reversal__date__gt=as_of)
        )
        reversed_rows = reversed_rows.filter(date__lte=as_of)
    else:
        links = links.filter(release__isnull=True)
        corrections = corrections.filter(reversal__isnull=True)
    for payment, document, amount in links.values_list(
        "payment_id", "document_id", "amount"
    ):
        for key in (payment, document):
            if key in amounts:
                amounts[key] -= amount
    for row in corrections.values("related_transaction_id").annotate(
        total=Sum("amount")
    ):
        amounts[row["related_transaction_id"]] -= row["total"]
    for key in reversed_rows.values_list("reverses_id", flat=True):
        amounts[key] = ZERO
    return amounts
