"""The sole posting engine; database triggers also guard final journal integrity."""

from django.db import transaction
from django.utils import timezone
from ledger import models as m
from .common import make, check, amount, Conflict, Unconfigured, business_date


def lock_period(day):
    day = business_date(day)
    period = (
        m.AccountingPeriod.objects.select_for_update()
        .filter(start__lte=day, end__gte=day)
        .first()
    )
    if not period:
        raise Unconfigured("Create the financial year and accounting period first.")
    if period.closed:
        raise Conflict("Accounting period is closed.")
    return period


def account_currency(user, account, currency):
    check(
        account.active and currency.active,
        "Posting account and currency must be active.",
    )
    check(
        not m.LedgerAccount.objects.filter(parent=account).exists(),
        "Post only to a leaf account.",
    )
    obj, _ = m.AccountCurrency.objects.get_or_create(
        account=account,
        currency=currency,
        defaults={
            "created_by": user,
            "created_by_name": user.get_full_name() or user.username,
        },
    )
    return obj


@transaction.atomic
def journal(user, record, event, rows, day=None, reversal_of=None):
    """Internal engine: callers perform domain-specific permissions and source locks."""
    day = day or record.date
    period = lock_period(day)
    check(
        m.Currency.objects.get(pk=record.currency_id).active,
        "Posting currency is inactive.",
    )
    if m.JournalEntry.objects.filter(source=record, event=event).exists():
        raise Conflict("This source event was already posted.")
    prepared = []
    for account, debit, credit, party, staff in rows:
        if isinstance(account, m.LedgerAccount):
            account = account_currency(user, account, record.currency)
        check(
            account.currency_id == record.currency_id, "Journal currencies must match."
        )
        check(
            account.account.active
            and not m.LedgerAccount.objects.filter(parent=account.account).exists(),
            "Posting account must be an active leaf.",
        )
        debit, credit = amount(debit, record.currency), amount(credit, record.currency)
        if debit == credit == 0:
            continue
        check(
            (debit > 0 and credit == 0) or (credit > 0 and debit == 0),
            "Journal lines must have exactly one positive side.",
        )
        prepared.append(
            dict(account=account, debit=debit, credit=credit, party=party, staff=staff)
        )
    check(
        len(prepared) >= 2
        and sum(x["debit"] for x in prepared) == sum(x["credit"] for x in prepared),
        "Journal is not balanced.",
    )
    entry = make(
        m.JournalEntry,
        user,
        date=day,
        period=period,
        currency=record.currency,
        description=record.description or event,
        source=record,
        event=event,
        reversal_of=reversal_of,
    )
    for row in prepared:
        make(m.JournalLine, user, entry=entry, **row)
    entry.posted_at = timezone.now()
    entry.save(update_fields=["posted_at"])
    return entry
