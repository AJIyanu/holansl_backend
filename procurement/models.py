import uuid
from django.db import models
from crm.models import Party, ContactPerson


class ClientRequest(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("discarded", "Discarded"),
        ("completed", "Completed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    client = models.ForeignKey(
        Party,
        on_delete=models.CASCADE,
        limit_choices_to={
            "party_type": "client"})
    contact_person = models.ForeignKey(
        ContactPerson,
        on_delete=models.SET_NULL,
        blank=True,
        null=True)
    item_name = models.CharField(max_length=255)
    specification = models.TextField(blank=True, null=True)
    model = models.CharField(max_length=100, blank=True, null=True)
    brand = models.CharField(max_length=100, blank=True, null=True)
    uom = models.CharField(max_length=50)
    quantity = models.PositiveIntegerField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending")
    comments = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Request: {self.item_name} ({self.client.name})"


class SupplierQuote(models.Model):
    IMPORT_TYPE = [
        ("local", "Local"),
        ("imported", "Imported"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    client_request = models.ForeignKey(
        ClientRequest,
        on_delete=models.CASCADE,
        related_name="quotes")
    supplier = models.ForeignKey(
        Party,
        on_delete=models.CASCADE,
        limit_choices_to={
            "party_type": "supplier"})

    price = models.DecimalField(max_digits=12, decimal_places=2)
    import_type = models.CharField(
        max_length=20,
        choices=IMPORT_TYPE,
        default="local")
    logistics_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default="USD")
    quoted_price = models.DecimalField(
        max_digits=12, decimal_places=2, blank=True, null=True)

    comments = models.TextField(blank=True, null=True)
    lead_time_days = models.PositiveIntegerField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Quote from {self.supplier.name} for {self.client_request.item_name}"


class PurchaseOrder(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("ordered", "Item Ordered"),
        ("ready", "Ready for Delivery"),
        ("canceled", "Canceled"),
        ("delivered", "Delivered"),
        ("accepted", "Accepted"),
        ("rejected", "Rejected"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    po_number = models.CharField(max_length=100, unique=True)
    client = models.ForeignKey(
        Party,
        on_delete=models.CASCADE,
        limit_choices_to={
            "party_type": "client"})
    supplier_quote = models.ForeignKey(
        SupplierQuote,
        on_delete=models.CASCADE,
        related_name="purchase_orders")

    quantity = models.PositiveIntegerField()
    uom = models.CharField(max_length=50)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    expiry_date = models.DateField(blank=True, null=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"PO {self.po_number} ({self.client.name})"


class POTracker(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name="trackers")
    status = models.CharField(
        max_length=20,
        choices=PurchaseOrder.STATUS_CHOICES)
    description = models.TextField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.purchase_order.po_number} - {self.get_status_display()}"
