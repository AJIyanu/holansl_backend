import re
import unicodedata
import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


DEFAULT_CONTACT_ROLES = [
    ("general", "General", 10),
    ("procurement", "Procurement", 20),
    ("accounts", "Accounts", 30),
    ("technical", "Technical", 40),
    ("delivery", "Delivery", 50),
    ("management", "Management", 60),
    ("sales", "Sales", 70),
    ("other", "Other", 100),
]


def normalize_text(value):
    if not value:
        return ""

    normalized = unicodedata.normalize(
        "NFKC",
        str(value),
    )

    return re.sub(
        r"\s+",
        " ",
        normalized,
    ).strip()


def normalize_phone(value):
    value = normalize_text(value)
    cleaned = re.sub(
        r"[^0-9+]",
        "",
        value,
    )

    if cleaned.count("+") > 1 or ("+" in cleaned and not cleaned.startswith("+")):
        cleaned = cleaned.replace("+", "")

    return cleaned


def migrate_legacy_crm_forward(
    apps,
    schema_editor,
):
    database = schema_editor.connection.alias

    Party = apps.get_model(
        "crm",
        "Party",
    )
    PartyRole = apps.get_model(
        "crm",
        "PartyRole",
    )
    OrganisationProfile = apps.get_model(
        "crm",
        "OrganisationProfile",
    )
    PersonProfile = apps.get_model(
        "crm",
        "PersonProfile",
    )
    ContactMethod = apps.get_model(
        "crm",
        "ContactMethod",
    )
    Address = apps.get_model(
        "crm",
        "Address",
    )
    ContactRole = apps.get_model(
        "crm",
        "ContactRole",
    )

    for slug, name, sort_order in DEFAULT_CONTACT_ROLES:
        ContactRole.objects.using(database).get_or_create(
            slug=slug,
            defaults={
                "name": name,
                "sort_order": sort_order,
                "is_active": True,
            },
        )

    role_map = {
        "client": "CLIENT",
        "supplier": "SUPPLIER",
        "logistics": "LOGISTICS_PROVIDER",
    }

    parties = Party.objects.using(database).all().iterator()

    for party in parties:
        display_name = normalize_text(party.name) or "Unnamed party"

        entity_kind = "ORGANISATION" if party.is_organization else "INDIVIDUAL"

        verification_level = (
            "BASIC"
            if any(
                [
                    party.email,
                    party.phone,
                    party.address,
                ]
            )
            else "MINIMAL"
        )

        party.display_name = display_name
        party.normalized_name = display_name.casefold()
        party.entity_kind = entity_kind
        party.status = "ACTIVE"
        party.verification_level = verification_level

        party.save(
            using=database,
            update_fields=[
                "display_name",
                "normalized_name",
                "entity_kind",
                "status",
                "verification_level",
            ],
        )

        mapped_role = role_map.get(party.party_type)

        if mapped_role:
            PartyRole.objects.using(database).get_or_create(
                party_id=party.id,
                role=mapped_role,
                defaults={
                    "is_active": True,
                },
            )

        if entity_kind == "ORGANISATION":
            OrganisationProfile.objects.using(database).get_or_create(
                party_id=party.id,
                defaults={
                    "legal_name": display_name,
                },
            )
        else:
            PersonProfile.objects.using(database).get_or_create(
                party_id=party.id,
                defaults={
                    "preferred_name": display_name,
                },
            )

        if party.email:
            email = normalize_text(party.email)

            ContactMethod.objects.using(database).get_or_create(
                party_id=party.id,
                method_type="EMAIL",
                normalized_value=email.casefold(),
                defaults={
                    "value": email,
                    "label": "Migrated email",
                    "is_primary": True,
                    "is_verified": False,
                    "is_active": True,
                },
            )

        if party.phone:
            phone = normalize_text(party.phone)

            ContactMethod.objects.using(database).get_or_create(
                party_id=party.id,
                method_type="PHONE",
                normalized_value=normalize_phone(phone),
                defaults={
                    "value": phone,
                    "label": "Migrated phone",
                    "is_primary": True,
                    "is_verified": False,
                    "is_active": True,
                },
            )

        if party.address:
            Address.objects.using(database).get_or_create(
                party_id=party.id,
                address_type="OTHER",
                is_primary=True,
                defaults={
                    "label": "Migrated address",
                    "location_notes": normalize_text(party.address),
                    "is_active": True,
                },
            )


