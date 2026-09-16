"""Explicit tax policy, client authorization and remittance evidence."""

from django.db import models
from django.db.models import Q, F
from .base import Record, money


class TaxType(Record):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    authority = models.CharField(max_length=150)


class TaxPolicyVersion(Record):
    tax_type = models.ForeignKey(TaxType, on_delete=models.PROTECT)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    rate = models.DecimalField(max_digits=7, decimal_places=4)
    liability_account = models.ForeignKey(
        "ledger.LedgerAccount", on_delete=models.PROTECT
    )
    withheld_account = models.ForeignKey(
        "ledger.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
    )
    treatment = models.CharField(
        max_length=25,
        choices=[
            ("CLIENT_REMITTANCE", "Client remits invoice tax"),
            ("TAX_CREDIT", "Withholding tax credit"),
        ],
    )
    verified_reference = models.TextField()
    # No statutory default: operator records the verified applicability reference.

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(rate__gte=0, rate__lte=100), name="ledger_tax_rate"
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True)
                | Q(effective_to__gte=F("effective_from")),
                name="ledger_tax_dates",
            ),
        ]


class ClientWithholdingAuthorization(Record):
    party = models.ForeignKey("crm.Party", on_delete=models.PROTECT)
    tax_type = models.ForeignKey(TaxType, on_delete=models.PROTECT)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)
    evidence = models.ForeignKey("ledger.LedgerDocument", on_delete=models.PROTECT)
    reason = models.TextField()
    revoked_from = models.DateField(null=True)

    class Meta(Record.Meta):
        permissions = [
            (
                "authorize_clientwithholdingauthorization",
                "Authorize or revoke client withholding",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True)
                | Q(effective_to__gte=F("effective_from")),
                name="ledger_auth_dates",
            )
        ]


class WithholdingRecord(Record):
    record = models.OneToOneField(
        "ledger.FinancialRecord", on_delete=models.PROTECT, related_name="withholding"
    )
    line = models.ForeignKey(
        "ledger.InvoiceLine", on_delete=models.PROTECT, related_name="withholdings"
    )
    authorization = models.ForeignKey(
        ClientWithholdingAuthorization, on_delete=models.PROTECT
    )
    certificate_reference = models.CharField(max_length=150)

    class Meta(Record.Meta):
        default_permissions = ()


class TaxCollectionAllocation(Record):
    allocation = models.ForeignKey(
        "ledger.AllocationEvent",
        on_delete=models.PROTECT,
        related_name="tax_collections",
    )
    line = models.ForeignKey("ledger.InvoiceLine", on_delete=models.PROTECT)
    amount = money()

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0), name="ledger_tax_collection_positive"
            )
        ]


class TaxRemittance(Record):
    record = models.OneToOneField(
        "ledger.FinancialRecord",
        on_delete=models.PROTECT,
        related_name="tax_remittance",
    )
    tax_policy = models.ForeignKey(TaxPolicyVersion, on_delete=models.PROTECT)
    period_from = models.DateField()
    period_to = models.DateField()

    class Meta(Record.Meta):
        default_permissions = ()


class ClientRemittanceEvidence(Record):
    withholding = models.ForeignKey(
        WithholdingRecord, on_delete=models.PROTECT, related_name="remittance_evidence"
    )
    document = models.ForeignKey("ledger.LedgerDocument", on_delete=models.PROTECT)
    reference = models.CharField(max_length=150)
    verified_on = models.DateField()
