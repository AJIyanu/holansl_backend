"""Typed finance records and immutable revisions."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from .base import Record, money

KINDS = [
    "INVOICE",
    "RECEIPT",
    "PAYMENT",
    "SUPPLIER_BILL",
    "EXPENSE",
    "ADVANCE",
    "CLEARANCE",
    "CREDIT",
    "WITHHOLDING",
    "TAX_REMITTANCE",
    "MANUAL",
    "OPENING",
]
RESOURCE = {
    "INVOICE": "invoice",
    "RECEIPT": "receipt",
    "PAYMENT": "payment",
    "SUPPLIER_BILL": "supplierbill",
    "EXPENSE": "expense",
    "ADVANCE": "advance",
    "CLEARANCE": "advanceclearance",
    "CREDIT": "creditnote",
    "WITHHOLDING": "withholdingrecord",
    "TAX_REMITTANCE": "taxremittance",
    "MANUAL": "journalentry",
    "OPENING": "openingbalance",
}


class FinancialRecord(Record):
    supersedes = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, related_name="superseded_by"
    )
    kind = models.CharField(
        max_length=20, choices=[(k, k.replace("_", " ").title()) for k in KINDS]
    )
    party = models.ForeignKey(
        "crm.Party",
        on_delete=models.PROTECT,
        null=True,
        related_name="financial_records",
    )
    party_name = models.CharField(max_length=255, blank=True)
    currency = models.ForeignKey("ledger.Currency", on_delete=models.PROTECT)
    policy = models.ForeignKey("ledger.PostingPolicyVersion", on_delete=models.PROTECT)
    department = models.ForeignKey("accounts.Department", on_delete=models.PROTECT)
    request = models.ForeignKey(
        "procurement.ProcurementRequest",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    date = models.DateField()
    description = models.TextField(blank=True)
    reference = models.CharField(max_length=150, blank=True)
    status = models.CharField(
        max_length=12,
        default="DRAFT",
        choices=[
            (v, v.title())
            for v in [
                "DRAFT",
                "SUBMITTED",
                "APPROVED",
                "REJECTED",
                "POSTED",
                "CANCELLED",
                "REVERSED",
                "SUPERSEDED",
            ]
        ],
    )
    version = models.PositiveIntegerField(default=1)
    amount = money(default=0)
    net_amount = money(default=0)
    tax_amount = money(default=0)
    posted_at = models.DateTimeField(null=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+"
    )
    evidence = models.ForeignKey(
        "ledger.LedgerDocument", on_delete=models.PROTECT, null=True, related_name="+"
    )
    registered_version = models.OneToOneField(
        "procurement.DocumentVersion",
        on_delete=models.PROTECT,
        null=True,
        related_name="financial_source",
    )

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0, net_amount__gte=0, tax_amount__gte=0),
                name="ledger_record_amounts",
            )
        ]
        permissions = [
            (f"{action}_{resource}", f"{action.title()} {resource}")
            for resource in RESOURCE.values()
            if resource != "journalentry"
            for action in ["view", "add", "change", "approve", "post"]
        ] + [
            ("viewall_financialrecord", "View all financial records"),
            ("viewdepartment_financialrecord", "View Accounts department records"),
            ("cancel_invoice", "Cancel eligible invoice quantities"),
            ("generate_financialrecord", "Generate financial documents"),
            ("reverse_financialrecord", "Reverse eligible financial records"),
            ("approve_quotation", "Approve procurement quotation revisions"),
            ("view_clientstatement", "View Reconciliation and Statement"),
            ("export_clientstatement", "Export Reconciliation and Statement"),
            ("view_taxreport", "View tax reports"),
        ]


class Invoice(Record):
    record = models.OneToOneField(
        FinancialRecord, on_delete=models.PROTECT, related_name="invoice"
    )
    due_date = models.DateField(null=True)

    class Meta(Record.Meta):
        default_permissions = ()


class InvoiceLine(Record):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="lines")
    description = models.TextField()
    unit = models.CharField(max_length=40)
    quantity = money()
    unit_price = money()
    discount = money(default=0)
    net = money()
    tax = money(default=0)
    total = money()
    item = models.ForeignKey(
        "procurement.RequestItem", on_delete=models.PROTECT, null=True
    )
    quote_line = models.ForeignKey(
        "procurement.ClientQuotationLine", on_delete=models.PROTECT, null=True
    )
    tax_policy = models.ForeignKey(
        "ledger.TaxPolicyVersion", on_delete=models.PROTECT, null=True
    )

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    quantity__gt=0,
                    unit_price__gte=0,
                    discount__gte=0,
                    net__gte=0,
                    tax__gte=0,
                    total__gte=0,
                ),
                name="ledger_invoice_line_amounts",
            )
        ]


class InvoiceOrderLink(Record):
    line = models.ForeignKey(
        InvoiceLine, on_delete=models.PROTECT, related_name="order_links"
    )
    po_line = models.ForeignKey(
        "procurement.ClientPurchaseOrderLine",
        on_delete=models.PROTECT,
        related_name="invoice_links",
    )
    quantity = money()
    evidence = models.TextField()

    class Meta(Record.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["line", "po_line"], name="ledger_invoice_order_link"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="ledger_order_qty"
            ),
        ]


class Receipt(Record):
    record = models.OneToOneField(
        FinancialRecord, on_delete=models.PROTECT, related_name="receipt"
    )
    payment_reference = models.CharField(max_length=150, blank=True)
    cash_account = models.ForeignKey("ledger.CashAccount", on_delete=models.PROTECT)
    purpose = models.CharField(
        max_length=20,
        choices=[
            (k, k.title()) for k in ["CLIENT", "ADVANCE_RETURN", "SUPPLIER_REFUND"]
        ],
    )
    target = models.ForeignKey(
        FinancialRecord,
        on_delete=models.PROTECT,
        null=True,
        related_name="incoming_receipts",
    )

    class Meta(Record.Meta):
        default_permissions = ()


class Payment(Record):
    record = models.OneToOneField(
        FinancialRecord, on_delete=models.PROTECT, related_name="payment"
    )
    cash_account = models.ForeignKey("ledger.CashAccount", on_delete=models.PROTECT)
    purpose = models.CharField(
        max_length=20,
        choices=[
            (k, k.title())
            for k in ["BILL", "EXPENSE", "ADVANCE", "CLIENT_REFUND", "TAX_REMITTANCE"]
        ],
    )
    target = models.ForeignKey(
        FinancialRecord,
        on_delete=models.PROTECT,
        null=True,
        related_name="outgoing_payments",
    )

    class Meta(Record.Meta):
        default_permissions = ()


class CostLine(Record):
    record = models.ForeignKey(
        FinancialRecord, on_delete=models.PROTECT, related_name="cost_lines"
    )
    account = models.ForeignKey("ledger.LedgerAccount", on_delete=models.PROTECT)
    amount = money()
    description = models.TextField()
    evidence = models.ForeignKey("ledger.LedgerDocument", on_delete=models.PROTECT)
    procurement_cost = models.ForeignKey(
        "procurement.ProcurementCost", on_delete=models.PROTECT, null=True
    )

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="ledger_cost_amount")
        ]


class Advance(Record):
    record = models.OneToOneField(
        FinancialRecord, on_delete=models.PROTECT, related_name="advance"
    )
    custodian = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    class Meta(Record.Meta):
        default_permissions = ()


class AdvanceClearance(Record):
    record = models.OneToOneField(
        FinancialRecord, on_delete=models.PROTECT, related_name="clearance"
    )
    advance = models.ForeignKey(
        Advance, on_delete=models.PROTECT, related_name="clearances"
    )

    class Meta(Record.Meta):
        default_permissions = ()


class CreditNote(Record):
    record = models.OneToOneField(
        FinancialRecord, on_delete=models.PROTECT, related_name="credit_note"
    )
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="credits"
    )

    class Meta(Record.Meta):
        default_permissions = ()


class CreditNoteLine(Record):
    credit = models.ForeignKey(
        CreditNote, on_delete=models.PROTECT, related_name="lines"
    )
    line = models.ForeignKey(InvoiceLine, on_delete=models.PROTECT)
    quantity = money()
    net = money()
    tax = money()
    total = money()

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0, net__gte=0, tax__gte=0, total__gte=0),
                name="ledger_credit_line_amount",
            )
        ]


class FinancialRevision(Record):
    record = models.ForeignKey(
        FinancialRecord, on_delete=models.PROTECT, related_name="revisions"
    )
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    fingerprint = models.CharField(max_length=64)

    class Meta(Record.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["record", "version"], name="ledger_revision_version"
            )
        ]


class ApprovalRequest(Record):
    revision = models.OneToOneField(
        FinancialRevision, on_delete=models.PROTECT, null=True, related_name="approval"
    )
    quotation_revision = models.OneToOneField(
        "procurement.ClientQuotationRevision",
        on_delete=models.PROTECT,
        null=True,
        related_name="ledger_approval",
    )
    snapshot = models.JSONField(default=dict)
    status = models.CharField(max_length=12, default="PENDING")
    task_batch = models.ForeignKey(
        "tasks.TaskBatch", on_delete=models.PROTECT, null=True
    )

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(revision__isnull=False, quotation_revision__isnull=True)
                | Q(revision__isnull=True, quotation_revision__isnull=False),
                name="ledger_approval_target",
            )
        ]


class ApprovalDecision(Record):
    request = models.OneToOneField(
        ApprovalRequest, on_delete=models.PROTECT, related_name="decision"
    )
    approved = models.BooleanField()
    reason = models.TextField()


class FinanceSubmission(Record):
    source = models.OneToOneField(
        "procurement.FinanceRequest",
        on_delete=models.PROTECT,
        related_name="ledger_submission",
    )
    snapshot = models.JSONField()
    record = models.ForeignKey(FinancialRecord, on_delete=models.PROTECT, null=True)


class RevenueRecognition(Record):
    line = models.ForeignKey(
        InvoiceLine, on_delete=models.PROTECT, related_name="recognitions"
    )
    quantity = money()
    amount = money()
    journal = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT)


class ManualLine(Record):
    record = models.ForeignKey(
        FinancialRecord, on_delete=models.PROTECT, related_name="manual_lines"
    )
    account = models.ForeignKey("ledger.AccountCurrency", on_delete=models.PROTECT)
    debit = money(default=0)
    credit = money(default=0)
    party = models.ForeignKey("crm.Party", on_delete=models.PROTECT, null=True)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(debit__gt=0, credit=0) | Q(credit__gt=0, debit=0),
                name="ledger_manual_line_side",
            )
        ]


class InvoiceIssue(Record):
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="issues"
    )
    recipient = models.ForeignKey("crm.Party", on_delete=models.PROTECT)
    method = models.CharField(max_length=40)
    occurred_at = models.DateTimeField()
    evidence = models.TextField()
