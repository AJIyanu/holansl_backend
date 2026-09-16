"""Audit, private artifacts and durable workflow intent."""

from django.db import models
from .base import Record


class LedgerEvent(Record):
    record = models.ForeignKey(
        "ledger.FinancialRecord",
        on_delete=models.PROTECT,
        null=True,
        related_name="events",
    )
    action = models.CharField(max_length=60)
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)


class LedgerOperation(Record):
    key = models.UUIDField(unique=True)
    fingerprint = models.CharField(max_length=64)
    result = models.JSONField(default=dict)


class LedgerDocument(Record):
    record = models.ForeignKey(
        "ledger.FinancialRecord",
        on_delete=models.PROTECT,
        null=True,
        related_name="documents",
    )
    party = models.ForeignKey("crm.Party", on_delete=models.PROTECT, null=True)
    statement = models.ForeignKey(
        "ledger.StatementSnapshot", on_delete=models.PROTECT, null=True
    )
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=120)
    checksum = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField()
    storage_provider = models.CharField(max_length=30)
    external_file_id = models.TextField()
    storage_path = models.TextField(blank=True)
    fingerprint = models.CharField(max_length=64, blank=True)

    class Meta(Record.Meta):
        permissions = [("download_ledgerdocument", "Download private ledger evidence")]


class IntegrationIntent(Record):
    approval = models.ForeignKey("ledger.ApprovalRequest", on_delete=models.PROTECT)
    event = models.CharField(max_length=20)
    processed_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    failure = models.CharField(max_length=80, blank=True)

    class Meta(Record.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["approval", "event"], name="ledger_integration_event"
            )
        ]


class ApprovalRoutingPolicy(Record):
    department = models.OneToOneField("accounts.Department", on_delete=models.PROTECT)
    enabled = models.BooleanField(default=True)
    channels = models.JSONField(default=list)
    reason = models.TextField()
