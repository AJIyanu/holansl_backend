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
