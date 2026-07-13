"""
CRM domain models.

This module defines the master records used to represent external people,
organisations, informal businesses, supplier sources, contact information,
relationship history, notes and communication activity.

Model classes accept validated field values through Django's ORM and return
persisted model instances when created or saved.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.text import slugify

from .crypto import (
    decrypt_sensitive_value,
    encrypt_sensitive_value,
    hash_sensitive_value,
    mask_sensitive_value,
    sensitive_value_last_four,
)
from .normalizers import (
    normalize_contact_value,
    normalize_party_name,
    normalize_text,
)


class UUIDTimeStampedModel(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PartyQuerySet(models.QuerySet):
    def active(self):
        return self.filter(
            status=Party.Status.ACTIVE,
            is_archived=False,
        )

    def directory(self):
        return self.exclude(
            status=Party.Status.MERGED,
        ).filter(
            is_archived=False,
        )

    def with_role(self, role: str):
        return self.filter(
            roles__role=role,
            roles__is_active=True,
        ).distinct()


class Party(UUIDTimeStampedModel):
    class EntityKind(models.TextChoices):
        ORGANISATION = "ORGANISATION", "Organisation"
        INDIVIDUAL = "INDIVIDUAL", "Individual"
        TRADING_NAME = (
            "TRADING_NAME",
            "Trading name / informal business",
        )

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        SUSPENDED = "SUSPENDED", "Suspended"
        BLOCKED = "BLOCKED", "Blocked"
        MERGED = "MERGED", "Merged"

    class VerificationLevel(models.TextChoices):
        MINIMAL = "MINIMAL", "Minimal"
        BASIC = "BASIC", "Basic"
        VERIFIED = "VERIFIED", "Verified"

    display_name = models.CharField(max_length=255)

    normalized_name = models.CharField(
        max_length=255,
        editable=False,
        db_index=True,
    )

    entity_kind = models.CharField(
        max_length=20,
        choices=EntityKind.choices,
        default=EntityKind.ORGANISATION,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )

    verification_level = models.CharField(
        max_length=20,
        choices=VerificationLevel.choices,
        default=VerificationLevel.MINIMAL,
    )

    is_archived = models.BooleanField(
        default=False,
        db_index=True,
    )

    archived_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    merged_into = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="merged_records",
        blank=True,
        null=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_parties_created",
        blank=True,
        null=True,
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_parties_updated",
        blank=True,
        null=True,
    )

    objects = PartyQuerySet.as_manager()

    class Meta:
        ordering = [
            "display_name",
            "id",
        ]

        indexes = [
            models.Index(
                fields=[
                    "status",
                    "is_archived",
                ],
                name="crm_party_status_arch_idx",
            ),
            models.Index(
                fields=[
                    "entity_kind",
                    "display_name",
                ],
                name="crm_party_kind_name_idx",
            ),
        ]

        constraints = [
            models.CheckConstraint(
                condition=~Q(
                    merged_into=F("id"),
                ),
                name="crm_party_not_merged_self",
            ),
        ]

        permissions = [
            (
                "deactivate_party",
                "Can deactivate CRM party",
            ),
            (
                "archive_party",
                "Can archive CRM party",
            ),
            (
                "block_party",
                "Can block CRM party",
            ),
            (
                "merge_party",
                "Can merge duplicate CRM parties",
            ),
            (
                "view_party_history",
                "Can view CRM party history",
            ),
        ]

    def __str__(self) -> str:
        return self.display_name

    def clean(self):
        super().clean()

        self.display_name = normalize_text(
            self.display_name,
        )

        if not self.display_name:
            raise ValidationError(
                {"display_name": ("A party display name is required.")}
            )

        if self.merged_into_id and self.merged_into_id == self.id:
            raise ValidationError(
                {"merged_into": ("A party cannot be merged into itself.")}
            )

        if self.status == self.Status.MERGED and not self.merged_into_id:
            raise ValidationError(
                {"merged_into": ("A merged party must identify its surviving party.")}
            )

        if self.merged_into_id and self.status != self.Status.MERGED:
            raise ValidationError(
                {"status": ("A party with a merge target must have MERGED status.")}
            )

    def save(self, *args, **kwargs):
        """
        Normalise, validate and persist the CRM party.

        Args:
            *args: Positional arguments accepted by Django model save.
            **kwargs: Keyword arguments accepted by Django model save.

        Returns:
            None.

        Raises:
            ValidationError: If the party identity or lifecycle state is invalid.
        """

        self.display_name = normalize_text(
            self.display_name,
        )

        self.normalized_name = normalize_party_name(
            self.display_name,
        )

        if self.is_archived and self.archived_at is None:
            self.archived_at = timezone.now()
        elif not self.is_archived:
            self.archived_at = None

        self.clean()

        update_fields = kwargs.get(
            "update_fields",
        )

        if update_fields is not None:
            kwargs["update_fields"] = set(
                update_fields,
            ) | {
                "display_name",
                "normalized_name",
                "archived_at",
            }

        return super().save(
            *args,
            **kwargs,
        )

    @property
    def is_selectable(self) -> bool:
        return (
            self.status == self.Status.ACTIVE
            and not self.is_archived
            and self.merged_into_id is None
        )

    def has_role(self, role: str) -> bool:
        return self.roles.filter(
            role=role,
            is_active=True,
        ).exists()

    def is_traceable(self) -> bool:
        has_contact = self.contact_methods.filter(
            is_active=True,
        ).exists()

        has_source = (
            self.sources.filter(
                is_active=True,
            )
            .exclude(
                platform_name="",
                seller_name="",
                external_id="",
                profile_url="",
                listing_url="",
                market_name="",
                location_details="",
                referrer_name="",
                notes="",
            )
            .exists()
        )

        return has_contact or has_source


class PartyRole(UUIDTimeStampedModel):
    class Role(models.TextChoices):
        CLIENT = "CLIENT", "Client"
        SUPPLIER = "SUPPLIER", "Supplier"
        PROSPECT = "PROSPECT", "Prospect"
        LOGISTICS_PROVIDER = (
            "LOGISTICS_PROVIDER",
            "Logistics provider",
        )
        SERVICE_PROVIDER = (
            "SERVICE_PROVIDER",
            "Service provider",
        )
        OTHER = "OTHER", "Other"

    party = models.ForeignKey(
        Party,
        on_delete=models.CASCADE,
        related_name="roles",
    )

    role = models.CharField(
        max_length=30,
        choices=Role.choices,
    )

    is_active = models.BooleanField(default=True)

    activated_at = models.DateTimeField(
        default=timezone.now,
    )

    deactivated_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    notes = models.TextField(blank=True)

    class Meta:
        ordering = [
            "party__display_name",
            "role",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "party",
                    "role",
                ],
                name="crm_unique_party_role",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "role",
                    "is_active",
                ],
                name="crm_role_active_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.party.display_name} — {self.get_role_display()}"

    def save(self, *args, **kwargs):
        """
        Maintain role lifecycle dates and persist the role.

        Args:
            *args: Positional arguments accepted by Django model save.
            **kwargs: Keyword arguments accepted by Django model save.

        Returns:
            None.
        """

        if self.is_active:
            self.deactivated_at = None
        elif self.deactivated_at is None:
            self.deactivated_at = timezone.now()

        return super().save(
            *args,
            **kwargs,
        )


class OrganisationProfile(UUIDTimeStampedModel):
    party = models.OneToOneField(
        Party,
        on_delete=models.CASCADE,
        related_name="organisation_profile",
    )

    legal_name = models.CharField(
        max_length=255,
        blank=True,
    )

    trading_name = models.CharField(
        max_length=255,
        blank=True,
    )

    website = models.URLField(
        max_length=500,
        blank=True,
    )

    industry = models.CharField(
        max_length=150,
        blank=True,
    )

    business_description = models.TextField(
        blank=True,
    )

    registration_country = models.CharField(
        max_length=2,
        blank=True,
    )

    incorporation_date = models.DateField(
        blank=True,
        null=True,
    )

    class Meta:
        ordering = [
            "party__display_name",
        ]

    def __str__(self) -> str:
        return self.legal_name or self.trading_name or self.party.display_name

    def clean(self):
        super().clean()

        if self.party_id and self.party.entity_kind not in {
            Party.EntityKind.ORGANISATION,
            Party.EntityKind.TRADING_NAME,
        }:
            raise ValidationError(
                {
                    "party": (
                        "An organisation profile requires "
                        "an organisation or trading-name party."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.legal_name = normalize_text(
            self.legal_name,
        )
        self.trading_name = normalize_text(
            self.trading_name,
        )
        self.registration_country = (self.registration_country or "").upper()

        self.full_clean()

        return super().save(*args, **kwargs)


class PersonProfile(UUIDTimeStampedModel):
    party = models.OneToOneField(
        Party,
        on_delete=models.CASCADE,
        related_name="person_profile",
    )

    title = models.CharField(
        max_length=30,
        blank=True,
    )

    first_name = models.CharField(
        max_length=100,
        blank=True,
    )

    middle_name = models.CharField(
        max_length=100,
        blank=True,
    )

    last_name = models.CharField(
        max_length=100,
        blank=True,
    )

    preferred_name = models.CharField(
        max_length=150,
        blank=True,
    )

    class Meta:
        ordering = [
            "party__display_name",
        ]

    def __str__(self) -> str:
        return self.full_name or self.party.display_name

    @property
    def full_name(self) -> str:
        return " ".join(
            value
            for value in [
                self.first_name,
                self.middle_name,
                self.last_name,
            ]
            if value
        ).strip()

    def clean(self):
        super().clean()

        if self.party_id and self.party.entity_kind != Party.EntityKind.INDIVIDUAL:
            raise ValidationError(
                {"party": ("A person profile requires an individual party.")}
            )

    def save(self, *args, **kwargs):
        for field_name in (
            "title",
            "first_name",
            "middle_name",
            "last_name",
            "preferred_name",
        ):
            setattr(
                self,
                field_name,
                normalize_text(
                    getattr(self, field_name),
                ),
            )

        self.full_clean()

        return super().save(*args, **kwargs)


class ContactRole(UUIDTimeStampedModel):
    name = models.CharField(
        max_length=100,
        unique=True,
    )

    slug = models.SlugField(
        max_length=120,
        unique=True,
        blank=True,
    )

    description = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)

    sort_order = models.PositiveSmallIntegerField(
        default=0,
    )

    class Meta:
        ordering = [
            "sort_order",
            "name",
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.name = normalize_text(self.name)

        if not self.slug:
            self.slug = slugify(self.name)

        return super().save(*args, **kwargs)


class PartyAffiliation(UUIDTimeStampedModel):
    person = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="organisation_affiliations",
    )

    organisation = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="people_affiliations",
    )

    job_title = models.CharField(
        max_length=150,
        blank=True,
    )

    department = models.CharField(
        max_length=150,
        blank=True,
    )

    start_date = models.DateField(
        blank=True,
        null=True,
    )

    end_date = models.DateField(
        blank=True,
        null=True,
    )

    is_current = models.BooleanField(default=True)

    is_primary_contact = models.BooleanField(
        default=False,
    )

    notes = models.TextField(blank=True)

    contact_roles = models.ManyToManyField(
        ContactRole,
        through="AffiliationContactRole",
        related_name="affiliations",
        blank=True,
    )

    class Meta:
        ordering = [
            "organisation__display_name",
            "-is_current",
            "person__display_name",
        ]

        constraints = [
            models.CheckConstraint(
                condition=~Q(
                    person=F("organisation"),
                ),
                name="crm_affiliation_distinct",
            ),
            models.UniqueConstraint(
                fields=[
                    "person",
                    "organisation",
                ],
                condition=Q(
                    is_current=True,
                ),
                name="crm_unique_current_affiliation",
            ),
        ]

        permissions = [
            (
                "end_partyaffiliation",
                "Can end CRM party affiliation",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.person.display_name} at {self.organisation.display_name}"

    def clean(self):
        super().clean()

        if self.person_id and self.organisation_id:
            if self.person_id == self.organisation_id:
                raise ValidationError(
                    {"organisation": ("A party cannot be affiliated with itself.")}
                )

            if self.person.entity_kind != Party.EntityKind.INDIVIDUAL:
                raise ValidationError(
                    {"person": ("The affiliation person must be an individual.")}
                )

            if self.organisation.entity_kind not in {
                Party.EntityKind.ORGANISATION,
                Party.EntityKind.TRADING_NAME,
            }:
                raise ValidationError(
                    {
                        "organisation": (
                            "The affiliation organisation "
                            "must be an organisation or "
                            "trading-name party."
                        )
                    }
                )

        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError(
                {"end_date": ("End date cannot be before start date.")}
            )

    def save(self, *args, **kwargs):
        self.job_title = normalize_text(
            self.job_title,
        )
        self.department = normalize_text(
            self.department,
        )

        if self.end_date is not None:
            self.is_current = False

        self.full_clean()

        return super().save(*args, **kwargs)


class AffiliationContactRole(UUIDTimeStampedModel):
    affiliation = models.ForeignKey(
        PartyAffiliation,
        on_delete=models.CASCADE,
        related_name="role_assignments",
    )

    contact_role = models.ForeignKey(
        ContactRole,
        on_delete=models.PROTECT,
        related_name="affiliation_assignments",
    )

    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = [
            "contact_role__sort_order",
            "contact_role__name",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "affiliation",
                    "contact_role",
                ],
                name="crm_unique_affiliation_role",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.affiliation} — {self.contact_role.name}"


class ContactMethod(UUIDTimeStampedModel):
    class MethodType(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        PHONE = "PHONE", "Telephone"
        MOBILE = "MOBILE", "Mobile"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        WEBSITE = "WEBSITE", "Website"
        SOCIAL_MEDIA = "SOCIAL_MEDIA", "Social media"
        MARKETPLACE = (
            "MARKETPLACE",
            "Marketplace account",
        )
        OTHER = "OTHER", "Other"

    party = models.ForeignKey(
        Party,
        on_delete=models.CASCADE,
        related_name="contact_methods",
    )

    method_type = models.CharField(
        max_length=20,
        choices=MethodType.choices,
    )

    value = models.CharField(max_length=500)

    normalized_value = models.CharField(
        max_length=500,
        editable=False,
        db_index=True,
    )

    label = models.CharField(
        max_length=100,
        blank=True,
    )

    is_primary = models.BooleanField(default=False)

    is_verified = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = [
            "party__display_name",
            "method_type",
            "-is_primary",
            "value",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "party",
                    "method_type",
                    "normalized_value",
                ],
                name="crm_unique_contact_value",
            ),
            models.UniqueConstraint(
                fields=[
                    "party",
                    "method_type",
                ],
                condition=Q(
                    is_primary=True,
                    is_active=True,
                ),
                name="crm_one_primary_contact_type",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "method_type",
                    "normalized_value",
                ],
                name="crm_contact_lookup_idx",
            ),
        ]

        permissions = [
            (
                "manage_contactmethod",
                "Can manage CRM contact methods",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.party.display_name}: {self.value}"

    def save(self, *args, **kwargs):
        self.value = normalize_text(self.value)

        self.normalized_value = normalize_contact_value(
            self.method_type,
            self.value,
        )

        if not self.normalized_value:
            raise ValidationError({"value": ("A contact value is required.")})

        if self.is_primary:
            self.is_active = True

        return super().save(*args, **kwargs)


class Address(UUIDTimeStampedModel):
    class AddressType(models.TextChoices):
        REGISTERED = "REGISTERED", "Registered"
        OFFICE = "OFFICE", "Office"
        BILLING = "BILLING", "Billing"
        DELIVERY = "DELIVERY", "Delivery"
        RESIDENTIAL = "RESIDENTIAL", "Residential"
        MARKET = (
            "MARKET",
            "Market / trading location",
        )
        OTHER = "OTHER", "Other"

    party = models.ForeignKey(
        Party,
        on_delete=models.CASCADE,
        related_name="addresses",
    )

    address_type = models.CharField(
        max_length=20,
        choices=AddressType.choices,
        default=AddressType.OTHER,
    )

    label = models.CharField(
        max_length=100,
        blank=True,
    )

    line_1 = models.CharField(
        max_length=255,
        blank=True,
    )

    line_2 = models.CharField(
        max_length=255,
        blank=True,
    )

    city = models.CharField(
        max_length=120,
        blank=True,
    )

    state_region = models.CharField(
        max_length=120,
        blank=True,
    )

    postal_code = models.CharField(
        max_length=30,
        blank=True,
    )

    country_code = models.CharField(
        max_length=2,
        blank=True,
    )

    location_notes = models.TextField(blank=True)

    is_primary = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = [
            "party__display_name",
            "address_type",
            "-is_primary",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "party",
                    "address_type",
                ],
                condition=Q(
                    is_primary=True,
                    is_active=True,
                ),
                name="crm_one_primary_address_type",
            ),
        ]

        permissions = [
            (
                "manage_address",
                "Can manage CRM addresses",
            ),
        ]

    def __str__(self) -> str:
        location = self.line_1 or self.location_notes or self.city

        return f"{self.party.display_name}: {location}"

    def clean(self):
        super().clean()

        if not any(
            [
                self.line_1,
                self.line_2,
                self.city,
                self.state_region,
                self.postal_code,
                self.country_code,
                self.location_notes,
            ]
        ):
            raise ValidationError(
                "At least one address or location detail is required."
            )

    def save(self, *args, **kwargs):
        for field_name in (
            "label",
            "line_1",
            "line_2",
            "city",
            "state_region",
            "postal_code",
            "location_notes",
        ):
            setattr(
                self,
                field_name,
                normalize_text(
                    getattr(self, field_name),
                ),
            )

        self.country_code = (self.country_code or "").upper()

        if self.is_primary:
            self.is_active = True

        self.full_clean()

        return super().save(*args, **kwargs)


class PartySource(UUIDTimeStampedModel):
    class SourceType(models.TextChoices):
        ONLINE_MARKETPLACE = (
            "ONLINE_MARKETPLACE",
            "Online marketplace",
        )
        PHYSICAL_MARKET = (
            "PHYSICAL_MARKET",
            "Physical market",
        )
        DIRECT_CONTACT = (
            "DIRECT_CONTACT",
            "Direct contact",
        )
        REFERRAL = "REFERRAL", "Referral"
        WEBSITE = "WEBSITE", "Website"
        SOCIAL_MEDIA = (
            "SOCIAL_MEDIA",
            "Social media",
        )
        PREVIOUS_TRANSACTION = (
            "PREVIOUS_TRANSACTION",
            "Previous transaction",
        )
        TRADE_DIRECTORY = (
            "TRADE_DIRECTORY",
            "Trade directory",
        )
        EVENT = "EVENT", "Exhibition / event"
        OTHER = "OTHER", "Other"

    party = models.ForeignKey(
        Party,
        on_delete=models.CASCADE,
        related_name="sources",
    )

    source_type = models.CharField(
        max_length=30,
        choices=SourceType.choices,
    )

    platform_name = models.CharField(
        max_length=120,
        blank=True,
        help_text=("For example Jumia, eBay, Amazon, Konga or AliExpress."),
    )

    seller_name = models.CharField(
        max_length=255,
        blank=True,
    )

    external_id = models.CharField(
        max_length=255,
        blank=True,
    )

    profile_url = models.URLField(
        max_length=1000,
        blank=True,
    )

    listing_url = models.URLField(
        max_length=1000,
        blank=True,
    )

    market_name = models.CharField(
        max_length=255,
        blank=True,
    )

    location_details = models.TextField(blank=True)

    referrer_name = models.CharField(
        max_length=255,
        blank=True,
    )

    notes = models.TextField(blank=True)

    discovered_at = models.DateField(
        default=timezone.localdate,
    )

    last_verified_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    discovered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_sources_discovered",
        blank=True,
        null=True,
    )

    is_primary = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = [
            "party__display_name",
            "-is_primary",
            "-discovered_at",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "party",
                ],
                condition=Q(
                    is_primary=True,
                    is_active=True,
                ),
                name="crm_one_primary_source",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "source_type",
                    "platform_name",
                ],
                name="crm_source_platform_idx",
            ),
            models.Index(
                fields=[
                    "platform_name",
                    "external_id",
                ],
                name="crm_source_external_idx",
            ),
        ]

        permissions = [
            (
                "manage_partysource",
                "Can manage CRM party sources",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.party.display_name}: {self.reference_label}"

    @property
    def reference_label(self) -> str:
        return (
            self.seller_name
            or self.external_id
            or self.market_name
            or self.platform_name
            or self.referrer_name
            or self.get_source_type_display()
        )

    def clean(self):
        super().clean()

        if not any(
            [
                self.platform_name,
                self.seller_name,
                self.external_id,
                self.profile_url,
                self.listing_url,
                self.market_name,
                self.location_details,
                self.referrer_name,
                self.notes,
            ]
        ):
            raise ValidationError("At least one traceable source detail is required.")

    def save(self, *args, **kwargs):
        for field_name in (
            "platform_name",
            "seller_name",
            "external_id",
            "market_name",
            "location_details",
            "referrer_name",
            "notes",
        ):
            setattr(
                self,
                field_name,
                normalize_text(
                    getattr(self, field_name),
                ),
            )

        if self.is_primary:
            self.is_active = True

        self.full_clean()

        return super().save(*args, **kwargs)


class PartyNote(UUIDTimeStampedModel):
    """
    Store an internal note connected to a CRM party.

    Accepts:
        A party, note type, content, confidentiality flag and optional author.

    Returns:
        A persisted PartyNote instance through Django's ORM.
    """

    class NoteType(models.TextChoices):
        """Define the supported categories for internal CRM notes."""

        GENERAL = "GENERAL", "General"
        PROCUREMENT = "PROCUREMENT", "Procurement"
        ACCOUNTS = "ACCOUNTS", "Accounts"
        RISK = "RISK", "Risk"
        CONFIDENTIAL = "CONFIDENTIAL", "Confidential"

    party = models.ForeignKey(
        Party,
        on_delete=models.CASCADE,
        related_name="notes",
    )

    note_type = models.CharField(
        max_length=20,
        choices=NoteType.choices,
        default=NoteType.GENERAL,
        db_index=True,
    )

    content = models.TextField()

    is_confidential = models.BooleanField(
        default=False,
        db_index=True,
    )

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_notes_authored",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = [
            "-created_at",
        ]

        indexes = [
            models.Index(
                fields=[
                    "party",
                    "-created_at",
                ],
                name="crm_note_party_created_idx",
            ),
        ]

        permissions = [
            (
                "view_confidentialnote",
                "Can view confidential CRM notes",
            ),
            (
                "manage_confidentialnote",
                "Can manage confidential CRM notes",
            ),
        ]

    def __str__(self) -> str:
        """
        Return a readable description of the note.

        Accepts:
            No arguments beyond the model instance.

        Returns:
            A string containing the party name and note category.
        """

        return f"{self.party.display_name} — {self.get_note_type_display()}"

    def save(self, *args, **kwargs):
        """
        Normalise and persist the CRM note.

        Accepts:
            Standard Django model save positional and keyword arguments.

        Returns:
            The result returned by Django's model save implementation.
        """

        self.content = normalize_text(self.content)

        if not self.content:
            raise ValidationError(
                {
                    "content": "A note cannot be empty.",
                }
            )

        if self.note_type == self.NoteType.CONFIDENTIAL:
            self.is_confidential = True

        return super().save(*args, **kwargs)


class PartyInteraction(UUIDTimeStampedModel):
    """
    Record communication or another interaction involving a CRM party.

    Accepts:
        A party, interaction type, date, summary, optional contact person,
        optional follow-up date and optional responsible staff member.

    Returns:
        A persisted PartyInteraction instance through Django's ORM.
    """

    class InteractionType(models.TextChoices):
        """Define the supported CRM communication and activity types."""

        CALL = "CALL", "Call"
        EMAIL = "EMAIL", "Email"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        MEETING = "MEETING", "Meeting"
        MARKETPLACE_MESSAGE = (
            "MARKETPLACE_MESSAGE",
            "Marketplace message",
        )
        SITE_VISIT = "SITE_VISIT", "Site visit"
        OTHER = "OTHER", "Other"

    party = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="interactions",
    )

    contact_party = models.ForeignKey(
        Party,
        on_delete=models.SET_NULL,
        related_name="contact_interactions",
        blank=True,
        null=True,
        help_text=(
            "Optional individual CRM party who participated in the interaction."
        ),
    )

    interaction_type = models.CharField(
        max_length=30,
        choices=InteractionType.choices,
        db_index=True,
    )

    occurred_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
    )

    subject = models.CharField(
        max_length=255,
        blank=True,
    )

    summary = models.TextField()

    staff_member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_interactions_recorded",
        blank=True,
        null=True,
    )

    follow_up_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    class Meta:
        ordering = [
            "-occurred_at",
            "-created_at",
        ]

        indexes = [
            models.Index(
                fields=[
                    "party",
                    "-occurred_at",
                ],
                name="crm_interaction_party_idx",
            ),
        ]

    def __str__(self) -> str:
        """
        Return a readable description of the interaction.

        Accepts:
            No arguments beyond the model instance.

        Returns:
            A string containing the interaction type and party name.
        """

        return f"{self.get_interaction_type_display()} — {self.party.display_name}"

    def clean(self):
        """
        Validate the selected contact and interaction dates.

        Accepts:
            The current PartyInteraction instance.

        Returns:
            None. Raises ValidationError when the interaction is invalid.
        """

        super().clean()

        if (
            self.contact_party_id
            and self.contact_party.entity_kind != Party.EntityKind.INDIVIDUAL
        ):
            raise ValidationError(
                {
                    "contact_party": (
                        "The interaction contact must be an individual CRM party."
                    )
                }
            )

        if (
            self.follow_up_at
            and self.occurred_at
            and self.follow_up_at < self.occurred_at
        ):
            raise ValidationError(
                {
                    "follow_up_at": (
                        "The follow-up date cannot be before the interaction date."
                    )
                }
            )

    def save(self, *args, **kwargs):
        """
        Normalise, validate and persist the interaction.

        Accepts:
            Standard Django model save positional and keyword arguments.

        Returns:
            The result returned by Django's model save implementation.
        """

        self.subject = normalize_text(self.subject)
        self.summary = normalize_text(self.summary)

        if not self.summary:
            raise ValidationError(
                {
                    "summary": "An interaction summary is required.",
                }
            )

        self.full_clean()

        return super().save(*args, **kwargs)


class PartyStatusHistory(UUIDTimeStampedModel):
    """
    Preserve a permanent history of important CRM party status changes.

    Accepts:
        A party, previous status, new status, reason, actor and metadata.

    Returns:
        A persisted PartyStatusHistory instance through Django's ORM.
    """

    party = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="status_history",
    )

    previous_status = models.CharField(
        max_length=20,
        choices=Party.Status.choices,
        blank=True,
    )

    new_status = models.CharField(
        max_length=20,
        choices=Party.Status.choices,
    )

    reason = models.TextField()

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_status_changes",
        blank=True,
        null=True,
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    class Meta:
        ordering = [
            "-created_at",
        ]

        indexes = [
            models.Index(
                fields=[
                    "party",
                    "-created_at",
                ],
                name="crm_status_party_created_idx",
            ),
        ]

    def __str__(self) -> str:
        """
        Return a readable description of the status transition.

        Accepts:
            No arguments beyond the model instance.

        Returns:
            A string containing the party and status transition.
        """

        return (
            f"{self.party.display_name}: "
            f"{self.previous_status or 'NEW'} → "
            f"{self.new_status}"
        )

    def save(self, *args, **kwargs):
        """
        Normalise and persist the status-history reason.

        Accepts:
            Standard Django model save positional and keyword arguments.

        Returns:
            The result returned by Django's model save implementation.
        """

        self.reason = normalize_text(self.reason)

        if not self.reason:
            raise ValidationError(
                {
                    "reason": "A reason for the status change is required.",
                }
            )

        return super().save(*args, **kwargs)


class PartyMergeRecord(UUIDTimeStampedModel):
    """
    Preserve the permanent record of one CRM party being merged into another.

    Accepts:
        A source party, surviving target party, reason, actor and merge summary.

    Returns:
        A persisted PartyMergeRecord instance through Django's ORM.
    """

    source_party = models.OneToOneField(
        Party,
        on_delete=models.PROTECT,
        related_name="merge_record_as_source",
    )

    target_party = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="merge_records_as_target",
    )

    reason = models.TextField()

    merged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_merges_performed",
        blank=True,
        null=True,
    )

    summary = models.JSONField(
        default=dict,
        blank=True,
    )

    class Meta:
        ordering = [
            "-created_at",
        ]

        indexes = [
            models.Index(
                fields=[
                    "target_party",
                    "-created_at",
                ],
                name="crm_merge_target_created_idx",
            ),
        ]

        constraints = [
            models.CheckConstraint(
                condition=~Q(
                    source_party=F("target_party"),
                ),
                name="crm_merge_distinct_parties",
            ),
        ]

    def __str__(self) -> str:
        """
        Return a readable description of the merge.

        Accepts:
            No arguments beyond the model instance.

        Returns:
            A string identifying the source and surviving parties.
        """

        return f"{self.source_party.display_name} → {self.target_party.display_name}"

    def clean(self):
        """
        Validate that the source and target parties differ.

        Accepts:
            The current PartyMergeRecord instance.

        Returns:
            None. Raises ValidationError when both parties are identical.
        """

        super().clean()

        if (
            self.source_party_id
            and self.target_party_id
            and self.source_party_id == self.target_party_id
        ):
            raise ValidationError(
                {"target_party": ("A party cannot be merged into itself.")}
            )

    def save(self, *args, **kwargs):
        """
        Normalise, validate and persist the merge record.

        Accepts:
            Standard Django model save positional and keyword arguments.

        Returns:
            The result returned by Django's model save implementation.
        """

        self.reason = normalize_text(self.reason)

        if not self.reason:
            raise ValidationError(
                {
                    "reason": "A reason for the merge is required.",
                }
            )

        self.full_clean()

        return super().save(*args, **kwargs)


class PartyIdentifier(UUIDTimeStampedModel):
    """Store an encrypted official or commercial identifier for a party."""

    class IdentifierType(models.TextChoices):
        """Supported CRM party identifier categories."""

        COMPANY_REGISTRATION = (
            "COMPANY_REGISTRATION",
            "Company registration number",
        )
        TAX_ID = "TAX_ID", "Tax identification number"
        VAT = "VAT", "VAT number"
        IMPORT_EXPORT = (
            "IMPORT_EXPORT",
            "Import/export number",
        )
        MARKETPLACE_SELLER = (
            "MARKETPLACE_SELLER",
            "Marketplace seller ID",
        )
        OTHER = "OTHER", "Other"

    party = models.ForeignKey(
        Party,
        on_delete=models.CASCADE,
        related_name="identifiers",
    )

    identifier_type = models.CharField(
        max_length=30,
        choices=IdentifierType.choices,
        db_index=True,
    )

    label = models.CharField(
        max_length=150,
        blank=True,
    )

    encrypted_value = models.TextField(
        editable=False,
    )

    value_hash = models.CharField(
        max_length=64,
        editable=False,
        db_index=True,
    )

    value_last_four = models.CharField(
        max_length=4,
        blank=True,
        editable=False,
    )

    issuing_country = models.CharField(
        max_length=2,
        blank=True,
    )

    issue_date = models.DateField(
        blank=True,
        null=True,
    )

    expiry_date = models.DateField(
        blank=True,
        null=True,
    )

    is_verified = models.BooleanField(
        default=False,
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_identifiers_created",
        blank=True,
        null=True,
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_identifiers_updated",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = [
            "party__display_name",
            "identifier_type",
            "label",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "party",
                    "identifier_type",
                    "value_hash",
                ],
                name="crm_unique_party_identifier",
            ),
            models.CheckConstraint(
                condition=(
                    Q(expiry_date__isnull=True)
                    | Q(issue_date__isnull=True)
                    | Q(expiry_date__gte=F("issue_date"))
                ),
                name="crm_identifier_dates_valid",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "identifier_type",
                    "value_hash",
                ],
                name="crm_identifier_lookup_idx",
            ),
        ]

        permissions = [
            (
                "view_sensitive_partyidentifier",
                "Can reveal sensitive CRM party identifiers",
            ),
            (
                "manage_sensitive_partyidentifier",
                "Can manage sensitive CRM party identifiers",
            ),
        ]

    def __str__(self) -> str:
        """
        Return a safe identifier description.

        Returns:
            str: Party, identifier type and masked suffix.
        """

        return (
            f"{self.party.display_name} — "
            f"{self.get_identifier_type_display()} "
            f"{self.masked_value}"
        ).strip()

    @property
    def masked_value(self) -> str:
        """
        Return the masked identifier value.

        Returns:
            str: Masked identifier suitable for ordinary API responses.
        """

        return mask_sensitive_value(
            self.value_last_four,
        )

    def set_value(self, value: str) -> None:
        """
        Encrypt and index an identifier value.

        Args:
            value: Plaintext identifier supplied by an authorised user.

        Returns:
            None.

        Raises:
            ValueError: If the value is empty.
        """

        self.encrypted_value = encrypt_sensitive_value(
            value,
        )
        self.value_hash = hash_sensitive_value(
            value,
        )
        self.value_last_four = sensitive_value_last_four(
            value,
        )

    def reveal_value(self) -> str:
        """
        Decrypt the identifier for an explicitly authorised request.

        Returns:
            str: Plaintext identifier.

        Raises:
            ValueError: If the encrypted value is invalid.
        """

        return decrypt_sensitive_value(
            self.encrypted_value,
        )

    def clean(self) -> None:
        """
        Validate identifier dates and encrypted state.

        Returns:
            None.

        Raises:
            ValidationError: If required encrypted data or dates are invalid.
        """

        super().clean()

        if not self.encrypted_value or not self.value_hash:
            raise ValidationError(
                {"encrypted_value": ("Use set_value() before saving an identifier.")}
            )

        if self.issue_date and self.expiry_date and self.expiry_date < self.issue_date:
            raise ValidationError(
                {"expiry_date": ("Expiry date cannot be before issue date.")}
            )

    def save(self, *args, **kwargs):
        """
        Normalise metadata and persist the identifier.

        Args:
            *args: Positional arguments accepted by Django model save.
            **kwargs: Keyword arguments accepted by Django model save.

        Returns:
            None.
        """

        self.label = normalize_text(self.label)
        self.issuing_country = (self.issuing_country or "").upper()

        self.full_clean()

        return super().save(*args, **kwargs)


class PartyBankAccount(UUIDTimeStampedModel):
    """Store encrypted bank or payment-account information for a party."""

    class PaymentMethod(models.TextChoices):
        """Supported payment-account categories."""

        BANK_TRANSFER = "BANK_TRANSFER", "Bank transfer"
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile money"
        OTHER = "OTHER", "Other"

    class VerificationStatus(models.TextChoices):
        """Supported payment-detail verification states."""

        UNVERIFIED = "UNVERIFIED", "Unverified"
        PENDING = "PENDING", "Pending verification"
        VERIFIED = "VERIFIED", "Verified"
        REJECTED = "REJECTED", "Rejected"

    party = models.ForeignKey(
        Party,
        on_delete=models.CASCADE,
        related_name="bank_accounts",
    )

    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.BANK_TRANSFER,
    )

    account_name = models.CharField(
        max_length=255,
    )

    bank_name = models.CharField(
        max_length=255,
        blank=True,
    )

    provider_name = models.CharField(
        max_length=255,
        blank=True,
    )

    encrypted_account_number = models.TextField(
        blank=True,
        editable=False,
    )

    account_number_hash = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
        db_index=True,
    )

    account_number_last_four = models.CharField(
        max_length=4,
        blank=True,
        editable=False,
    )

    encrypted_iban = models.TextField(
        blank=True,
        editable=False,
    )

    iban_hash = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
        db_index=True,
    )

    iban_last_four = models.CharField(
        max_length=4,
        blank=True,
        editable=False,
    )

    swift_bic = models.CharField(
        max_length=20,
        blank=True,
    )

    currency = models.CharField(
        max_length=3,
        blank=True,
    )

    country_code = models.CharField(
        max_length=2,
        blank=True,
    )

    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
        db_index=True,
    )

    is_primary = models.BooleanField(
        default=False,
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    notes = models.TextField(
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_bank_accounts_created",
        blank=True,
        null=True,
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_bank_accounts_updated",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = [
            "party__display_name",
            "-is_primary",
            "account_name",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "party",
                ],
                condition=Q(
                    is_primary=True,
                    is_active=True,
                ),
                name="crm_one_primary_bank_account",
            ),
            models.UniqueConstraint(
                fields=[
                    "party",
                    "account_number_hash",
                ],
                condition=~Q(
                    account_number_hash="",
                ),
                name="crm_unique_party_account_number",
            ),
            models.UniqueConstraint(
                fields=[
                    "party",
                    "iban_hash",
                ],
                condition=~Q(
                    iban_hash="",
                ),
                name="crm_unique_party_iban",
            ),
        ]

        permissions = [
            (
                "view_sensitive_partybankaccount",
                "Can reveal sensitive CRM bank details",
            ),
            (
                "manage_sensitive_partybankaccount",
                "Can manage sensitive CRM bank details",
            ),
        ]

    def __str__(self) -> str:
        """
        Return a safe payment-account description.

        Returns:
            str: Party, bank/provider and masked account suffix.
        """

        provider = (
            self.bank_name or self.provider_name or self.get_payment_method_display()
        )

        return (
            f"{self.party.display_name} — {provider} {self.masked_account_number}"
        ).strip()

    @property
    def masked_account_number(self) -> str:
        """
        Return the masked account number.

        Returns:
            str: Safe account-number display value.
        """

        return mask_sensitive_value(
            self.account_number_last_four,
        )

    @property
    def masked_iban(self) -> str:
        """
        Return the masked IBAN.

        Returns:
            str: Safe IBAN display value.
        """

        return mask_sensitive_value(
            self.iban_last_four,
        )

    def set_account_number(
        self,
        value: str,
    ) -> None:
        """
        Encrypt and index an account number.

        Args:
            value: Plaintext bank, mobile-money or payment account number.

        Returns:
            None.

        Raises:
            ValueError: If the account number is empty.
        """

        self.encrypted_account_number = encrypt_sensitive_value(value)
        self.account_number_hash = hash_sensitive_value(value)
        self.account_number_last_four = sensitive_value_last_four(value)

    def set_iban(
        self,
        value: str,
    ) -> None:
        """
        Encrypt and index an IBAN.

        Args:
            value: Plaintext IBAN.

        Returns:
            None.

        Raises:
            ValueError: If the IBAN is empty.
        """

        self.encrypted_iban = encrypt_sensitive_value(
            value,
        )
        self.iban_hash = hash_sensitive_value(
            value,
        )
        self.iban_last_four = sensitive_value_last_four(
            value,
        )

    def reveal_account_number(self) -> str:
        """
        Decrypt the account number.

        Returns:
            str: Plaintext account number, or an empty string when absent.

        Raises:
            ValueError: If stored encrypted data is invalid.
        """

        if not self.encrypted_account_number:
            return ""

        return decrypt_sensitive_value(
            self.encrypted_account_number,
        )

    def reveal_iban(self) -> str:
        """
        Decrypt the IBAN.

        Returns:
            str: Plaintext IBAN, or an empty string when absent.

        Raises:
            ValueError: If stored encrypted data is invalid.
        """

        if not self.encrypted_iban:
            return ""

        return decrypt_sensitive_value(
            self.encrypted_iban,
        )

    def clean(self) -> None:
        """
        Validate that the payment account has an encrypted identifier.

        Returns:
            None.

        Raises:
            ValidationError: If neither an account number nor IBAN exists.
        """

        super().clean()

        if not (self.encrypted_account_number or self.encrypted_iban):
            raise ValidationError(
                {"encrypted_account_number": ("An account number or IBAN is required.")}
            )

    def save(self, *args, **kwargs):
        """
        Normalise and persist payment-account metadata.

        Args:
            *args: Positional arguments accepted by Django model save.
            **kwargs: Keyword arguments accepted by Django model save.

        Returns:
            None.
        """

        self.account_name = normalize_text(
            self.account_name,
        )
        self.bank_name = normalize_text(
            self.bank_name,
        )
        self.provider_name = normalize_text(
            self.provider_name,
        )
        self.swift_bic = normalize_text(
            self.swift_bic,
        ).upper()
        self.currency = (self.currency or "").upper()
        self.country_code = (self.country_code or "").upper()
        self.notes = normalize_text(
            self.notes,
        )

        if self.is_primary:
            self.is_active = True

        self.full_clean()

        return super().save(*args, **kwargs)


class PartyDocument(UUIDTimeStampedModel):
    """Store metadata and remote-storage references for a CRM document."""

    class Category(models.TextChoices):
        """Supported CRM document categories."""

        REGISTRATION = "REGISTRATION", "Registration"
        TAX = "TAX", "Tax"
        BANK = "BANK", "Bank/payment"
        CONTRACT = "CONTRACT", "Contract"
        CORRESPONDENCE = (
            "CORRESPONDENCE",
            "Correspondence",
        )
        IDENTITY = "IDENTITY", "Identity"
        QUOTE = "QUOTE", "Quote/supporting record"
        OTHER = "OTHER", "Other"

    class StorageProvider(models.TextChoices):
        """Supported external document-storage providers."""

        GOOGLE_DRIVE = "GOOGLE_DRIVE", "Google Drive"
        SUPABASE = "SUPABASE", "Supabase Storage"

    class VerificationStatus(models.TextChoices):
        """Supported document verification states."""

        UNVERIFIED = "UNVERIFIED", "Unverified"
        PENDING = "PENDING", "Pending verification"
        VERIFIED = "VERIFIED", "Verified"
        REJECTED = "REJECTED", "Rejected"

    party = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="documents",
    )

    category = models.CharField(
        max_length=30,
        choices=Category.choices,
        default=Category.OTHER,
        db_index=True,
    )

    original_filename = models.CharField(
        max_length=255,
    )

    mime_type = models.CharField(
        max_length=255,
    )

    size_bytes = models.PositiveBigIntegerField()

    checksum_sha256 = models.CharField(
        max_length=64,
        db_index=True,
    )

    storage_provider = models.CharField(
        max_length=20,
        choices=StorageProvider.choices,
    )

    external_file_id = models.CharField(
        max_length=1000,
    )

    external_folder_id = models.CharField(
        max_length=1000,
        blank=True,
    )

    storage_path = models.CharField(
        max_length=1500,
        blank=True,
    )

    description = models.TextField(
        blank=True,
    )

    is_confidential = models.BooleanField(
        default=False,
        db_index=True,
    )

    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )

    expires_at = models.DateField(
        blank=True,
        null=True,
        db_index=True,
    )

    expiry_notification_key = models.CharField(
        max_length=255,
        blank=True,
        editable=False,
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_documents_uploaded",
        blank=True,
        null=True,
    )

    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="crm_documents_deleted",
        blank=True,
        null=True,
    )

    deleted_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    class Meta:
        ordering = [
            "-created_at",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "party",
                    "category",
                    "checksum_sha256",
                ],
                condition=Q(
                    is_active=True,
                ),
                name="crm_unique_active_document",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "party",
                    "category",
                    "is_active",
                ],
                name="crm_document_party_idx",
            ),
            models.Index(
                fields=[
                    "expires_at",
                    "is_active",
                ],
                name="crm_document_expiry_idx",
            ),
        ]

        permissions = [
            (
                "download_partydocument",
                "Can download CRM documents",
            ),
            (
                "view_confidential_partydocument",
                "Can view confidential CRM documents",
            ),
            (
                "manage_confidential_partydocument",
                "Can manage confidential CRM documents",
            ),
        ]

    def __str__(self) -> str:
        """
        Return a readable document description.

        Returns:
            str: Party name and original filename.
        """

        return f"{self.party.display_name} — {self.original_filename}"

    def save(self, *args, **kwargs):
        """
        Normalise and persist document metadata.

        Args:
            *args: Positional arguments accepted by Django model save.
            **kwargs: Keyword arguments accepted by Django model save.

        Returns:
            None.
        """

        self.original_filename = normalize_text(
            self.original_filename,
        )
        self.mime_type = normalize_text(
            self.mime_type,
        ).lower()
        self.description = normalize_text(
            self.description,
        )

        return super().save(*args, **kwargs)
