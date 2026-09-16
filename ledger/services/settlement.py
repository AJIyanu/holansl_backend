"""Append-only allocations. Applying a receipt never receives the money again."""

from decimal import Decimal
from django.db import transaction
from ledger import models as m
from .common import (
    require,
    get_record,
    make,
    check,
    amount,
    client_account,
    event,
    business_date,
)
from .posting import lock_period


def book_lock(currency):
    return m.Currency.objects.select_for_update().get(pk=currency.pk)


def applied(qs, day=None):
    if day:
        qs = qs.filter(date__lte=day)
    return sum(((-r.amount if r.release_of_id else r.amount) for r in qs), Decimal(0))


def invoice_due(invoice, day=None):
    return invoice.record.amount - applied(invoice.allocations.all(), day)


def source_available(source, day=None):
    reversed_entry = source.record.journals.filter(event="REVERSE").first()
    if reversed_entry and (day is None or reversed_entry.date <= day):
        return Decimal(0)
    refunds = source.refund_applications.filter(
        payment__record__status__in=["POSTED", "REVERSED"]
    )
    if day:
        refunds = refunds.filter(payment__record__date__lte=day)
    return (
        source.amount
        - applied(source.allocations.all(), day)
        - sum(
            (
                r.amount
                for r in refunds
                if not r.payment.record.journals.filter(
                    event="REVERSE", **({"date__lte": day} if day else {})
                ).exists()
            ),
            Decimal(0),
        )
    )


def tax_collected(line):
    rows = m.TaxCollectionAllocation.objects.filter(line=line).select_related(
        "allocation"
    )
    return sum(
        ((-r.amount if r.allocation.release_of_id else r.amount) for r in rows),
        Decimal(0),
    )


def tax_withheld(line):
    return sum(
        (
            r.record.amount
            for r in line.withholdings.filter(record__status="POSTED").select_related(
                "record"
            )
        ),
        Decimal(0),
    )


def credited(line):
    return sum(
        (
            r.tax
            for r in m.CreditNoteLine.objects.filter(
                line=line, credit__record__status="POSTED"
            )
        ),
        Decimal(0),
    )


def movement(user, record, journal, kind, debit=0, credit=0):
    account = client_account(user, record.party, record.currency, lock=True)
    if debit or credit:
        make(
            m.ClientMovement,
            user,
            account=account,
            source=record,
            journal=journal,
            date=journal.date,
            kind=kind,
            debit=debit,
            credit=credit,
        )
    return account


def source_for(user, record, account):
    return make(
        m.SettlementSource, user, record=record, account=account, amount=record.amount
    )


@transaction.atomic
def allocate(user, source, invoice, value, day, tax_lines=None, *, internal=False):
    # Internal calls still execute full actor/source visibility; authority comes from
    # the owning posting action, rather than requiring an unrelated allocation grant.
    receipt = source.record.kind == "RECEIPT"
    if not internal:
        require(user, "allocate_receipt" if receipt else "allocate_settlementsource")
    source_record = get_record(user, source.record_id)
    target = get_record(user, invoice.record_id)
    book_lock(source_record.currency)
    lock_period(day)
    client_account(user, source_record.party, source_record.currency, lock=True)
    source = m.SettlementSource.objects.select_for_update().get(pk=source.pk)
    invoice = m.Invoice.objects.select_for_update().get(pk=invoice.pk)
    check(
        source_record.status == "POSTED" and target.status == "POSTED",
        "Both settlement source and invoice must be posted.",
    )
    check(
        source.account.party_id == target.party_id
        and source.account.currency_id == target.currency_id,
        "Allocation client/currency must match.",
    )
    day = business_date(day)
    check(
        day >= source_record.date and day >= target.date,
        "Allocation cannot precede its source or invoice.",
    )
    # Avoid creating a historical over-allocation before subsequent release events.
    latest = (
        m.AllocationEvent.objects.filter(source__account=source.account)
        .order_by("-date")
        .first()
    )
    check(
        not latest or day >= latest.date,
        "Allocation changes cannot predate the latest client allocation event.",
    )
    value = amount(value, target.currency)
    check(
        0 < value <= source_available(source) and value <= invoice_due(invoice),
        "Amount exceeds available settlement or outstanding invoice.",
    )
    tax_rows = []
    tax_total = Decimal(0)
    seen = set()
    for row in tax_lines or []:
        line = row["line"]
        tax_value = amount(row["amount"], target.currency)
        check(
            line.invoice_id == invoice.pk and line.pk not in seen,
            "Invalid or duplicate tax component.",
        )
        seen.add(line.pk)
        check(
            0
            <= tax_value
            <= line.tax - tax_collected(line) - tax_withheld(line) - credited(line),
            "Tax allocation exceeds remaining component.",
        )
        tax_rows.append((line, tax_value))
        tax_total += tax_value
    check(tax_total <= value, "Tax cannot exceed cash applied.")
    if receipt and source_record.receipt.purpose == "CLIENT":
        remaining_tax = sum(
            (
                line.tax - tax_collected(line) - tax_withheld(line) - credited(line)
                for line in invoice.lines.all()
            ),
            Decimal(0),
        )
        if value == invoice_due(invoice):
            check(
                tax_total == remaining_tax,
                "Final cash settlement must explicitly allocate all remaining invoice tax.",
            )
    else:
        check(not tax_rows, "Noncash settlement must not be recorded as tax collected.")
    obj = make(
        m.AllocationEvent,
        user,
        source=source,
        invoice=invoice,
        date=day,
        amount=value,
        tax_amount=tax_total,
    )
    for line, tax_value in tax_rows:
        make(
            m.TaxCollectionAllocation, user, allocation=obj, line=line, amount=tax_value
        )
    event(user, "allocated", target, source=source.pk, allocation=obj.pk)
    from ledger.integrations.procurement import tracking_event

    tracking_event(
        user,
        target,
        "PAYMENT_RECEIVED" if receipt else "INVOICE_SETTLEMENT_UPDATED",
        f"Ledger settlement on {target.reference}; allocation {obj.pk}.",
    )
    return obj


