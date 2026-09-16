"""Native-currency accounts and immutable balanced journals."""

from django.conf import settings
from django.db import models
from django.db.models import Q, F
from .base import Record, money


class Currency(Record):
    code = models.CharField(max_length=3, unique=True)
    name = models.CharField(max_length=80)
    symbol = models.CharField(max_length=8)
    minor_units = models.PositiveSmallIntegerField(default=2)
    active = models.BooleanField(default=True)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(minor_units__lte=4), name="ledger_currency_precision"
            )
        ]


class LedgerAccount(Record):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    classification = models.CharField(
        max_length=12,
        choices=[
            (v, v.title())
            for v in ["ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE"]
        ],
    )
    purpose = models.CharField(max_length=30, blank=True)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True)
    active = models.BooleanField(default=True)


class AccountCurrency(Record):
    account = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name="currencies"
    )
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)

    class Meta(Record.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["account", "currency"], name="ledger_account_currency"
            )
        ]


class CashAccount(Record):
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=8, choices=[("BANK", "Bank"), ("CASH", "Cash")])
    account = models.OneToOneField(AccountCurrency, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)


class FinancialYear(Record):
    year = models.PositiveIntegerField(unique=True)
    start = models.DateField()
    end = models.DateField()


class AccountingPeriod(Record):
    year = models.ForeignKey(
        FinancialYear, on_delete=models.PROTECT, related_name="periods"
    )
    start = models.DateField(unique=True)
    end = models.DateField()
    closed = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=0)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(end__gte=F("start")), name="ledger_period_dates"
            )
        ]
        permissions = [
            ("close_accountingperiod", "Close accounting period"),
            ("reopen_accountingperiod", "Reopen accounting period"),
        ]


class PostingPolicyVersion(Record):
    name = models.CharField(max_length=120, unique=True)
    # Typed protected mappings; no client-provided arbitrary posting JSON.
    receivable = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name="+"
    )
    payable = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name="+"
    )
    deferred_revenue = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name="+"
    )
    revenue = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name="+"
    )
    advance = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name="+"
    )
    opening_equity = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name="+"
    )
    recognition = models.CharField(
        max_length=12,
        choices=[("ACCEPTANCE", "Client acceptance"), ("DELIVERY", "Delivery")],
        default="ACCEPTANCE",
    )
    self_approval = models.BooleanField(default=True)
    # Decimal HALF_UP per-line, minor-unit totals; a new version changes future records only.
    rounding = models.CharField(max_length=20, default="HALF_UP_LINE", editable=False)


class JournalEntry(Record):
    date = models.DateField()
    period = models.ForeignKey(AccountingPeriod, on_delete=models.PROTECT)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    description = models.TextField()
    source = models.ForeignKey(
        "ledger.FinancialRecord",
        on_delete=models.PROTECT,
        null=True,
        related_name="journals",
    )
    event = models.CharField(max_length=80)
    posted_at = models.DateTimeField(null=True)
    reversal_of = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, related_name="reversal"
    )

    class Meta(Record.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["source", "event"], name="ledger_source_posting"
            )
        ]
        permissions = [
            ("post_journalentry", "Post manual journals"),
            ("reverse_journalentry", "Reverse manual journals"),
            ("approve_journalentry", "Approve manual journals"),
        ]


class JournalLine(Record):
    entry = models.ForeignKey(
        JournalEntry, on_delete=models.PROTECT, related_name="lines"
    )
    account = models.ForeignKey(AccountCurrency, on_delete=models.PROTECT)
    party = models.ForeignKey("crm.Party", on_delete=models.PROTECT, null=True)
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+"
    )
    debit = money(default=0)
    credit = money(default=0)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=(Q(debit__gt=0, credit=0) | Q(credit__gt=0, debit=0)),
                name="ledger_line_one_side",
            )
        ]