def migrate_legacy_crm_backward(
    apps,
    schema_editor,
):
    database = schema_editor.connection.alias

    Party = apps.get_model(
        "crm",
        "Party",
    )
    PartyRole = apps.get_model(
        "crm",
        "PartyRole",
    )
    ContactMethod = apps.get_model(
        "crm",
        "ContactMethod",
    )
    Address = apps.get_model(
        "crm",
        "Address",
    )

    reverse_role_map = {
        "CLIENT": "client",
        "SUPPLIER": "supplier",
        "LOGISTICS_PROVIDER": "logistics",
    }

    parties = Party.objects.using(database).all().iterator()

    for party in parties:
        active_roles = list(
            PartyRole.objects.using(database)
            .filter(
                party_id=party.id,
                is_active=True,
                role__in=reverse_role_map,
            )
            .values_list(
                "role",
                flat=True,
            )
        )

        party.name = party.display_name

        party.party_type = (
            reverse_role_map[active_roles[0]] if len(active_roles) == 1 else "client"
        )

        party.is_organization = party.entity_kind != "INDIVIDUAL"

        email = (
            ContactMethod.objects.using(database)
            .filter(
                party_id=party.id,
                method_type="EMAIL",
                is_active=True,
            )
            .order_by(
                "-is_primary",
                "created_at",
            )
            .first()
        )

        phone = (
            ContactMethod.objects.using(database)
            .filter(
                party_id=party.id,
                method_type__in=[
                    "PHONE",
                    "MOBILE",
                    "WHATSAPP",
                ],
                is_active=True,
            )
            .order_by(
                "-is_primary",
                "created_at",
            )
            .first()
        )

        address = (
            Address.objects.using(database)
            .filter(
                party_id=party.id,
                is_active=True,
            )
            .order_by(
                "-is_primary",
                "created_at",
            )
            .first()
        )

        party.email = email.value if email else None

        party.phone = phone.value if phone else None

        if address:
            party.address = (
                address.line_1 or address.location_notes or address.city or None
            )
        else:
            party.address = None

        party.save(
            using=database,
            update_fields=[
                "name",
                "party_type",
                "is_organization",
                "email",
                "phone",
                "address",
            ],
        )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        (
            "crm",
            "0001_initial",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="party",
            name="archived_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="party",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=(django.db.models.deletion.SET_NULL),
                related_name="crm_parties_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="party",
            name="display_name",
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="party",
            name="entity_kind",
            field=models.CharField(
                choices=[
                    (
                        "ORGANISATION",
                        "Organisation",
                    ),
                    (
                        "INDIVIDUAL",
                        "Individual",
                    ),
                    (
                        "TRADING_NAME",
                        "Trading name / informal business",
                    ),
                ],
                db_index=True,
                default="ORGANISATION",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="party",
            name="is_archived",
            field=models.BooleanField(
                db_index=True,
                default=False,
            ),
        ),
        migrations.AddField(
            model_name="party",
            name="merged_into",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=(django.db.models.deletion.PROTECT),
                related_name="merged_records",
                to="crm.party",
            ),
        ),
        migrations.AddField(
            model_name="party",
            name="normalized_name",
            field=models.CharField(
                db_index=True,
                default="",
                editable=False,
                max_length=255,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="party",
            name="status",
            field=models.CharField(
                choices=[
                    ("ACTIVE", "Active"),
                    ("INACTIVE", "Inactive"),
                    ("SUSPENDED", "Suspended"),
                    ("BLOCKED", "Blocked"),
                    ("MERGED", "Merged"),
                ],
                db_index=True,
                default="ACTIVE",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="party",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=(django.db.models.deletion.SET_NULL),
                related_name="crm_parties_updated",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="party",
            name="verification_level",
            field=models.CharField(
                choices=[
                    ("MINIMAL", "Minimal"),
                    ("BASIC", "Basic"),
                    ("VERIFIED", "Verified"),
                ],
                default="MINIMAL",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="party",
            name="name",
            field=models.CharField(
                blank=True,
                default="",
                help_text=("Deprecated compatibility value; use display_name."),
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="party",
            name="party_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("client", "Client"),
                    ("supplier", "Supplier"),
                    ("logistics", "Logistics"),
                ],
                help_text=(
                    "Deprecated compatibility value; use active PartyRole rows."
                ),
                max_length=20,
                null=True,
            ),
        ),
        migrations.AlterModelOptions(
            name="contactperson",
            options={
                "verbose_name": ("Legacy contact person"),
                "verbose_name_plural": ("Legacy contact people"),
            },
        ),
        migrations.AlterModelOptions(
            name="party",
            options={
                "ordering": [
                    "display_name",
                    "id",
                ],
                "permissions": [
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
                ],
            },
        ),
        migrations.CreateModel(
            name="ContactRole",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        max_length=100,
                        unique=True,
                    ),
                ),
                (
                    "slug",
                    models.SlugField(
                        blank=True,
                        max_length=120,
                        unique=True,
                    ),
                ),
                (
                    "description",
                    models.TextField(
                        blank=True,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                    ),
                ),
                (
                    "sort_order",
                    models.PositiveSmallIntegerField(
                        default=0,
                    ),
                ),
            ],
            options={
                "ordering": [
                    "sort_order",
                    "name",
                ],
            },
        ),
        migrations.CreateModel(
            name="OrganisationProfile",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "legal_name",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "trading_name",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "website",
                    models.URLField(
                        blank=True,
                        max_length=500,
                    ),
                ),
                (
                    "industry",
                    models.CharField(
                        blank=True,
                        max_length=150,
                    ),
                ),
                (
                    "business_description",
                    models.TextField(
                        blank=True,
                    ),
                ),
                (
                    "registration_country",
                    models.CharField(
                        blank=True,
                        max_length=2,
                    ),
                ),
                (
                    "incorporation_date",
                    models.DateField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "party",
                    models.OneToOneField(
                        on_delete=(django.db.models.deletion.CASCADE),
                        related_name="organisation_profile",
                        to="crm.party",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "party__display_name",
                ],
            },
        ),
        migrations.CreateModel(
            name="PersonProfile",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        blank=True,
                        max_length=30,
                    ),
                ),
                (
                    "first_name",
                    models.CharField(
                        blank=True,
                        max_length=100,
                    ),
                ),
                (
                    "middle_name",
                    models.CharField(
                        blank=True,
                        max_length=100,
                    ),
                ),
                (
                    "last_name",
                    models.CharField(
                        blank=True,
                        max_length=100,
                    ),
                ),
                (
                    "preferred_name",
                    models.CharField(
                        blank=True,
                        max_length=150,
                    ),
                ),
                (
                    "party",
                    models.OneToOneField(
                        on_delete=(django.db.models.deletion.CASCADE),
                        related_name="person_profile",
                        to="crm.party",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "party__display_name",
                ],
            },
        ),
        migrations.CreateModel(
            name="PartyRole",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("CLIENT", "Client"),
                            ("SUPPLIER", "Supplier"),
                            ("PROSPECT", "Prospect"),
                            (
                                "LOGISTICS_PROVIDER",
                                "Logistics provider",
                            ),
                            (
                                "SERVICE_PROVIDER",
                                "Service provider",
                            ),
                            ("OTHER", "Other"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                    ),
                ),
                (
                    "activated_at",
                    models.DateTimeField(
                        default=(django.utils.timezone.now),
                    ),
                ),
                (
                    "deactivated_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "notes",
                    models.TextField(
                        blank=True,
                    ),
                ),
                (
                    "party",
                    models.ForeignKey(
                        on_delete=(django.db.models.deletion.CASCADE),
                        related_name="roles",
                        to="crm.party",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "party__display_name",
                    "role",
                ],
                "indexes": [
                    models.Index(
                        fields=[
                            "role",
                            "is_active",
                        ],
                        name="crm_role_active_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "party",
                            "role",
                        ),
                        name="crm_unique_party_role",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="PartyAffiliation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "job_title",
                    models.CharField(
                        blank=True,
                        max_length=150,
                    ),
                ),
                (
                    "department",
                    models.CharField(
                        blank=True,
                        max_length=150,
                    ),
                ),
                (
                    "start_date",
                    models.DateField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "end_date",
                    models.DateField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "is_current",
                    models.BooleanField(
                        default=True,
                    ),
                ),
                (
                    "is_primary_contact",
                    models.BooleanField(
                        default=False,
                    ),
                ),
                (
                    "notes",
                    models.TextField(
                        blank=True,
                    ),
                ),
                (
                    "organisation",
                    models.ForeignKey(
                        on_delete=(django.db.models.deletion.PROTECT),
                        related_name="people_affiliations",
                        to="crm.party",
                    ),
                ),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=(django.db.models.deletion.PROTECT),
                        related_name=("organisation_affiliations"),
                        to="crm.party",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "organisation__display_name",
                    "-is_current",
                    "person__display_name",
                ],
                "permissions": [
                    (
                        "end_partyaffiliation",
                        "Can end CRM party affiliation",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=~models.Q(person=models.F("organisation")),
                        name=("crm_affiliation_distinct"),
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(
                            is_current=True,
                        ),
                        fields=(
                            "person",
                            "organisation",
                        ),
                        name=("crm_unique_current_affiliation"),
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="AffiliationContactRole",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "is_primary",
                    models.BooleanField(
                        default=False,
                    ),
                ),
                (
                    "affiliation",
                    models.ForeignKey(
                        on_delete=(django.db.models.deletion.CASCADE),
                        related_name="role_assignments",
                        to="crm.partyaffiliation",
                    ),
                ),
                (
                    "contact_role",
                    models.ForeignKey(
                        on_delete=(django.db.models.deletion.PROTECT),
                        related_name=("affiliation_assignments"),
                        to="crm.contactrole",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "contact_role__sort_order",
                    "contact_role__name",
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "affiliation",
                            "contact_role",
                        ),
                        name=("crm_unique_affiliation_role"),
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="partyaffiliation",
            name="contact_roles",
            field=models.ManyToManyField(
                blank=True,
                related_name="affiliations",
                through=("crm.AffiliationContactRole"),
                to="crm.contactrole",
            ),
        ),
        migrations.CreateModel(
            name="ContactMethod",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "method_type",
                    models.CharField(
                        choices=[
                            ("EMAIL", "Email"),
                            ("PHONE", "Telephone"),
                            ("MOBILE", "Mobile"),
                            ("WHATSAPP", "WhatsApp"),
                            ("WEBSITE", "Website"),
                            (
                                "SOCIAL_MEDIA",
                                "Social media",
                            ),
                            (
                                "MARKETPLACE",
                                "Marketplace account",
                            ),
                            ("OTHER", "Other"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "value",
                    models.CharField(
                        max_length=500,
                    ),
                ),
                (
                    "normalized_value",
                    models.CharField(
                        db_index=True,
                        editable=False,
                        max_length=500,
                    ),
                ),
                (
                    "label",
                    models.CharField(
                        blank=True,
                        max_length=100,
                    ),
                ),
                (
                    "is_primary",
                    models.BooleanField(
                        default=False,
                    ),
                ),
                (
                    "is_verified",
                    models.BooleanField(
                        default=False,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                    ),
                ),
                (
                    "party",
                    models.ForeignKey(
                        on_delete=(django.db.models.deletion.CASCADE),
                        related_name="contact_methods",
                        to="crm.party",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "party__display_name",
                    "method_type",
                    "-is_primary",
                    "value",
                ],
                "permissions": [
                    (
                        "manage_contactmethod",
                        "Can manage CRM contact methods",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=[
                            "method_type",
                            "normalized_value",
                        ],
                        name="crm_contact_lookup_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "party",
                            "method_type",
                            "normalized_value",
                        ),
                        name=("crm_unique_contact_value"),
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(
                            is_active=True,
                            is_primary=True,
                        ),
                        fields=(
                            "party",
                            "method_type",
                        ),
                        name=("crm_one_primary_contact_type"),
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Address",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "address_type",
                    models.CharField(
                        choices=[
                            (
                                "REGISTERED",
                                "Registered",
                            ),
                            ("OFFICE", "Office"),
                            ("BILLING", "Billing"),
                            ("DELIVERY", "Delivery"),
                            (
                                "RESIDENTIAL",
                                "Residential",
                            ),
                            (
                                "MARKET",
                                "Market / trading location",
                            ),
                            ("OTHER", "Other"),
                        ],
                        default="OTHER",
                        max_length=20,
                    ),
                ),
                (
                    "label",
                    models.CharField(
                        blank=True,
                        max_length=100,
                    ),
                ),
                (
                    "line_1",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "line_2",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "city",
                    models.CharField(
                        blank=True,
                        max_length=120,
                    ),
                ),
                (
                    "state_region",
                    models.CharField(
                        blank=True,
                        max_length=120,
                    ),
                ),
                (
                    "postal_code",
                    models.CharField(
                        blank=True,
                        max_length=30,
                    ),
                ),
                (
                    "country_code",
                    models.CharField(
                        blank=True,
                        max_length=2,
                    ),
                ),
                (
                    "location_notes",
                    models.TextField(
                        blank=True,
                    ),
                ),
                (
                    "is_primary",
                    models.BooleanField(
                        default=False,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                    ),
                ),
                (
                    "party",
                    models.ForeignKey(
                        on_delete=(django.db.models.deletion.CASCADE),
                        related_name="addresses",
                        to="crm.party",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "party__display_name",
                    "address_type",
                    "-is_primary",
                ],
                "permissions": [
                    (
                        "manage_address",
                        "Can manage CRM addresses",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            is_active=True,
                            is_primary=True,
                        ),
                        fields=(
                            "party",
                            "address_type",
                        ),
                        name=("crm_one_primary_address_type"),
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="PartySource",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "source_type",
                    models.CharField(
                        choices=[
                            (
                                "ONLINE_MARKETPLACE",
                                "Online marketplace",
                            ),
                            (
                                "PHYSICAL_MARKET",
                                "Physical market",
                            ),
                            (
                                "DIRECT_CONTACT",
                                "Direct contact",
                            ),
                            ("REFERRAL", "Referral"),
                            ("WEBSITE", "Website"),
                            (
                                "SOCIAL_MEDIA",
                                "Social media",
                            ),
                            (
                                "PREVIOUS_TRANSACTION",
                                "Previous transaction",
                            ),
                            (
                                "TRADE_DIRECTORY",
                                "Trade directory",
                            ),
                            (
                                "EVENT",
                                "Exhibition / event",
                            ),
                            ("OTHER", "Other"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "platform_name",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "For example Jumia, eBay, Amazon, Konga or AliExpress."
                        ),
                        max_length=120,
                    ),
                ),
                (
                    "seller_name",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "external_id",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "profile_url",
                    models.URLField(
                        blank=True,
                        max_length=1000,
                    ),
                ),
                (
                    "listing_url",
                    models.URLField(
                        blank=True,
                        max_length=1000,
                    ),
                ),
                (
                    "market_name",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "location_details",
                    models.TextField(
                        blank=True,
                    ),
                ),
                (
                    "referrer_name",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "notes",
                    models.TextField(
                        blank=True,
                    ),
                ),
                (
                    "discovered_at",
                    models.DateField(
                        default=(django.utils.timezone.localdate),
                    ),
                ),
                (
                    "last_verified_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "is_primary",
                    models.BooleanField(
                        default=False,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                    ),
                ),
                (
                    "discovered_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=(django.db.models.deletion.SET_NULL),
                        related_name=("crm_sources_discovered"),
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "party",
                    models.ForeignKey(
                        on_delete=(django.db.models.deletion.CASCADE),
                        related_name="sources",
                        to="crm.party",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "party__display_name",
                    "-is_primary",
                    "-discovered_at",
                ],
                "permissions": [
                    (
                        "manage_partysource",
                        "Can manage CRM party sources",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=[
                            "source_type",
                            "platform_name",
                        ],
                        name=("crm_source_platform_idx"),
                    ),
                    models.Index(
                        fields=[
                            "platform_name",
                            "external_id",
                        ],
                        name=("crm_source_external_idx"),
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            is_active=True,
                            is_primary=True,
                        ),
                        fields=("party",),
                        name=("crm_one_primary_source"),
                    ),
                ],
            },
        ),
        migrations.RunPython(
            migrate_legacy_crm_forward,
            migrate_legacy_crm_backward,
        ),
        migrations.AlterField(
            model_name="party",
            name="display_name",
            field=models.CharField(
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="party",
            name="normalized_name",
            field=models.CharField(
                db_index=True,
                editable=False,
                max_length=255,
            ),
        ),
        migrations.RemoveField(
            model_name="party",
            name="address",
        ),
        migrations.RemoveField(
            model_name="party",
            name="email",
        ),
        migrations.RemoveField(
            model_name="party",
            name="is_organization",
        ),
        migrations.RemoveField(
            model_name="party",
            name="phone",
        ),
        migrations.AddIndex(
            model_name="party",
            index=models.Index(
                fields=[
                    "status",
                    "is_archived",
                ],
                name="crm_party_status_arch_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="party",
            index=models.Index(
                fields=[
                    "entity_kind",
                    "display_name",
                ],
                name="crm_party_kind_name_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="party",
            constraint=models.CheckConstraint(
                condition=~models.Q(
                    merged_into=models.F("id"),
                ),
                name="crm_party_not_merged_self",
            ),
        ),
    ]
