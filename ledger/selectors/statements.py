"""Reconciliation and Statement: frozen rows from one serialized currency snapshot."""

from decimal import Decimal
from django.db import transaction
from ledger import models as m
from ledger.services.common import (
    full_scope,
    check,
    make,
    client_account,
    digest,
    primitive,
)
from ledger.services.settlement import book_lock, invoice_due, source_available


@transaction.atomic
def generate(user, party, currency, date_from, date_to):
    full_scope(user, "view_clientstatement")
    check(date_from <= date_to, "End date must not precede start date.")
    book_lock(currency)
    account = client_account(user, party, currency, lock=True)
    all_rows = list(
        account.movements.select_related("source", "journal").order_by(
            "date", "created_at", "id"
        )
    )
    openings = [r for r in all_rows if r.kind == "OPENING"]
    if openings:
        check(
            date_from >= min(r.date for r in openings),
            "Selected range precedes available opening history.",
        )
    opening = sum(
        (
            r.debit - r.credit
            for r in all_rows
            if r.date < date_from or r.kind == "OPENING" and r.date == date_from
        ),
        Decimal(0),
    )
    period = [
        r
        for r in all_rows
        if date_from <= r.date <= date_to
        and not (r.kind == "OPENING" and r.date == date_from)
    ]
    balance = opening
    rows = []
    invoiced = Decimal(0)
    cash = Decimal(0)
    withheld = Decimal(0)
    debits = Decimal(0)
    credits = Decimal(0)
    for row in period:
        balance += row.debit - row.credit
        if row.kind == "INVOICE":
            invoiced += row.debit
        elif row.kind == "RECEIPT":
            cash += row.credit
        elif row.kind == "WITHHOLDING":
            withheld += row.credit
        else:
            debits += row.debit
            credits += row.credit
        rows.append(
            dict(
                id=str(row.pk),
                date=str(row.date),
                kind=row.kind,
                record=str(row.source_id),
                reference=row.source.reference,
                description=row.source.description,
                debit=str(row.debit),
                credit=str(row.credit),
                running_balance=str(balance),
            )
        )
    outstanding = []
    for invoice in m.Invoice.objects.filter(
        record__party=party,
        record__currency=currency,
        record__status="POSTED",
        record__date__lte=date_to,
    ).select_related("record"):
        outstanding.append(
            dict(
                invoice=str(invoice.record_id),
                reference=invoice.record.reference,
                outstanding=str(invoice_due(invoice, date_to)),
            )
        )
    unallocated = []
    for source in m.SettlementSource.objects.filter(
        account=account,
        record__status__in=["POSTED", "REVERSED"],
        record__date__lte=date_to,
    ).select_related("record"):
        unallocated.append(
            dict(
                source=str(source.pk),
                record=str(source.record_id),
                amount=str(source_available(source, date_to)),
            )
        )
    data = dict(
        title="Reconciliation and Statement",
        client=str(party.pk),
        client_name=str(party),
        currency=currency.code,
        date_from=str(date_from),
        date_to=str(date_to),
        opening_balance=str(opening),
        invoice_total=str(invoiced),
        cash_received_total=str(cash),
        withheld_total=str(withheld),
        debit_adjustments=str(debits),
        credit_adjustments=str(credits),
        closing_balance=str(balance),
        balance_due=str(max(balance, 0)),
        credit_held=str(max(-balance, 0)),
        rows=rows,
        outstanding=outstanding,
        unallocated=unallocated,
    )
    return make(
        m.StatementSnapshot,
        user,
        account=account,
        date_from=date_from,
        date_to=date_to,
        data=primitive(data),
        fingerprint=digest(data),
    )
