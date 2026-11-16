"""Procurement item requests and their append-only activity trackers."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


def reference():
    """Historical migration callable; no runtime Procurement records use it."""
    return uuid.uuid4().hex.upper()


class Record(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_by_name = models.CharField(max_length=255)

    class Meta:
        abstract = True


class ProcurementClientCode(Record):
    client = models.OneToOneField(
        "crm.Party", on_delete=models.PROTECT, related_name="procurement_client_code"
    )
    code = models.CharField(max_length=3, unique=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(code__regex=r"^[A-Z]{3}$"),
                name="proc_client_code_three_letters",
            )
        ]


class ItemDailySequence(models.Model):
    date = models.DateField(primary_key=True)
    next_serial = models.PositiveIntegerField(default=0)


class ItemRequest(Record):
    class ReceivedVia(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        CALL = "CALL", "Call"
        WALK_IN = "WALK_IN", "Walk-in client"
        CUSTOM = "CUSTOM", "Custom"

    client = models.ForeignKey(
        "crm.Party", on_delete=models.PROTECT, related_name="item_requests"
    )
    client_name = models.CharField(max_length=255)
    received_via = models.CharField(
        max_length=20, choices=ReceivedVia.choices, blank=True
    )
    received_via_custom = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-received_at", "-created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(received_via="CUSTOM") & ~Q(received_via_custom="")
                    | ~Q(received_via="CUSTOM")
                ),
                name="proc_custom_channel_required",
            )
        ]


class RequestedItem(Record):
    class Urgency(models.TextChoices):
        LOW = "LOW", "Low"
        NORMAL = "NORMAL", "Normal"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"
        CRITICAL = "CRITICAL", "Critical"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        SUSPENDED = "SUSPENDED", "Suspended"
        DELIVERED = "DELIVERED", "Delivered"
        CLOSED = "CLOSED", "Closed"

    request = models.ForeignKey(
        ItemRequest, on_delete=models.PROTECT, related_name="items"
    )
    reordered_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reorders",
    )
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField()
    specification = models.TextField(blank=True)
    brand = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit = models.CharField(max_length=80)
    urgency = models.CharField(
        max_length=10, choices=Urgency.choices, default=Urgency.NORMAL
    )
    deadline = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.OPEN
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "id"]
        permissions = [
            ("view_itemtracker", "Can view item trackers"),
            ("add_itemactivity", "Can add item activities"),
            ("change_itemstatus", "Can change item status"),
            ("download_itemimage", "Can download requested item images"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0),
                name="proc_requested_item_quantity_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status__in=["DELIVERED", "CLOSED"], completed_at__isnull=False)
                    | Q(status__in=["OPEN", "SUSPENDED"], completed_at__isnull=True)
                ),
                name="proc_requested_item_completion_valid",
            ),
        ]


class RequestedItemImage(Record):
    item = models.ForeignKey(
        RequestedItem, on_delete=models.PROTECT, related_name="images"
    )
    position = models.PositiveSmallIntegerField(default=0)
    caption = models.CharField(max_length=255, blank=True)
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)
    storage_provider = models.CharField(max_length=30)
    external_file_id = models.TextField()
    storage_path = models.TextField(blank=True)

    class Meta:
        ordering = ["position", "created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "position"], name="proc_item_image_position_unique"
            )
        ]


class ItemActivity(Record):
    class Category(models.TextChoices):
        ACTIVITY = "ACTIVITY", "Activity"
        COMMENT = "COMMENT", "Comment"
        SYSTEM = "SYSTEM", "System"

    item = models.ForeignKey(
        RequestedItem, on_delete=models.PROTECT, related_name="activities"
    )
    category = models.CharField(max_length=10, choices=Category.choices)
    title_code = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    status_result = models.CharField(
        max_length=12, choices=RequestedItem.Status.choices, blank=True
    )
    source_type = models.CharField(max_length=50, blank=True)
    source_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["occurred_at", "created_at", "id"]
        indexes = [
            models.Index(fields=["item", "occurred_at"], name="proc_activity_item_time")
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Item activities are append-only.")
        return super().save(*args, **kwargs)


class SupplierQuote(Record):
    class ReceivedVia(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        CALL = "CALL", "Call"
        PHYSICAL = "PHYSICAL", "Physical copy"
        PORTAL = "PORTAL", "Supplier portal"
        CUSTOM = "CUSTOM", "Custom"

    supplier = models.ForeignKey(
        "crm.Party",
        on_delete=models.PROTECT,
        related_name="procurement_supplier_quotes",
    )
    supplier_name = models.CharField(max_length=255)
    supplier_reference = models.CharField(max_length=100, blank=True)
    quotation_date = models.DateField(default=timezone.localdate)
    expires_on = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="NGN")
    received_via = models.CharField(max_length=20, choices=ReceivedVia.choices)
    received_via_custom = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    attachment_filename = models.CharField(max_length=255, blank=True)
    attachment_mime_type = models.CharField(max_length=100, blank=True)
    attachment_size_bytes = models.PositiveIntegerField(null=True, blank=True)
    attachment_checksum = models.CharField(max_length=64, blank=True)
    attachment_storage_provider = models.CharField(max_length=30, blank=True)
    attachment_external_file_id = models.TextField(blank=True)
    attachment_storage_path = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-quotation_date", "-created_at", "id"]
        permissions = [
            ("download_supplierquote", "Can download supplier quotation attachments")
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(received_via="CUSTOM") & ~Q(received_via_custom="")
                    | ~Q(received_via="CUSTOM")
                ),
                name="proc_supplier_quote_custom_channel",
            )
        ]


class SupplierQuoteItem(Record):
    quote = models.ForeignKey(
        SupplierQuote, on_delete=models.PROTECT, related_name="items"
    )
    item = models.ForeignKey(
        RequestedItem, on_delete=models.PROTECT, related_name="supplier_quote_items"
    )
    item_name = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit = models.CharField(max_length=80)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    tax_enabled = models.BooleanField(default=False)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=7.5)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["quote", "item"], name="proc_supplier_quote_item_unique"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="proc_supplier_quote_qty_positive"
            ),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0),
                name="proc_supplier_quote_price_nonnegative",
            ),
        ]


class QuotationDailySequence(models.Model):
    date = models.DateField(primary_key=True)
    next_serial = models.PositiveIntegerField(default=1)


class ClientQuotation(Record):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ISSUED = "ISSUED", "Issued"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"
        SUPERSEDED = "SUPERSEDED", "Superseded"

    client = models.ForeignKey(
        "crm.Party",
        on_delete=models.PROTECT,
        related_name="procurement_client_quotations",
    )
    client_name = models.CharField(max_length=255)
    reference = models.CharField(max_length=30, unique=True)
    version = models.PositiveIntegerField(default=1)
    quotation_date = models.DateField(default=timezone.localdate)
    expires_on = models.DateField()
    currency = models.CharField(max_length=3, default="NGN")
    tax_enabled = models.BooleanField(default=False)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=7.5)
    discount_type = models.CharField(
        max_length=10,
        choices=[("NONE", "None"), ("PERCENT", "Percentage"), ("AMOUNT", "Amount")],
        default="NONE",
    )
    discount_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    terms = models.TextField(blank=True)
    recipient_email = models.EmailField(blank=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    snapshot = models.JSONField(default=dict, blank=True)
    document_filename = models.CharField(max_length=255, blank=True)
    document_mime_type = models.CharField(max_length=100, blank=True)
    document_size_bytes = models.PositiveIntegerField(null=True, blank=True)
    document_checksum = models.CharField(max_length=64, blank=True)
    document_storage_provider = models.CharField(max_length=30, blank=True)
    document_external_file_id = models.TextField(blank=True)
    document_storage_path = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-quotation_date", "-created_at", "id"]
        permissions = [
            ("issue_clientquotation", "Can issue client quotations"),
            ("communicate_clientquotation", "Can communicate client quotations"),
            ("revise_clientquotation", "Can revise issued client quotations"),
            ("remove_clientquotation", "Can remove client quotation documents"),
        ]


class ClientQuotationItem(Record):
    quotation = models.ForeignKey(
        ClientQuotation, on_delete=models.PROTECT, related_name="items"
    )
    item = models.ForeignKey(
        RequestedItem, on_delete=models.PROTECT, related_name="client_quotation_items"
    )
    item_name = models.CharField(max_length=255)
    description = models.TextField()
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit = models.CharField(max_length=80)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["quotation", "item"], name="proc_client_quote_item_unique"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="proc_client_quote_qty_positive"
            ),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0),
                name="proc_client_quote_price_nonnegative",
            ),
        ]


class ClientQuotationRevision(Record):
    quotation = models.ForeignKey(
        ClientQuotation, on_delete=models.PROTECT, related_name="revisions"
    )
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    issued_at = models.DateTimeField()
    issued_by_name = models.CharField(max_length=255)
    document_filename = models.CharField(max_length=255)
    document_mime_type = models.CharField(max_length=100)
    document_size_bytes = models.PositiveIntegerField()
    document_checksum = models.CharField(max_length=64)
    document_storage_provider = models.CharField(max_length=30)
    document_external_file_id = models.TextField(blank=True)
    document_storage_path = models.TextField(blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    removed_by_name = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["quotation", "version"],
                name="proc_client_quote_revision_unique",
            )
        ]


class ClientQuotationCommunication(Record):
    class Method(models.TextChoices):
        DOWNLOAD = "DOWNLOAD", "Downloaded"
        PRINT = "PRINT", "Printed"
        EMAIL = "EMAIL", "Email"

    quotation = models.ForeignKey(
        ClientQuotation, on_delete=models.PROTECT, related_name="communications"
    )
    method = models.CharField(max_length=20, choices=Method.choices)
    destination = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)


class PurchaseOrderDailySequence(models.Model):
    date = models.DateField(primary_key=True)
    next_serial = models.PositiveIntegerField(default=1)


class PurchaseOrder(Record):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        ACTIVE = "ACTIVE", "Active"
        FULFILLED = "FULFILLED", "Fulfilled"
        ATTENTION_REQUIRED = "ATTENTION_REQUIRED", "Attention required"
        SUSPENDED = "SUSPENDED", "Suspended"

    client = models.ForeignKey(
        "crm.Party",
        on_delete=models.PROTECT,
        related_name="procurement_purchase_orders",
    )
    client_name = models.CharField(max_length=255)
    reference = models.CharField(max_length=30, unique=True)
    client_reference = models.CharField(max_length=120)
    document_date = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    received_via = models.CharField(max_length=30, blank=True)
    urgency = models.CharField(
        max_length=10,
        choices=RequestedItem.Urgency.choices,
        default=RequestedItem.Urgency.NORMAL,
    )
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.QUEUED
    )
    status_reason = models.TextField(blank=True)
    currency = models.CharField(max_length=3, default="NGN")
    notes = models.TextField(blank=True)
    attachment_filename = models.CharField(max_length=255, blank=True)
    attachment_mime_type = models.CharField(max_length=100, blank=True)
    attachment_size_bytes = models.PositiveIntegerField(null=True, blank=True)
    attachment_checksum = models.CharField(max_length=64, blank=True)
    attachment_storage_provider = models.CharField(max_length=30, blank=True)
    attachment_external_file_id = models.TextField(blank=True)
    attachment_storage_path = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at", "-urgency", "status", "id"]
        permissions = [
            ("download_purchaseorder", "Can download purchase order attachments"),
            ("change_purchaseorderstatus", "Can change purchase order status"),
        ]


class PurchaseOrderItem(Record):
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, related_name="items"
    )
    item = models.ForeignKey(
        RequestedItem, on_delete=models.PROTECT, related_name="purchase_order_items"
    )
    item_name = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit = models.CharField(max_length=80)
    unit_price = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["purchase_order", "item"], name="proc_po_item_unique"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="proc_po_qty_positive"
            ),
            models.CheckConstraint(
                condition=Q(unit_price__isnull=True) | Q(unit_price__gte=0),
                name="proc_po_price_nonnegative",
            ),
        ]


class Purchase(Record):
    class LogisticsMethod(models.TextChoices):
        PUBLIC_TRANSPORT = "PUBLIC_TRANSPORT", "Public transport"
        LOGISTICS_COMPANY = "LOGISTICS_COMPANY", "Logistics company"
        PICKUP = "PICKUP", "Pickup at seller"
        HOLAN_VEHICLE = "HOLAN_VEHICLE", "Holan vehicle"
        COURIER = "COURIER", "Courier"
        ROAD_FREIGHT = "ROAD_FREIGHT", "Road freight"
        AIR_CARGO = "AIR_CARGO", "Air cargo"
        SEA_FREIGHT = "SEA_FREIGHT", "Sea freight"
        RAIL_FREIGHT = "RAIL_FREIGHT", "Rail freight"
        SHIPMENT = "SHIPMENT", "Shipment"
        CUSTOM = "CUSTOM", "Custom"

    purchased_from = models.ForeignKey(
        "crm.Party", on_delete=models.PROTECT, related_name="procurement_purchases"
    )
    purchased_from_name = models.CharField(max_length=255)
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="purchases",
    )
    purchased_at = models.DateTimeField(default=timezone.now)
    currency = models.CharField(max_length=3, default="NGN")
    supplier_document_reference = models.CharField(max_length=120, blank=True)
    logistics_method = models.CharField(
        max_length=30, choices=LogisticsMethod.choices, blank=True
    )
    logistics_method_custom = models.CharField(max_length=120, blank=True)
    logistics_cost = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-purchased_at", "-created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(logistics_cost__isnull=True) | Q(logistics_cost__gte=0),
                name="proc_purchase_logistics_nonnegative",
            ),
            models.CheckConstraint(
                condition=~Q(logistics_method="CUSTOM")
                | ~Q(logistics_method_custom=""),
                name="proc_purchase_custom_logistics",
            ),
        ]


class PurchaseItem(Record):
    class Status(models.TextChoices):
        PAID = "PAID", "Paid"
        TRANSIT = "TRANSIT", "Transit"
        RECEIVED = "RECEIVED", "Received"
        RETURNED = "RETURNED", "Returned"
        REFUNDED = "REFUNDED", "Refunded"

    purchase = models.ForeignKey(
        Purchase, on_delete=models.PROTECT, related_name="items"
    )
    item = models.ForeignKey(
        RequestedItem, on_delete=models.PROTECT, related_name="purchase_items"
    )
    item_name = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit = models.CharField(max_length=80)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PAID
    )
    status_reason = models.TextField(blank=True)
    status_changed_at = models.DateTimeField(default=timezone.now)
    status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["created_at", "id"]
        permissions = [
            ("change_purchaseitemstatus", "Can change purchased item status")
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["purchase", "item"], name="proc_purchase_item_unique"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="proc_purchase_qty_positive"
            ),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0), name="proc_purchase_price_nonnegative"
            ),
        ]


class WaybillDailySequence(models.Model):
    date = models.DateField(primary_key=True)
    next_serial = models.PositiveIntegerField(default=1)


class Waybill(Record):
    client = models.ForeignKey(
        "crm.Party", on_delete=models.PROTECT, related_name="procurement_waybills"
    )
    client_name = models.CharField(max_length=255)
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="waybills",
    )
    reference = models.CharField(max_length=32, unique=True)
    waybill_date = models.DateField(default=timezone.localdate)
    dispatch_at = models.DateTimeField(null=True, blank=True)
    delivery_personnel = models.CharField(max_length=255)
    delivered_to = models.CharField(max_length=255)
    delivery_address = models.TextField()
    logistics_method = models.CharField(max_length=120, blank=True)
    driver_name = models.CharField(max_length=255, blank=True)
    vehicle_details = models.CharField(max_length=255, blank=True)
    signatory_name = models.CharField(max_length=255, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    document_filename = models.CharField(max_length=255, blank=True)
    document_mime_type = models.CharField(max_length=100, blank=True)
    document_size_bytes = models.PositiveIntegerField(null=True, blank=True)
    document_checksum = models.CharField(max_length=64, blank=True)
    document_storage_provider = models.CharField(max_length=30, blank=True)
    document_external_file_id = models.TextField(blank=True)
    document_storage_path = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-waybill_date", "-created_at", "id"]
        permissions = [
            ("issue_waybill", "Can issue waybills"),
            ("communicate_waybill", "Can communicate waybills"),
        ]


class WaybillItem(Record):
    waybill = models.ForeignKey(Waybill, on_delete=models.PROTECT, related_name="items")
    item = models.ForeignKey(
        RequestedItem, on_delete=models.PROTECT, related_name="waybill_items"
    )
    item_name = models.CharField(max_length=255)
    description = models.TextField()
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit = models.CharField(max_length=80)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["waybill", "item"], name="proc_waybill_item_unique"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="proc_waybill_qty_positive"
            ),
        ]


class WaybillCommunication(Record):
    class Method(models.TextChoices):
        DOWNLOAD = "DOWNLOAD", "Downloaded"
        PRINT = "PRINT", "Printed"
        EMAIL = "EMAIL", "Email"

    waybill = models.ForeignKey(
        Waybill, on_delete=models.PROTECT, related_name="communications"
    )
    method = models.CharField(max_length=20, choices=Method.choices)
    destination = models.CharField(max_length=255, blank=True)


class ItemVerdict(Record):
    class Verdict(models.TextChoices):
        ACCEPTED = "ACCEPTED", "Accepted by client"
        REJECTED = "REJECTED", "Rejected by client"
        RETURNED = "RETURNED", "Returned to supplier"
        DISPOSED = "DISPOSED", "Disposed"

    waybill_item = models.ForeignKey(
        WaybillItem, on_delete=models.PROTECT, related_name="verdicts"
    )
    item = models.ForeignKey(
        RequestedItem, on_delete=models.PROTECT, related_name="verdicts"
    )
    verdict = models.CharField(max_length=12, choices=Verdict.choices)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit = models.CharField(max_length=80)
    occurred_at = models.DateTimeField(default=timezone.now)
    client_recipient = models.CharField(max_length=255, blank=True)
    reason = models.TextField(blank=True)
    supplier = models.ForeignKey(
        "crm.Party",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="procurement_item_returns",
    )
    supplier_name = models.CharField(max_length=255, blank=True)
    disposal_method = models.CharField(max_length=255, blank=True)
    authorised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    authorised_by_name = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["occurred_at", "created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="proc_verdict_qty_positive"
            )
        ]


class InvoiceRequest(Record):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        APPROVED = "APPROVED", "Approved"
        CANCELLED = "CANCELLED", "Cancelled"

    item = models.ForeignKey(
        RequestedItem, on_delete=models.PROTECT, related_name="invoice_requests"
    )
    client = models.ForeignKey(
        "crm.Party",
        on_delete=models.PROTECT,
        related_name="procurement_invoice_requests",
    )
    client_name = models.CharField(max_length=255)
    description = models.CharField(max_length=1000)
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    unit = models.CharField(max_length=40)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default="NGN")
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="invoice_requests",
    )
    purchase_order_reference = models.CharField(max_length=40, blank=True)
    waybill = models.ForeignKey(
        Waybill,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="invoice_requests",
    )
    waybill_reference = models.CharField(max_length=40, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.QUEUED
    )
    decision_reason = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    decided_by_name = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "id"]
        permissions = [
            ("approve_invoicerequest", "Can approve procurement invoice requests"),
            ("cancel_invoicerequest", "Can cancel procurement invoice requests"),
            ("email_invoicerequest", "Can email approved procurement invoices"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="proc_invoice_request_qty_positive"
            ),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0),
                name="proc_invoice_request_price_nonnegative",
            ),
        ]
