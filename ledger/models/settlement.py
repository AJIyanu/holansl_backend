"""Dated client movements, allocation events and immutable report snapshots."""

from django.db import models
from django.db.models import Q
from .base import Record, money


class ClientAccount(Record):
    party = models.ForeignKey("crm.Party", on_delete=models.PROTECT)
    currency = models.ForeignKey("ledger.Currency", on_delete=models.PROTECT)

    class Meta(Record.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["party", "currency"], name="ledger_client_currency"
            )
        ]


class ClientMovement(Record):
    account = models.ForeignKey(
        ClientAccount, on_delete=models.PROTECT, related_name="movements"
    )
    source = models.ForeignKey("ledger.FinancialRecord", on_delete=models.PROTECT)
    journal = models.ForeignKey("ledger.JournalEntry", on_delete=models.PROTECT)
    date = models.DateField()
    kind = models.CharField(max_length=30)
    debit = money(default=0)
    credit = money(default=0)

    class Meta(Record.Meta):
        indexes = [models.Index(fields=["account", "date", "created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "journal"], name="ledger_movement_journal"
            ),
            models.CheckConstraint(
                condition=Q(debit__gt=0, credit=0) | Q(credit__gt=0, debit=0),
                name="ledger_movement_side",
            ),
        ]


class SettlementSource(Record):
    record = models.OneToOneField(
        "ledger.FinancialRecord",
        on_delete=models.PROTECT,
        related_name="settlement_source",
    )
    account = models.ForeignKey(ClientAccount, on_delete=models.PROTECT)
    amount = money()

    class Meta(Record.Meta):
        permissions = [
            ("allocate_settlementsource", "Allocate and release settlement"),
            ("allocate_receipt", "Allocate and release receipts"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="ledger_settlement_positive"
            )
        ]


class AllocationEvent(Record):
    source = models.ForeignKey(
        SettlementSource, on_delete=models.PROTECT, related_name="allocations"
    )
    invoice = models.ForeignKey(
        "ledger.Invoice", on_delete=models.PROTECT, related_name="allocations"
    )
    date = models.DateField()
    amount = money()
    release_of = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, related_name="releases"
    )
    reason = models.TextField(blank=True)
    tax_amount = money(default=0)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0, tax_amount__gte=0),
                name="ledger_allocation_positive",
            )
        ]


class StatementSnapshot(Record):
    account = models.ForeignKey(ClientAccount, on_delete=models.PROTECT)
    date_from = models.DateField()
    date_to = models.DateField()
    data = models.JSONField()
    fingerprint = models.CharField(max_length=64)


class RefundApplication(Record):
    payment = models.ForeignKey(
        "ledger.Payment", on_delete=models.PROTECT, related_name="refund_applications"
    )
    source = models.ForeignKey(
        SettlementSource, on_delete=models.PROTECT, related_name="refund_applications"
    )
    amount = money()

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="ledger_refund_positive"
            ),
            models.UniqueConstraint(
                fields=["payment", "source"], name="ledger_refund_source"
            ),
        ]
