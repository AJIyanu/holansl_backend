"""Procurement records. Official accounting remains owned by ledger."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


def reference():
    return uuid.uuid4().hex.upper()


class RetainedQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("Procurement history cannot be deleted.")


class Record(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_by_name = models.CharField(max_length=255)
    objects = RetainedQuerySet.as_manager()

    class Meta:
        abstract = True
        ordering = ["-created_at", "id"]

    def delete(self, *args, **kwargs):
        raise ValidationError("Procurement history cannot be deleted.")


class ProcurementRequest(Record):
    number = models.CharField(max_length=40, unique=True, default=reference)
    client = models.ForeignKey(
        "crm.Party", on_delete=models.PROTECT, related_name="procurement_requests"
    )
    client_name = models.CharField(max_length=255)
    contact = models.ForeignKey(
        "crm.Party", on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="procurement_requests",
    )
    department = models.ForeignKey(
        "accounts.Department", on_delete=models.PROTECT, null=True, blank=True
    )
    received_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=0)

    class Meta(Record.Meta):
        permissions = [
            ("view_all_procurementrequest", "View all procurement requests"),
            (
                "view_department_procurementrequest",
                "View department procurement requests",
            ),
            ("operate_procurementrequest", "Perform procurement business actions"),
            ("resolve_procurementrequest", "Resolve rejected procurement items"),
            ("download_procurementdocument", "Download procurement documents"),
        ]


class RequestItem(Record):
    request = models.ForeignKey(
        ProcurementRequest, on_delete=models.PROTECT, related_name="items"
    )
    description = models.TextField()
    specification = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit = models.CharField(max_length=40)
    required_date = models.DateField(null=True, blank=True)
    previous_item = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reorders"
    )

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="proc_item_qty_positive"
            )
        ]


class ProcurementDocument(Record):
    class Category(models.TextChoices):
        QUOTATION = "QUOTATION", "Quotation"
        PURCHASE_ORDER = "PURCHASE_ORDER", "Purchase order"
        WAYBILL = "WAYBILL", "Waybill"
        SIGNED_WAYBILL = "SIGNED_WAYBILL", "Signed waybill"
        RECEIPT = "RECEIPT", "Receipt evidence (unverified)"
        OTHER = "OTHER", "Other evidence"

    request = models.ForeignKey(
        ProcurementRequest, on_delete=models.PROTECT, related_name="documents"
    )
    category = models.CharField(max_length=30, choices=Category.choices)
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=150)
    size_bytes = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)
    storage_provider = models.CharField(max_length=30)
    external_file_id = models.TextField()
    storage_path = models.TextField(blank=True)


class SupplierQuotation(Record):
    request = models.ForeignKey(
        ProcurementRequest, on_delete=models.PROTECT, related_name="supplier_quotations"
    )
    supplier = models.ForeignKey(
        "crm.Party", on_delete=models.PROTECT, related_name="+"
    )
    supplier_name = models.CharField(max_length=255)
    reference = models.CharField(max_length=150, blank=True)
    currency = models.CharField(max_length=3)
    expires_at = models.DateTimeField(null=True, blank=True)
    previous = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="next_revision",
    )
    reason = models.TextField(blank=True)
    document = models.ForeignKey(
        ProcurementDocument, on_delete=models.PROTECT, null=True, blank=True
    )


class SupplierQuotationLine(Record):
    quotation = models.ForeignKey(
        SupplierQuotation, on_delete=models.PROTECT, related_name="lines"
    )
    item = models.ForeignKey(
        RequestItem, on_delete=models.PROTECT, related_name="supplier_offers"
    )
    attempt = models.ForeignKey(
        "FulfilmentAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="supplier_offers",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4)
    charges = models.DecimalField(max_digits=18, decimal_places=4, default=0)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0, unit_price__gte=0, charges__gte=0),
                name="proc_offer_values_valid",
            )
        ]


class ClientQuotation(Record):
    request = models.ForeignKey(
        ProcurementRequest, on_delete=models.PROTECT, related_name="quotations"
    )
    number = models.CharField(max_length=40, unique=True, default=reference)


class ClientQuotationRevision(Record):
    quotation = models.ForeignKey(
        ClientQuotation, on_delete=models.PROTECT, related_name="revisions"
    )
    number = models.PositiveIntegerField()
    currency = models.CharField(max_length=3)
    expires_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)
    document = models.ForeignKey(
        ProcurementDocument, on_delete=models.PROTECT, null=True, blank=True
    )
    expired_recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta(Record.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["quotation", "number"], name="proc_revision_number_unique"
            )
        ]


class ClientQuotationLine(Record):
    revision = models.ForeignKey(
        ClientQuotationRevision, on_delete=models.PROTECT, related_name="lines"
    )
    item = models.ForeignKey(
        RequestItem, on_delete=models.PROTECT, related_name="quotation_lines"
    )
    supplier_offer = models.ForeignKey(SupplierQuotationLine, on_delete=models.PROTECT)
    description = models.TextField()
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4)
    estimated_unit_cost = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    discount_type = models.CharField(
        max_length=10,
        choices=[("AMOUNT", "Amount"), ("PERCENT", "Percentage")],
        default="AMOUNT",
    )
    discount_value = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    discount = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    tax_rate = models.DecimalField(
        max_digits=7, decimal_places=4, null=True, default=7.5
    )
    tax = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    logistics = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    additional_charges = models.DecimalField(max_digits=18, decimal_places=4, default=0)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    quantity__gt=0,
                    unit_price__gte=0,
                    discount_value__gte=0,
                    discount__gte=0,
                )
                & (Q(discount_type="AMOUNT") | Q(discount_value__lte=100))
                & Q(
                    tax_rate__gte=0,
                    tax_rate__lte=100,
                    tax__gte=0,
                    logistics__gte=0,
                    additional_charges__gte=0,
                ),
                name="proc_quote_values_valid",
            )
        ]


class QuotationIssue(Record):
    revision = models.ForeignKey(
        ClientQuotationRevision, on_delete=models.PROTECT, related_name="issues"
    )
    recipient = models.ForeignKey("crm.Party", on_delete=models.PROTECT)
    recipient_name = models.CharField(max_length=255)
    method = models.CharField(max_length=40)
    occurred_at = models.DateTimeField(default=timezone.now)
    evidence = models.TextField()


class ClientPurchaseOrder(Record):
    request = models.ForeignKey(
        ProcurementRequest, on_delete=models.PROTECT, related_name="purchase_orders"
    )
    reference = models.CharField(max_length=150)
    verbal = models.BooleanField(default=False)
    evidence = models.TextField(blank=True)
    document = models.ForeignKey(
        ProcurementDocument, on_delete=models.PROTECT, null=True, blank=True
    )
    received_at = models.DateTimeField(default=timezone.now)


class ClientPurchaseOrderLine(Record):
    order = models.ForeignKey(
        ClientPurchaseOrder, on_delete=models.PROTECT, related_name="lines"
    )
    quote_line = models.ForeignKey(
        ClientQuotationLine,
        on_delete=models.PROTECT,
        related_name="po_lines",
        null=True,
        blank=True,
    )
    item = models.ForeignKey(
        RequestItem, on_delete=models.PROTECT, null=True, related_name="po_lines"
    )
    item_description = models.TextField(blank=True)
    origin = models.CharField(
        max_length=12,
        default="QUOTATION",
        choices=[("QUOTATION", "Quotation"), ("INVOICE", "Direct invoice")],
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(origin="QUOTATION", quote_line__isnull=False)
                | Q(origin="INVOICE", quote_line__isnull=True, item__isnull=False),
                name="proc_po_line_origin",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0, unit_price__gte=0),
                name="proc_po_values_valid",
            ),
        ]


class FulfilmentAttempt(Record):
    item = models.ForeignKey(
        RequestItem, on_delete=models.PROTECT, related_name="attempts"
    )
    po_line = models.ForeignKey(
        ClientPurchaseOrderLine, on_delete=models.PROTECT, related_name="attempts"
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    replaces = models.ForeignKey(
        "DeliveryOutcomeLine",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="replacement_attempts",
    )
    strategy = models.CharField(
        max_length=30,
        choices=[
            ("ORIGINAL", "Original purchase"),
            ("SAME_SUPPLIER", "Supplier replacement"),
            ("DIFFERENT_SUPPLIER", "Different supplier"),
        ],
    )
    reason = models.TextField(blank=True)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="proc_attempt_qty_positive"
            )
        ]


class SupplierPurchase(Record):
    request = models.ForeignKey(
        ProcurementRequest, on_delete=models.PROTECT, related_name="supplier_purchases"
    )
    supplier = models.ForeignKey(
        "crm.Party", on_delete=models.PROTECT, related_name="+"
    )
    supplier_name = models.CharField(max_length=255)
    currency = models.CharField(max_length=3)
    reference = models.CharField(max_length=150, blank=True)
    document = models.ForeignKey(
        ProcurementDocument, on_delete=models.PROTECT, null=True, blank=True
    )


class SupplierPurchaseLine(Record):
    purchase = models.ForeignKey(
        SupplierPurchase, on_delete=models.PROTECT, related_name="lines"
    )
    attempt = models.ForeignKey(
        FulfilmentAttempt, on_delete=models.PROTECT, related_name="purchase_lines"
    )
    supplier_offer = models.ForeignKey(SupplierQuotationLine, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0, unit_price__gte=0),
                name="proc_purchase_values_valid",
            )
        ]


class GoodsReceipt(Record):
    purchase = models.ForeignKey(
        SupplierPurchase, on_delete=models.PROTECT, related_name="receipts"
    )
    received_at = models.DateTimeField(default=timezone.now)
    document = models.ForeignKey(
        ProcurementDocument, on_delete=models.PROTECT, null=True, blank=True
    )


class GoodsReceiptLine(Record):
    receipt = models.ForeignKey(
        GoodsReceipt, on_delete=models.PROTECT, related_name="lines"
    )
    purchase_line = models.ForeignKey(
        SupplierPurchaseLine, on_delete=models.PROTECT, related_name="receipts"
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="proc_received_qty_positive"
            )
        ]


class Delivery(Record):
    request = models.ForeignKey(
        ProcurementRequest, on_delete=models.PROTECT, related_name="deliveries"
    )
    number = models.CharField(max_length=40, unique=True, default=reference)
    destination = models.TextField()
    recipient = models.ForeignKey(
        "crm.Party", on_delete=models.PROTECT, related_name="+"
    )
    recipient_name = models.CharField(max_length=255)
    logistics_details = models.TextField(blank=True)
    waybill = models.ForeignKey(
        ProcurementDocument,
        on_delete=models.PROTECT,
        related_name="waybills",
        null=True,
        blank=True,
    )
    dispatched_at = models.DateTimeField(null=True, blank=True)


class DeliveryLine(Record):
    delivery = models.ForeignKey(
        Delivery, on_delete=models.PROTECT, related_name="lines"
    )
    receipt_line = models.ForeignKey(
        GoodsReceiptLine, on_delete=models.PROTECT, related_name="deliveries"
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="proc_delivery_qty_positive"
            )
        ]


class DeliveryOutcome(Record):
    delivery = models.ForeignKey(
        Delivery, on_delete=models.PROTECT, related_name="outcomes"
    )
    receiver = models.ForeignKey(
        "crm.Party", on_delete=models.PROTECT, related_name="+"
    )
    receiver_name = models.CharField(max_length=255)
    occurred_at = models.DateTimeField(default=timezone.now)
    signed_waybill = models.ForeignKey(
        ProcurementDocument, on_delete=models.PROTECT, null=True, blank=True
    )


class DeliveryOutcomeLine(Record):
    outcome = models.ForeignKey(
        DeliveryOutcome, on_delete=models.PROTECT, related_name="lines"
    )
    delivery_line = models.ForeignKey(
        DeliveryLine, on_delete=models.PROTECT, related_name="outcomes"
    )
    accepted = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    rejected = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    reason = models.TextField(blank=True)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(accepted__gte=0, rejected__gte=0)
                & (Q(accepted__gt=0) | Q(rejected__gt=0)),
                name="proc_outcome_qty_valid",
            ),
            models.CheckConstraint(
                condition=Q(rejected=0) | ~Q(reason=""),
                name="proc_rejection_reason_required",
            ),
        ]


class RejectionAction(Record):
    class Kind(models.TextChoices):
        RETURNED = "RETURNED_TO_SUPPLIER", "Item returned to supplier"
        ACCEPTED = "SUPPLIER_ACCEPTED_RETURN", "Supplier accepted return"
        REFUSED = "SUPPLIER_REFUSED_RETURN", "Supplier refused return"
        REPLACE = "SAME_SUPPLIER_REPLACEMENT", "Supplier will resupply"
        SOURCE = "DIFFERENT_SUPPLIER_CHOSEN", "Source from another supplier"
        REFUND = "REFUND_REPORTED", "Refund reported; ledger verification pending"
        CLOSE = "CLOSED_REJECTED", "Story terminated at rejection"

    rejection = models.ForeignKey(
        DeliveryOutcomeLine, on_delete=models.PROTECT, related_name="resolution_actions"
    )
    kind = models.CharField(max_length=40, choices=Kind.choices)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    reason = models.TextField()
    document = models.ForeignKey(
        ProcurementDocument, on_delete=models.PROTECT, null=True, blank=True
    )
    replacement_attempt = models.OneToOneField(
        FulfilmentAttempt,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="resolution_action",
    )
    refund_amount = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    currency = models.CharField(max_length=3, blank=True)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0) & ~Q(reason=""),
                name="proc_resolution_valid",
            )
        ]


class ProcurementCost(Record):
    item = models.ForeignKey(
        RequestItem, on_delete=models.PROTECT, related_name="costs"
    )
    kind = models.CharField(
        max_length=30,
        choices=[
            ("SUPPLIER", "Supplier"),
            ("LOGISTICS", "Logistics"),
            ("OTHER", "Other"),
        ],
    )
    amount = models.DecimalField(max_digits=18, decimal_places=4)
    currency = models.CharField(max_length=3)
    description = models.TextField()
    document = models.ForeignKey(ProcurementDocument, on_delete=models.PROTECT)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0), name="proc_cost_nonnegative"
            )
        ]


class FinanceRequest(Record):
    request = models.ForeignKey(
        ProcurementRequest, on_delete=models.PROTECT, related_name="finance_requests"
    )
    kind = models.CharField(
        max_length=30,
        choices=[
            ("INVOICE", "Invoice"),
            ("EXPENSE", "Expense"),
            ("ADVANCE", "Advance"),
            ("ADVANCE_BREAKDOWN", "Advance breakdown"),
            ("SUPPLIER_REFUND", "Supplier refund"),
        ],
    )
    notes = models.TextField(blank=True)
    currency = models.CharField(max_length=3)


class FinanceRequestLine(Record):
    request = models.ForeignKey(
        FinanceRequest, on_delete=models.PROTECT, related_name="lines"
    )
    item = models.ForeignKey(RequestItem, on_delete=models.PROTECT)
    po_line = models.ForeignKey(
        ClientPurchaseOrderLine, on_delete=models.PROTECT, null=True, blank=True
    )
    delivery_line = models.ForeignKey(
        DeliveryLine, on_delete=models.PROTECT, null=True, blank=True
    )
    amount = models.DecimalField(max_digits=18, decimal_places=4)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    document = models.ForeignKey(
        ProcurementDocument, on_delete=models.PROTECT, null=True, blank=True
    )

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0, quantity__gt=0),
                name="proc_finance_values_valid",
            )
        ]


class ProcurementTaskLink(Record):
    item = models.ForeignKey(
        RequestItem, on_delete=models.PROTECT, related_name="task_links"
    )
    task = models.ForeignKey("tasks.Task", on_delete=models.PROTECT)

    class Meta(Record.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["item", "task"], name="proc_task_link_unique"
            )
        ]


class ItemTrackingEvent(Record):
    item = models.ForeignKey(
        RequestItem, on_delete=models.PROTECT, related_name="events"
    )
    kind = models.CharField(max_length=50)
    occurred_at = models.DateTimeField(default=timezone.now)
    quantity = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    reason = models.TextField(blank=True)
    attempt = models.ForeignKey(
        FulfilmentAttempt, on_delete=models.PROTECT, null=True, related_name="events"
    )
    supplier_offer = models.ForeignKey(
        SupplierQuotationLine, on_delete=models.PROTECT, null=True
    )
    quote_revision = models.ForeignKey(
        ClientQuotationRevision, on_delete=models.PROTECT, null=True
    )
    po_line = models.ForeignKey(
        ClientPurchaseOrderLine, on_delete=models.PROTECT, null=True
    )
    purchase_line = models.ForeignKey(
        SupplierPurchaseLine, on_delete=models.PROTECT, null=True
    )
    receipt_line = models.ForeignKey(
        GoodsReceiptLine, on_delete=models.PROTECT, null=True
    )
    delivery_line = models.ForeignKey(DeliveryLine, on_delete=models.PROTECT, null=True)
    outcome_line = models.ForeignKey(
        DeliveryOutcomeLine, on_delete=models.PROTECT, null=True
    )
    rejection_action = models.ForeignKey(
        RejectionAction, on_delete=models.PROTECT, null=True
    )
    document = models.ForeignKey(
        ProcurementDocument, on_delete=models.PROTECT, null=True
    )
    related_item = models.ForeignKey(
        RequestItem, on_delete=models.PROTECT, null=True, related_name="linked_events"
    )

    class Meta(Record.Meta):
        ordering = ["occurred_at", "created_at", "id"]
        indexes = [models.Index(fields=["item", "occurred_at"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Tracking events are append-only.")
        return super().save(*args, **kwargs)


class Operation(Record):
    """Persist a request fingerprint/result for safe client retries."""

    key = models.UUIDField(unique=True)
    fingerprint = models.CharField(max_length=64)
    result = models.JSONField()


class ClientDocumentCode(Record):
    """Stable numbering identity; never inferred from mutable client names."""

    client = models.OneToOneField(
        "crm.Party", on_delete=models.PROTECT, related_name="document_code"
    )
    code = models.CharField(max_length=4, unique=True)

    class Meta(Record.Meta):
        permissions = [("manage_clientdocumentcode", "Manage client document codes")]
        constraints = [
            models.CheckConstraint(
                condition=Q(code__regex=r"^[A-Z]{3,4}$"), name="proc_client_code_format"
            )
        ]


class DocumentSequence(models.Model):
    year = models.PositiveIntegerField(primary_key=True)
    last_serial = models.PositiveBigIntegerField(default=0)


class DocumentRegister(Record):
    class Kind(models.TextChoices):
        QUOTE = "QT", "Quotation"
        WAYBILL = "WB", "Waybill"
        INVOICE = "IN", "Invoice"
        RECEIPT = "RC", "Receipt"

    request = models.ForeignKey(
        ProcurementRequest,
        on_delete=models.PROTECT,
        related_name="registered_documents",
        null=True,
        blank=True,
    )
    client = models.ForeignKey(
        "crm.Party",
        on_delete=models.PROTECT,
        null=True,
        related_name="document_registers",
    )
    kind = models.CharField(max_length=2, choices=Kind.choices)
    client_code = models.CharField(max_length=4)
    year = models.PositiveIntegerField()
    serial = models.PositiveBigIntegerField()
    number = models.CharField(max_length=60, unique=True)
    quotation = models.OneToOneField(
        ClientQuotation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="registered_document",
    )
    delivery = models.OneToOneField(
        Delivery,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="registered_document",
    )

    class Meta(Record.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["year", "serial"], name="proc_annual_document_serial"
            )
        ]
        permissions = [
            ("generate_documentregister", "Generate procurement document files"),
            ("requestapproval_documentregister", "Request ledger document approval"),
        ]


class DocumentVersion(Record):
    register = models.ForeignKey(
        DocumentRegister, on_delete=models.PROTECT, related_name="versions"
    )
    revision = models.PositiveIntegerField()
    quotation_revision = models.OneToOneField(
        ClientQuotationRevision,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="document_version",
    )
    snapshot = models.JSONField()
    approval_requested_at = models.DateTimeField(null=True, blank=True)
    approval_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Approval is resolved by the future ledger integration, never writable by procurement.
    ledger_reference = models.CharField(max_length=150, blank=True)

    class Meta(Record.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["register", "revision"], name="proc_document_revision_unique"
            )
        ]


class DocumentAsset(Record):
    version = models.ForeignKey(
        DocumentVersion, on_delete=models.PROTECT, related_name="assets"
    )
    document = models.OneToOneField(ProcurementDocument, on_delete=models.PROTECT)
    format = models.CharField(max_length=3, choices=[("PDF", "PDF"), ("JPG", "JPG")])
    page = models.PositiveIntegerField(default=0)
    draft = models.BooleanField(default=True)
    template_version = models.CharField(max_length=40)

    class Meta(Record.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["version", "format", "page", "draft", "template_version"],
                name="proc_document_asset_unique",
            )
        ]
