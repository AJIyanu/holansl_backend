"""Reversal journals retain cash evidence and unwind allocations atomically."""

from decimal import Decimal
from django.db import transaction
from ledger import models as m
from .common import get_record, require, check, event, business_date
from .posting import journal, lock_period
from .settlement import book_lock, release, movement


@transaction.atomic
def reverse(user, record, day, reason):
    record = get_record(user, record.pk)
    require(
        user,
        "reverse_journalentry"
        if record.kind == "MANUAL"
        else "reverse_financialrecord",
    )
    check(reason.strip(), "Reversal reason is required.")
    check(
        record.kind
        in {
            "MANUAL",
            "RECEIPT",
            "PAYMENT",
            "WITHHOLDING",
            "EXPENSE",
            "SUPPLIER_BILL",
            "CLEARANCE",
        },
        "Use invoice cancellation/credit or the owning authorization workflow for this record.",
    )
    day = business_date(day)
    check(day >= record.date, "Reversal cannot predate original posting.")
    book_lock(record.currency)
    lock_period(day)
    record = get_record(user, record.pk, lock=True)
    check(record.status == "POSTED", "Record must be posted and not already reversed.")
    original = record.journals.get(event="POST")
    if record.kind in {"EXPENSE", "SUPPLIER_BILL"}:
        check(
            not record.outgoing_payments.filter(record__status="POSTED").exists(),
            "Reverse payments before reversing the obligation.",
        )
        check(
            not record.incoming_receipts.filter(record__status="POSTED").exists(),
            "Resolve supplier refunds before reversing the bill.",
        )
    if record.kind == "PAYMENT" and record.payment.purpose == "ADVANCE":
        target = record.payment.target
        check(
            not target.incoming_receipts.filter(record__status="POSTED").exists()
            and not target.advance.clearances.filter(record__status="POSTED").exists(),
            "Reverse returns/clearances before reversing advance funding.",
        )
    if record.kind == "PAYMENT" and record.payment.purpose == "BILL":
        check(
            not record.payment.target.incoming_receipts.filter(
                record__status="POSTED"
            ).exists(),
            "Reverse supplier refunds before reversing their verified payment.",
        )
    if hasattr(record, "settlement_source"):
        source = record.settlement_source
        check(
            not source.refund_applications.filter(
                payment__record__status="POSTED"
            ).exists(),
            "Reverse client refunds before reversing their source.",
        )
        for application in list(
            source.allocations.filter(release_of=None).order_by("date", "created_at")
        ):
            remaining = application.amount - sum(
                (x.amount for x in application.releases.all()), Decimal(0)
            )
            if remaining:
                release(user, application, remaining, day, reason, internal=True)
    rows = [
        (r.account, r.credit, r.debit, r.party, r.staff)
        for r in original.lines.select_related("account", "party", "staff")
    ]
    entry = journal(user, record, "REVERSE", rows, day, original)
    previous = m.ClientMovement.objects.filter(source=record, journal=original).first()
    if previous:
        movement(
            user,
            record,
            entry,
            record.kind + "_REVERSAL",
            debit=previous.credit,
            credit=previous.debit,
        )
    record.status = "REVERSED"
    record.save(update_fields=["status"])
    event(user, "reversed", record, reason, journal=entry.pk, date=day)
    return record
