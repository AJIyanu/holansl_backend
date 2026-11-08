"""Cash book and its append-only, two-legged journal."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q, F

CURRENCIES = [
    ("NGN", "Nigerian naira"),
    ("USD", "US dollar"),
    ("GBP", "Pound sterling"),
]
PRIMARY_CASH_ID = "00000000-0000-4000-8000-000000000001"


class CashBookEntry(models.Model):
    class Kind(models.TextChoices):
        RECEIPT = "RECEIPT", "Money in (cash debit)"
        EXPENSE = "EXPENSE", "Expense (cash credit)"
        CAPITAL = "CAPITAL", "Capital introduced (cash debit)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        "LedgerAccount", default=PRIMARY_CASH_ID, on_delete=models.PROTECT
    )
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="NGN")
    request_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    date = models.DateField(db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    description = models.CharField(max_length=500)
    reference = models.CharField(max_length=100, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cash_entries_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cash_entries_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["date", "created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="cashbook_positive_amount"
            ),
            models.CheckConstraint(
                condition=Q(kind__in=["RECEIPT", "EXPENSE", "CAPITAL"]),
                name="cashbook_valid_kind",
            ),
        ]


class CashBookJournal(models.Model):
    """One amount on each side guarantees equal debit/credit totals by construction."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey(
        CashBookEntry,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="journal",
    )
    party_transaction = models.OneToOneField(
        "PartyLedgerTransaction",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="journal",
    )
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="NGN")
    date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    debit_account = models.CharField(max_length=20)
    credit_account = models.CharField(max_length=20)
    description = models.CharField(max_length=500)
    reference = models.CharField(max_length=100, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    reverses = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal"
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ("view",)
        ordering = ["date", "created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="cashjournal_positive_amount"
            ),
            models.CheckConstraint(
                condition=~Q(debit_account=F("credit_account")),
                name="cashjournal_distinct_accounts",
            ),
            models.CheckConstraint(
                condition=Q(
                    debit_account__in=[
                        "CASH",
                        "INCOME",
                        "EXPENSE",
                        "CAPITAL",
                        "RECEIVABLE",
                        "PAYABLE",
                        "DEFERRED_REVENUE",
                        "PURCHASE_CLEARING",
                        "ADJUSTMENT",
                        "VAT_PAYABLE",
                        "WHT_RECEIVABLE",
                    ],
                    credit_account__in=[
                        "CASH",
                        "INCOME",
                        "EXPENSE",
                        "CAPITAL",
                        "RECEIVABLE",
                        "PAYABLE",
                        "DEFERRED_REVENUE",
                        "PURCHASE_CLEARING",
                        "ADJUSTMENT",
                        "VAT_PAYABLE",
                        "WHT_RECEIVABLE",
                    ],
                ),
                name="cashjournal_valid_accounts",
            ),
        ]


class PartyLedgerSelection(models.Model):
    """A staff member's last party, independently remembered for each ledger."""

    class Kind(models.TextChoices):
        SALES = "sales", "Sales ledger"
        PURCHASE = "purchase", "Purchase ledger"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    party = models.ForeignKey(
        "crm.Party", null=True, blank=True, on_delete=models.SET_NULL
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        permissions = [
            ("view_salesledger", "Can view sales ledger"),
            ("view_purchaseledger", "Can view purchase ledger"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "kind"], name="ledger_unique_user_selection"
            ),
            models.CheckConstraint(
                condition=Q(kind__in=["sales", "purchase"]),
                name="ledger_valid_selection_kind",
            ),
        ]


