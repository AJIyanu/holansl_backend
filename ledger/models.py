"""Cash book and its append-only, two-legged journal."""

import uuid
from decimal import Decimal

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
    staff_entry = models.ForeignKey(
        "StaffLedgerEntry",
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
                        "INPUT_TAX",
                        "WHT_PAYABLE",
                        "STAFF_RECEIVABLE",
                        "STAFF_PAYABLE",
                        "INPUT_PENDING",
                        "CIT_PAYABLE",
                        "TAX_EXPENSE",
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
                        "INPUT_TAX",
                        "WHT_PAYABLE",
                        "STAFF_RECEIVABLE",
                        "STAFF_PAYABLE",
                        "INPUT_PENDING",
                        "CIT_PAYABLE",
                        "TAX_EXPENSE",
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
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("7.5")
    )
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


class PurchaseBill(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    party = models.ForeignKey("crm.Party", on_delete=models.PROTECT)
    reference = models.CharField(max_length=100)
    date = models.DateField(db_index=True)
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    subtotal = models.DecimalField(max_digits=18, decimal_places=2)
    snapshot = models.JSONField()
    version = models.PositiveIntegerField(default=1)
    delivered_at = models.DateField(null=True, blank=True)
    transaction = models.OneToOneField(
        PartyLedgerTransaction,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="bill_document",
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ("view", "add", "change")
        permissions = [("deliver_purchasebill", "Can mark supplier bills delivered")]
        ordering = ["-date", "-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(subtotal__gt=0), name="purchase_bill_positive"
            )
        ]


class PurchaseBillEvent(models.Model):
    request_key = models.UUIDField(primary_key=True)
    bill = models.ForeignKey(
        PurchaseBill, on_delete=models.PROTECT, related_name="events"
    )
    action = models.CharField(max_length=10)
    payload = models.JSONField()
    snapshot = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["-created_at", "-request_key"]


class PurchasePayment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    party = models.ForeignKey("crm.Party", on_delete=models.PROTECT)
    bill = models.ForeignKey(
        PurchaseBill, null=True, blank=True, on_delete=models.PROTECT
    )
    transaction = models.OneToOneField(
        PartyLedgerTransaction,
        on_delete=models.PROTECT,
        related_name="purchase_payment",
    )
    date = models.DateField(db_index=True)
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    subtotal = models.DecimalField(max_digits=18, decimal_places=2)
    vat = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    withholding = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    paid = models.DecimalField(max_digits=18, decimal_places=2)
    snapshot = models.JSONField()
    submission = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ("view", "add", "change")
        permissions = [
            ("download_purchasepayment", "Can download supplier receipt evidence")
        ]
        ordering = ["-date", "-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(subtotal__gt=0, vat__gte=0, withholding__gte=0, paid__gt=0),
                name="purchase_payment_positive",
            ),
            models.CheckConstraint(
                condition=Q(paid=F("subtotal") + F("vat") - F("withholding")),
                name="purchase_payment_balanced",
            ),
        ]


class PurchaseReceiptEvidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    payment = models.ForeignKey(
        PurchasePayment, on_delete=models.PROTECT, related_name="evidence"
    )
    version = models.PositiveIntegerField()
    receipt_number = models.CharField(max_length=100, blank=True)
    filename = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True)
    storage_provider = models.CharField(max_length=30, blank=True)
    external_file_id = models.TextField(blank=True)
    storage_path = models.TextField(blank=True)
    payload = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["payment", "version"], name="purchase_evidence_version"
            )
        ]


class StaffLedgerSelection(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_ledger_selection",
    )
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ledger_selected_by",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()


class StaffLedgerEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="staff_ledger_entries",
    )
    kind = models.CharField(
        max_length=10,
        choices=[
            ("PAY", "Pay staff"),
            ("RECEIVE", "Receive from staff"),
            ("RETIRE", "Retire advance"),
        ],
    )
    category = models.CharField(max_length=30)
    date = models.DateField(db_index=True)
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    description = models.CharField(max_length=1000)
    reference = models.CharField(max_length=100, blank=True)
    related_entry = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="settlements",
    )
    cash_entry = models.OneToOneField(
        CashBookEntry,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="staff_transaction",
    )
    receivable_delta = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    payable_delta = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    payload = models.JSONField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="staff_entries_recorded",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        permissions = [
            ("view_staffledger", "Can view staff ledger"),
            ("pay_staffledger", "Can pay staff"),
            ("receive_staffledger", "Can receive money from staff"),
            ("retire_staffledger", "Can submit itemized advance retirements"),
            ("approve_staffledger", "Can approve or reject advance retirements"),
            ("download_staffledger", "Can download staff ledger supporting documents"),
            ("print_staffledger", "Can generate staff reconciliation and statement"),
        ]
        ordering = ["date", "created_at", "id"]
        indexes = [
            models.Index(
                fields=["staff", "currency", "date"], name="staff_ledger_period_idx"
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="staff_ledger_positive"
            ),
            models.CheckConstraint(
                condition=Q(kind="RETIRE", cash_entry__isnull=True)
                | Q(kind__in=["PAY", "RECEIVE"], cash_entry__isnull=False),
                name="staff_ledger_cash_link",
            ),
        ]


