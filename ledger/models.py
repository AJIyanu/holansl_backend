import uuid
from django.db import models
from django.utils import timezone
from crm.models import Party
from procurement.models import SupplierQuote, PurchaseOrder


class Category(models.Model):
    FLOW_CHOICES = [
        ("CREDIT", "Credit (Money In)"),
        ("DEBIT", "Debit (Money Out)"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    flow = models.CharField(max_length=10, choices=FLOW_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.flow})"


class Expectation(models.Model):
    STATUS_CHOICES = [
        ("FULFILLED", "Fulfilled"),
        ("NOT_FULFILLED", "Not Fulfilled"),
        ("PARTLY_FULFILLED", "Partly Fulfilled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, related_name="expectations")
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    duration = models.PositiveIntegerField(null=True, blank=True, help_text="Duration in days")
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=10, default="NGN")
    compound_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="NOT_FULFILLED")
    party = models.ForeignKey(Party, on_delete=models.SET_NULL, null=True, blank=True, related_name="expectations")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.type} - {self.amount} {self.currency} [{self.status}]"


class Transaction(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("ACKNOWLEDGED", "Acknowledged"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    date = models.DateTimeField(default=timezone.now)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, related_name="transactions")
    party = models.ForeignKey(Party, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")
    supplier_offer = models.ForeignKey(SupplierQuote, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")
    expectations = models.ForeignKey(Expectation, on_delete=models.SET_NULL, null=True, related_name="transactions", blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=10, default="NGN")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {self.amount} {self.currency} [{self.status}]"