class PartyLedgerTransaction(models.Model):
    class Type(models.TextChoices):
        INVOICE = "INVOICE", "Sales invoice"
        BILL = "BILL", "Supplier bill"
        PAYMENT = "PAYMENT", "Payment"
        CREDIT = "CREDIT", "Credit note"
        REFUND = "REFUND", "Refund"
        ADJUST_UP = "ADJUST_UP", "Increase balance"
        ADJUST_DOWN = "ADJUST_DOWN", "Decrease balance"
        REVERSAL = "REVERSAL", "Reversal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    party = models.ForeignKey("crm.Party", on_delete=models.PROTECT)
    ledger_kind = models.CharField(
        max_length=8, choices=PartyLedgerSelection.Kind.choices
    )
    transaction_type = models.CharField(max_length=12, choices=Type.choices)
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="NGN")
    effect = models.SmallIntegerField(default=1)
    related_transaction = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="adjustments",
    )
    reverses = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal"
    )
    reason = models.CharField(max_length=500, blank=True)
    date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    description = models.CharField(max_length=500)
    reference = models.CharField(max_length=100, blank=True)
    allocation_request = models.JSONField(default=list, editable=False)
    cash_entry = models.OneToOneField(
        CashBookEntry,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="party_transaction",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        permissions = [
            ("add_salestransaction", "Can post sales ledger transactions"),
            ("add_purchasetransaction", "Can post purchase ledger transactions"),
            ("correct_salesledger", "Can correct sales ledger and allocations"),
            ("correct_purchaseledger", "Can correct purchase ledger and allocations"),
            ("approve_procurement", "Can approve procurement financial documents"),
            ("recognize_salesledger", "Can record sales delivery acceptance"),
        ]
        ordering = ["date", "created_at", "id"]
        indexes = [
            models.Index(
                fields=["party", "ledger_kind", "currency", "date"],
                name="ledger_party_period_idx",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="party_transaction_positive"
            ),
            models.CheckConstraint(
                condition=Q(currency__in=["NGN", "USD", "GBP"]),
                name="party_transaction_currency",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        ledger_kind="sales",
                        transaction_type__in=[
                            "INVOICE",
                            "PAYMENT",
                            "CREDIT",
                            "REFUND",
                            "ADJUST_UP",
                            "ADJUST_DOWN",
                            "REVERSAL",
                        ],
                    )
                    | Q(
                        ledger_kind="purchase",
                        transaction_type__in=[
                            "BILL",
                            "PAYMENT",
                            "CREDIT",
                            "REFUND",
                            "ADJUST_UP",
                            "ADJUST_DOWN",
                            "REVERSAL",
                        ],
                    )
                ),
                name="party_transaction_valid_type",
            ),
        ]