class StaffRetirement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    advance = models.ForeignKey(
        StaffLedgerEntry, on_delete=models.PROTECT, related_name="retirements"
    )
    date = models.DateField()
    description = models.CharField(max_length=1000)
    total = models.DecimalField(max_digits=18, decimal_places=2)
    items = models.JSONField()
    payload = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(total__gt=0), name="staff_retirement_positive"
            )
        ]


class StaffRetirementDecision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    retirement = models.OneToOneField(
        StaffRetirement, on_delete=models.PROTECT, related_name="decision"
    )
    advance = models.ForeignKey(
        StaffLedgerEntry, on_delete=models.PROTECT, related_name="retirement_decisions"
    )
    approved = models.BooleanField()
    date = models.DateField()
    reason = models.CharField(max_length=1000)
    entry = models.OneToOneField(
        StaffLedgerEntry,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="retirement_decision",
    )
    payload = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["advance"],
                condition=Q(approved=True),
                name="staff_single_retirement_approval",
            ),
            models.CheckConstraint(
                condition=Q(approved=True, entry__isnull=False)
                | Q(approved=False, entry__isnull=True),
                name="staff_decision_posting",
            ),
        ]


class StaffLedgerDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey(
        StaffLedgerEntry,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="documents",
    )
    retirement = models.ForeignKey(
        StaffRetirement,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="documents",
    )
    item_position = models.PositiveIntegerField(null=True, blank=True)
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)
    storage_provider = models.CharField(max_length=30)
    external_file_id = models.TextField()
    storage_path = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    entry__isnull=False,
                    retirement__isnull=True,
                    item_position__isnull=True,
                )
                | Q(
                    entry__isnull=True,
                    retirement__isnull=False,
                    item_position__isnull=False,
                    item_position__gt=0,
                ),
                name="staff_document_owner",
            )
        ]


class StaffStatement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ledger_statements",
    )
    date_from = models.DateField()
    date_to = models.DateField()
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    snapshot = models.JSONField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="staff_statements_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["-created_at", "-id"]


class TaxEntry(models.Model):
    """Immutable tax subledger movements; existing journal amounts are never reposted."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_key = models.CharField(max_length=160, unique=True)
    document_key = models.CharField(max_length=100, blank=True, db_index=True)
    kind = models.CharField(max_length=30)
    side = models.CharField(max_length=10, blank=True)
    date = models.DateField(db_index=True)
    period = models.CharField(max_length=7, db_index=True)
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    party = models.ForeignKey(
        "crm.Party", null=True, blank=True, on_delete=models.PROTECT
    )
    transaction = models.ForeignKey(
        PartyLedgerTransaction,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tax_entries",
    )
    cash_entry = models.ForeignKey(
        CashBookEntry,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tax_entries",
    )
    reference = models.CharField(max_length=100, blank=True)
    description = models.CharField(max_length=1000)
    output_vat = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    input_vat = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    input_pending = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    input_nonrecoverable = models.DecimalField(
        max_digits=18, decimal_places=2, default=0
    )
    sales_vat_cash = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    purchase_vat_cash = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    wht_asset = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    wht_liability = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    input_used = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    vat_remitted = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    wht_remitted = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cit_assessed = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cit_paid = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    wht_used = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    party_vat = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    delivery_vat = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    metadata = models.JSONField(default=dict)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["date", "created_at", "id"]
        indexes = [
            models.Index(fields=["currency", "date"], name="tax_currency_date_idx")
        ]
        permissions = [
            (f"{action}_taxledger", label)
            for action, label in [
                ("view", "Can view tax ledger"),
                ("remit", "Can prepare tax remittances"),
                ("approve", "Can approve tax operations"),
                ("adjust", "Can prepare tax adjustments and CIT assessments"),
                (
                    "configure",
                    "Can configure tax policies and input VAT classification",
                ),
                ("apply", "Can apply WHT credits to CIT"),
                ("export", "Can export tax ledger"),
            ]
        ]


class TaxRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    payload = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["-created_at", "-id"]


class TaxDecision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    request = models.OneToOneField(
        TaxRequest, on_delete=models.PROTECT, related_name="decision"
    )
    approved = models.BooleanField()
    reason = models.CharField(max_length=1000)
    payload = models.JSONField()
    entry = models.OneToOneField(
        TaxEntry, null=True, blank=True, on_delete=models.PROTECT
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=Q(approved=True, entry__isnull=False)
                | Q(approved=False, entry__isnull=True),
                name="tax_decision_posting",
            )
        ]


class TaxDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.OneToOneField(
        TaxRequest, on_delete=models.PROTECT, related_name="document"
    )
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)
    storage_provider = models.CharField(max_length=30)
    external_file_id = models.TextField()
    storage_path = models.TextField(blank=True)

    class Meta:
        default_permissions = ()


class TaxPolicy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    party = models.ForeignKey(
        "crm.Party", null=True, blank=True, on_delete=models.PROTECT
    )
    kind = models.CharField(max_length=10, choices=[("VAT", "VAT"), ("WHT", "WHT")])
    rate = models.DecimalField(max_digits=5, decimal_places=2)
    effective_date = models.DateField()
    enabled = models.BooleanField(default=True)
    reason = models.CharField(max_length=1000)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["-effective_date", "-created_at", "-id"]
