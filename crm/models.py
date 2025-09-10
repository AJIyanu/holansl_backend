import uuid
from django.db import models


class Party(models.Model):
    """
    Abstract entity for any person or organization involved
    (client, supplier, logistics).
    """
    PARTY_TYPES = [
        ("client", "Client"),
        ("supplier", "Supplier"),
        ("logistics", "Logistics"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    party_type = models.CharField(max_length=20, choices=PARTY_TYPES)
    is_organization = models.BooleanField(default=True)

    # Common contact details
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    address = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.get_party_type_display()})"


class ContactPerson(models.Model):
    """
    For parties that are organizations, store their contact people.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="contacts")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True, null=True)
    position = models.CharField(max_length=100, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    link = models.URLField(blank=True, null=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name or ''} - {self.party.name}"