@transaction.atomic
def release(user, allocation, value, day, reason, *, internal=False):
    source = allocation.source
    if not internal:
        require(
            user,
            "allocate_receipt"
            if source.record.kind == "RECEIPT"
            else "allocate_settlementsource",
        )
    record = get_record(user, allocation.invoice.record_id)
    get_record(user, source.record_id)
    book_lock(record.currency)
    lock_period(day)
    check(
        not allocation.release_of_id and reason.strip(),
        "Release an original allocation with a reason.",
    )
    allocation = m.AllocationEvent.objects.select_for_update().get(pk=allocation.pk)
    remaining = allocation.amount - sum(
        (x.amount for x in allocation.releases.all()), Decimal(0)
    )
    value = amount(value, record.currency)
    check(0 < value <= remaining, "Release exceeds active allocation.")
    latest = (
        m.AllocationEvent.objects.filter(source__account=source.account)
        .order_by("-date")
        .first()
    )
    check(
        day >= allocation.date and (not latest or day >= latest.date),
        "Release cannot predate the latest allocation.",
    )
    # Release all active tax only on the final release; partial releases are
    # proportional, rounded once with final remainder preserved.
    tax_remaining = allocation.tax_amount - sum(
        (x.tax_amount for x in allocation.releases.all()), Decimal(0)
    )
    tax_value = (
        tax_remaining
        if value == remaining
        else amount(allocation.tax_amount * value / allocation.amount, record.currency)
    )
    obj = make(
        m.AllocationEvent,
        user,
        source=source,
        invoice=allocation.invoice,
        date=day,
        amount=value,
        release_of=allocation,
        reason=reason,
        tax_amount=tax_value,
    )
    remaining_to_split = tax_value
    components = list(allocation.tax_collections.all())
    for idx, row in enumerate(components):
        prev = sum(
            (
                r.amount
                for r in m.TaxCollectionAllocation.objects.filter(
                    allocation__release_of=allocation, line=row.line
                )
            ),
            Decimal(0),
        )
        capacity = row.amount - prev
        part = (
            min(
                capacity,
                remaining_to_split
                if idx == len(components) - 1
                else amount(
                    tax_value * row.amount / allocation.tax_amount, record.currency
                ),
            )
            if allocation.tax_amount
            else Decimal(0)
        )
        make(
            m.TaxCollectionAllocation, user, allocation=obj, line=row.line, amount=part
        )
        remaining_to_split -= part
    check(remaining_to_split == 0, "Tax release cannot be apportioned exactly.")
    event(user, "allocation_released", record, reason, allocation=obj.pk)
    from ledger.integrations.procurement import tracking_event

    tracking_event(
        user,
        record,
        "PAYMENT_ALLOCATION_RELEASED",
        f"Ledger allocation released: {reason}",
    )
    return obj