class PartyPaymentAllocation(models.Model):
    """Shared payment-to-document links; never separate tables for individual parties."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    effective_date = models.DateField(null=True, blank=True)
    payment = models.ForeignKey(
        PartyLedgerTransaction, on_delete=models.PROTECT, related_name="allocations"
    )
    document = models.ForeignKey(
        PartyLedgerTransaction, on_delete=models.PROTECT, related_name="settlements"
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="ledger_positive_allocation"
            ),
            models.CheckConstraint(
                condition=~Q(payment=F("document")),
                name="ledger_distinct_allocation_source",
            ),
        ]


class LedgerAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    kind = models.CharField(
        max_length=4, choices=[("CASH", "Cash holder"), ("BANK", "Bank account")]
    )
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["currency", "kind", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "currency", "kind"], name="ledger_account_identity"
            ),
            models.CheckConstraint(
                condition=Q(currency__in=["NGN", "USD", "GBP"]),
                name="ledger_account_currency",
            ),
        ]


class LedgerOperation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    party = models.ForeignKey("crm.Party", on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    payload = models.JSONField()
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()


class AllocationRelease(models.Model):
    allocation = models.OneToOneField(
        PartyPaymentAllocation, on_delete=models.PROTECT, related_name="release"
    )
    date = models.DateField()
    reason = models.CharField(max_length=500)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()


class Reconciliation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    party = models.ForeignKey("crm.Party", on_delete=models.PROTECT)
    ledger_kind = models.CharField(
        max_length=8, choices=PartyLedgerSelection.Kind.choices
    )
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    date_from = models.DateField()
    date_to = models.DateField()
    snapshot = models.JSONField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ("view", "add")
        permissions = [
            ("confirm_reconciliation", "Can confirm or dispute reconciliations")
        ]
        ordering = ["-created_at", "id"]


class ReconciliationReview(models.Model):
    reconciliation = models.ForeignKey(
        Reconciliation, on_delete=models.PROTECT, related_name="reviews"
    )
    request_key = models.UUIDField(unique=True)
    status = models.CharField(
        max_length=8, choices=[("AGREED", "Agreed"), ("DISPUTED", "Disputed")]
    )
    notes = models.CharField(max_length=1000)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["created_at", "id"]


class InvoiceFulfilment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(
        PartyLedgerTransaction, on_delete=models.PROTECT, related_name="fulfilments"
    )
    request_key = models.UUIDField(unique=True)
    status = models.CharField(
        max_length=12,
        choices=[
            ("DISPATCHED", "Dispatched"),
            ("REJECTED", "Rejected"),
            ("ACCEPTED", "Accepted"),
        ],
    )
    date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    evidence = models.CharField(max_length=1000)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["created_at", "id"]


class RevenueRecognition(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(
        PartyLedgerTransaction, on_delete=models.PROTECT, related_name="recognitions"
    )
    source_key = models.CharField(max_length=180, unique=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    is_reversal = models.BooleanField(default=False)
    date = models.DateField()
    evidence = models.CharField(max_length=1000)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    journal = models.OneToOneField(CashBookJournal, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()


class ProcurementLedgerLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction = models.OneToOneField(
        PartyLedgerTransaction,
        on_delete=models.PROTECT,
        related_name="procurement_link",
    )
    finance_request = models.OneToOneField(
        "procurement.FinanceRequest", null=True, blank=True, on_delete=models.PROTECT
    )
    supplier_purchase = models.OneToOneField(
        "procurement.SupplierPurchase", null=True, blank=True, on_delete=models.PROTECT
    )
    request = models.ForeignKey(
        "procurement.ProcurementRequest", on_delete=models.PROTECT
    )

    class Meta:
        default_permissions = ()


class ProcurementInvoiceLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    link = models.ForeignKey(
        ProcurementLedgerLink, on_delete=models.PROTECT, related_name="lines"
    )
    item = models.ForeignKey("procurement.RequestItem", on_delete=models.PROTECT)
    po_line = models.ForeignKey(
        "procurement.ClientPurchaseOrderLine",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        default_permissions = ()


class ProcurementApproval(models.Model):
    revision = models.OneToOneField(
        "procurement.ClientQuotationRevision", on_delete=models.PROTECT
    )
    approved = models.BooleanField()
    reason = models.CharField(max_length=500)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()


class FinanceSubmission(models.Model):
    request = models.OneToOneField(
        "procurement.FinanceRequest", on_delete=models.PROTECT
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()


class FinanceApproval(models.Model):
    """Review of a submitted procurement request; does not itself move funds."""

    request = models.OneToOneField(
        "procurement.FinanceRequest", on_delete=models.PROTECT
    )
    approved = models.BooleanField()
    reason = models.CharField(max_length=500)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()


class InvoiceClientCode(models.Model):
    party = models.OneToOneField(
        "crm.Party", on_delete=models.PROTECT, primary_key=True
    )
    code = models.CharField(max_length=3, unique=True)

    class Meta:
        default_permissions = ()


class InvoiceSequence(models.Model):
    date = models.DateField(primary_key=True)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        default_permissions = ()


class SalesInvoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    party = models.ForeignKey("crm.Party", on_delete=models.PROTECT)
    date = models.DateField()
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="NGN")
    reference = models.CharField(max_length=40, unique=True, null=True, blank=True)
    billed_to = models.CharField(max_length=255)
    billing_address = models.TextField(blank=True, max_length=1000)
    recipient_email = models.EmailField(blank=True)
    notes = models.TextField(blank=True, max_length=2000)
    subtotal = models.DecimalField(max_digits=18, decimal_places=2)
    version = models.PositiveIntegerField(default=1)
    confirmed_revision = models.ForeignKey(
        "InvoiceRevision",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    delivered_at = models.DateField(null=True, blank=True)
    posted_transaction = models.OneToOneField(
        PartyLedgerTransaction,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="invoice_document",
    )
    finance_request = models.OneToOneField(
        "procurement.FinanceRequest", null=True, blank=True, on_delete=models.PROTECT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        default_permissions = ("view", "add", "change")
        permissions = [
            ("confirm_salesinvoice", "Can confirm sales invoices"),
            ("deliver_salesinvoice", "Can mark sales invoices delivered"),
            ("print_salesinvoice", "Can print sales invoices"),
            ("email_salesinvoice", "Can email sales invoices"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(subtotal__gt=0), name="salesinvoice_positive_subtotal"
            )
        ]


class SalesInvoiceItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(
        SalesInvoice, on_delete=models.CASCADE, related_name="items"
    )
    position = models.PositiveIntegerField()
    description = models.CharField(max_length=1000)
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    unit = models.CharField(max_length=40)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    source_item = models.ForeignKey(
        "procurement.RequestItem", null=True, blank=True, on_delete=models.PROTECT
    )
    po_line = models.ForeignKey(
        "procurement.ClientPurchaseOrderLine",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )

    class Meta:
        default_permissions = ()
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "position"], name="invoice_item_position"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0, unit_price__gte=0, amount__gte=0),
                name="invoice_item_values",
            ),
        ]


class InvoiceRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(
        SalesInvoice, on_delete=models.PROTECT, related_name="revisions"
    )
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "version"], name="invoice_revision_version"
            )
        ]


class InvoiceOperation(models.Model):
    request_key = models.UUIDField(primary_key=True)
    invoice = models.ForeignKey(
        SalesInvoice, on_delete=models.PROTECT, related_name="operations"
    )
    action = models.CharField(max_length=15)
    payload = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()


class InvoiceEmail(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    revision = models.ForeignKey(
        InvoiceRevision, on_delete=models.PROTECT, related_name="emails"
    )
    destination = models.EmailField()
    subject = models.CharField(max_length=250)
    message = models.TextField(max_length=10000)
    status = models.CharField(
        max_length=12,
        default="SENDING",
        choices=[
            ("SENDING", "Sending"),
            ("SENT", "Accepted by email provider"),
            ("UNKNOWN", "Delivery not confirmed"),
        ],
    )
    provider_message_id = models.CharField(max_length=255, blank=True)
    pdf_sha256 = models.CharField(max_length=64)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        ordering = ["-created_at", "-id"]


class SalesReceipt(models.Model):
    """Confirmed acknowledgement and immutable snapshot of one sales payment."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    party = models.ForeignKey("crm.Party", on_delete=models.PROTECT)
    invoice = models.ForeignKey(
        SalesInvoice, null=True, blank=True, on_delete=models.PROTECT
    )
    transaction = models.OneToOneField(
        PartyLedgerTransaction,
        on_delete=models.PROTECT,
        related_name="receipt_document",
    )
    reference = models.CharField(max_length=40, unique=True)
    date = models.DateField(db_index=True)
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    subtotal = models.DecimalField(max_digits=18, decimal_places=2)
    vat = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    withholding = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    received = models.DecimalField(max_digits=18, decimal_places=2)
    snapshot = models.JSONField()
    submission = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at", "-id"]
        default_permissions = ("view", "add")
        permissions = [
            ("print_salesreceipt", "Can print sales receipts"),
            ("email_salesreceipt", "Can email sales receipts"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    subtotal__gt=0, vat__gte=0, withholding__gte=0, received__gt=0
                ),
                name="receipt_positive_amounts",
            ),
            models.CheckConstraint(
                condition=Q(received=F("subtotal") + F("vat") - F("withholding")),
                name="receipt_balanced_amounts",
            ),
        ]


class ReceiptEmail(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    receipt = models.ForeignKey(
        SalesReceipt, on_delete=models.PROTECT, related_name="emails"
    )
    destination = models.EmailField()
    subject = models.CharField(max_length=250)
    message = models.TextField(max_length=10000)
    status = models.CharField(
        max_length=10,
        default="SENDING",
        choices=[
            ("SENDING", "Sending"),
            ("SENT", "Provider accepted"),
            ("UNKNOWN", "Unknown outcome"),
        ],
    )
    provider_message_id = models.CharField(max_length=255, blank=True)
    pdf_sha256 = models.CharField(max_length=64)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        ordering = ["-created_at", "-id"]
