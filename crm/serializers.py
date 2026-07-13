"""
CRM API serializers.

This module validates CRM API requests and produces directory, detail,
relationship, history, note and interaction response structures.

Serializer classes accept model instances or submitted request data and
return validated Python values, persisted model instances or JSON-compatible
representations.
"""

from __future__ import annotations

from django.db import transaction
from rest_framework import serializers

from .models import (
    Address,
    AffiliationContactRole,
    ContactMethod,
    ContactRole,
    OrganisationProfile,
    Party,
    PartyAffiliation,
    # existing models...
    PartyBankAccount,
    PartyDocument,
    PartyIdentifier,
    PartyInteraction,
    PartyMergeRecord,
    PartyNote,
    PartyRole,
    PartySource,
    PartyStatusHistory,
    PersonProfile,
)
from .services import (
    build_duplicate_probe_from_party,
    find_party_duplicates,
)


class PartyRoleSerializer(serializers.ModelSerializer):
    """
    Serialise or validate a CRM party role.

    Accepts:
        A PartyRole instance or role-assignment request data.

    Returns:
        A PartyRole representation or validated model instance.
    """

    role_display = serializers.CharField(
        source="get_role_display",
        read_only=True,
    )

    class Meta:
        """Configure PartyRole fields exposed through the API."""

        model = PartyRole
        fields = [
            "id",
            "party",
            "role",
            "role_display",
            "is_active",
            "activated_at",
            "deactivated_at",
            "notes",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "activated_at",
            "deactivated_at",
            "created_at",
            "updated_at",
        ]


class OrganisationProfileInputSerializer(serializers.Serializer):
    """
    Validate organisation-profile data nested inside party creation.

    Accepts:
        Optional organisation identity and business-description fields.

    Returns:
        A validated dictionary suitable for OrganisationProfile creation.
    """

    legal_name = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    trading_name = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    website = serializers.URLField(
        max_length=500,
        required=False,
        allow_blank=True,
    )

    industry = serializers.CharField(
        max_length=150,
        required=False,
        allow_blank=True,
    )

    business_description = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    registration_country = serializers.CharField(
        max_length=2,
        required=False,
        allow_blank=True,
    )

    incorporation_date = serializers.DateField(
        required=False,
        allow_null=True,
    )


class PersonProfileInputSerializer(serializers.Serializer):
    """
    Validate person-profile data nested inside party creation.

    Accepts:
        Optional title, name and preferred-name values.

    Returns:
        A validated dictionary suitable for PersonProfile creation.
    """

    title = serializers.CharField(
        max_length=30,
        required=False,
        allow_blank=True,
    )

    first_name = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
    )

    middle_name = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
    )

    last_name = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
    )

    preferred_name = serializers.CharField(
        max_length=150,
        required=False,
        allow_blank=True,
    )


class ContactMethodInputSerializer(serializers.Serializer):
    """
    Validate contact information nested inside party creation.

    Accepts:
        Contact type, value and optional display and verification flags.

    Returns:
        A validated dictionary suitable for ContactMethod creation.
    """

    method_type = serializers.ChoiceField(
        choices=ContactMethod.MethodType.choices,
    )

    value = serializers.CharField(
        max_length=500,
    )

    label = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
    )

    is_primary = serializers.BooleanField(
        required=False,
        default=False,
    )

    is_verified = serializers.BooleanField(
        required=False,
        default=False,
    )

    is_active = serializers.BooleanField(
        required=False,
        default=True,
    )


class AddressInputSerializer(serializers.Serializer):
    """
    Validate an address or informal trading location during party creation.

    Accepts:
        Address type, address components and optional location notes.

    Returns:
        A validated dictionary suitable for Address creation.
    """

    address_type = serializers.ChoiceField(
        choices=Address.AddressType.choices,
        required=False,
        default=Address.AddressType.OTHER,
    )

    label = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
    )

    line_1 = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    line_2 = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    city = serializers.CharField(
        max_length=120,
        required=False,
        allow_blank=True,
    )

    state_region = serializers.CharField(
        max_length=120,
        required=False,
        allow_blank=True,
    )

    postal_code = serializers.CharField(
        max_length=30,
        required=False,
        allow_blank=True,
    )

    country_code = serializers.CharField(
        max_length=2,
        required=False,
        allow_blank=True,
    )

    location_notes = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    is_primary = serializers.BooleanField(
        required=False,
        default=False,
    )

    is_active = serializers.BooleanField(
        required=False,
        default=True,
    )


class PartySourceInputSerializer(serializers.Serializer):
    """
    Validate party source and supplier provenance data during creation.

    Accepts:
        Source type and available marketplace, market, referral or URL data.

    Returns:
        A validated dictionary suitable for PartySource creation.
    """

    source_type = serializers.ChoiceField(
        choices=PartySource.SourceType.choices,
    )

    platform_name = serializers.CharField(
        max_length=120,
        required=False,
        allow_blank=True,
    )

    seller_name = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    external_id = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    profile_url = serializers.URLField(
        max_length=1000,
        required=False,
        allow_blank=True,
    )

    listing_url = serializers.URLField(
        max_length=1000,
        required=False,
        allow_blank=True,
    )

    market_name = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    location_details = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    referrer_name = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    notes = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    discovered_at = serializers.DateField(
        required=False,
    )

    is_primary = serializers.BooleanField(
        required=False,
        default=False,
    )

    is_active = serializers.BooleanField(
        required=False,
        default=True,
    )


class OrganisationProfileSerializer(serializers.ModelSerializer):
    """
    Serialise an organisation's extended CRM profile.

    Accepts:
        An OrganisationProfile instance.

    Returns:
        A JSON-compatible organisation-profile representation.
    """

    class Meta:
        """Configure OrganisationProfile response fields."""

        model = OrganisationProfile
        fields = [
            "id",
            "legal_name",
            "trading_name",
            "website",
            "industry",
            "business_description",
            "registration_country",
            "incorporation_date",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]


class PersonProfileSerializer(serializers.ModelSerializer):
    """
    Serialise an individual's extended CRM profile.

    Accepts:
        A PersonProfile instance.

    Returns:
        A JSON-compatible person-profile representation.
    """

    full_name = serializers.CharField(
        read_only=True,
    )

    class Meta:
        """Configure PersonProfile response fields."""

        model = PersonProfile
        fields = [
            "id",
            "title",
            "first_name",
            "middle_name",
            "last_name",
            "preferred_name",
            "full_name",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "full_name",
            "created_at",
            "updated_at",
        ]


class ContactMethodSerializer(serializers.ModelSerializer):
    """
    Serialise and validate a party contact method.

    Accepts:
        A ContactMethod instance or standalone contact-method request.

    Returns:
        A contact-method representation or validated model instance.
    """

    method_type_display = serializers.CharField(
        source="get_method_type_display",
        read_only=True,
    )

    class Meta:
        """Configure ContactMethod API fields."""

        model = ContactMethod
        fields = [
            "id",
            "party",
            "method_type",
            "method_type_display",
            "value",
            "normalized_value",
            "label",
            "is_primary",
            "is_verified",
            "is_active",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "normalized_value",
            "created_at",
            "updated_at",
        ]


class AddressSerializer(serializers.ModelSerializer):
    """
    Serialise and validate a party address or informal location.

    Accepts:
        An Address instance or standalone address request.

    Returns:
        An address representation or validated model instance.
    """

    address_type_display = serializers.CharField(
        source="get_address_type_display",
        read_only=True,
    )

    class Meta:
        """Configure Address API fields."""

        model = Address
        fields = [
            "id",
            "party",
            "address_type",
            "address_type_display",
            "label",
            "line_1",
            "line_2",
            "city",
            "state_region",
            "postal_code",
            "country_code",
            "location_notes",
            "is_primary",
            "is_active",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]


class PartySourceSerializer(serializers.ModelSerializer):
    """
    Serialise and validate CRM source and supplier provenance information.

    Accepts:
        A PartySource instance or standalone source request.

    Returns:
        A source representation or validated model instance.
    """

    source_type_display = serializers.CharField(
        source="get_source_type_display",
        read_only=True,
    )

    reference_label = serializers.CharField(
        read_only=True,
    )

    class Meta:
        """Configure PartySource API fields."""

        model = PartySource
        fields = [
            "id",
            "party",
            "source_type",
            "source_type_display",
            "platform_name",
            "seller_name",
            "external_id",
            "profile_url",
            "listing_url",
            "market_name",
            "location_details",
            "referrer_name",
            "notes",
            "discovered_at",
            "last_verified_at",
            "discovered_by",
            "is_primary",
            "is_active",
            "reference_label",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "discovered_by",
            "last_verified_at",
            "reference_label",
            "created_at",
            "updated_at",
        ]


class ContactRoleSerializer(serializers.ModelSerializer):
    """
    Serialise and validate a configurable organisation-contact role.

    Accepts:
        A ContactRole instance or contact-role request.

    Returns:
        A contact-role representation or validated model instance.
    """

    class Meta:
        """Configure ContactRole API fields."""

        model = ContactRole
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "is_active",
            "sort_order",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]


class PartyAffiliationSerializer(serializers.ModelSerializer):
    """
    Serialise and manage a person's relationship with an organisation.

    Accepts:
        A PartyAffiliation instance or affiliation request with contact roles.

    Returns:
        An affiliation representation or persisted PartyAffiliation instance.
    """

    person_name = serializers.CharField(
        source="person.display_name",
        read_only=True,
    )

    organisation_name = serializers.CharField(
        source="organisation.display_name",
        read_only=True,
    )

    contact_roles = ContactRoleSerializer(
        many=True,
        read_only=True,
    )

    contact_role_ids = serializers.PrimaryKeyRelatedField(
        queryset=ContactRole.objects.filter(
            is_active=True,
        ),
        many=True,
        write_only=True,
        required=False,
    )

    class Meta:
        """Configure PartyAffiliation API fields."""

        model = PartyAffiliation
        fields = [
            "id",
            "person",
            "person_name",
            "organisation",
            "organisation_name",
            "job_title",
            "department",
            "start_date",
            "end_date",
            "is_current",
            "is_primary_contact",
            "notes",
            "contact_roles",
            "contact_role_ids",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]

    @transaction.atomic
    def create(self, validated_data):
        """
        Create an affiliation and its requested contact-role assignments.

        Accepts:
            Validated affiliation values and optional ContactRole instances.

        Returns:
            The newly created PartyAffiliation instance.
        """

        contact_roles = validated_data.pop(
            "contact_role_ids",
            [],
        )

        affiliation = PartyAffiliation.objects.create(
            **validated_data,
        )

        AffiliationContactRole.objects.bulk_create(
            [
                AffiliationContactRole(
                    affiliation=affiliation,
                    contact_role=contact_role,
                )
                for contact_role in contact_roles
            ]
        )

        return affiliation

    @transaction.atomic
    def update(self, instance, validated_data):
        """
        Update an affiliation and optionally replace its contact roles.

        Accepts:
            Existing affiliation and validated update values.

        Returns:
            The updated PartyAffiliation instance.
        """

        contact_roles = validated_data.pop(
            "contact_role_ids",
            None,
        )

        instance = super().update(
            instance,
            validated_data,
        )

        if contact_roles is not None:
            instance.role_assignments.all().delete()

            AffiliationContactRole.objects.bulk_create(
                [
                    AffiliationContactRole(
                        affiliation=instance,
                        contact_role=contact_role,
                    )
                    for contact_role in contact_roles
                ]
            )

        return instance


class PartyListSerializer(serializers.ModelSerializer):
    """
    Produce the lightweight CRM directory representation.

    Accepts:
        A Party instance with prefetched roles, contacts and sources.

    Returns:
        A concise JSON-compatible party directory record.
    """

    roles = PartyRoleSerializer(
        many=True,
        read_only=True,
    )

    primary_email = serializers.SerializerMethodField()
    primary_phone = serializers.SerializerMethodField()
    primary_source = serializers.SerializerMethodField()

    class Meta:
        """Configure lightweight Party directory fields."""

        model = Party
        fields = [
            "id",
            "display_name",
            "entity_kind",
            "status",
            "verification_level",
            "is_archived",
            "roles",
            "primary_email",
            "primary_phone",
            "primary_source",
            "created_at",
            "updated_at",
        ]

    def get_primary_email(self, obj):
        """
        Return the party's preferred active email address.

        Accepts:
            A Party instance.

        Returns:
            The primary email string, first active email or None.
        """

        emails = [
            item
            for item in obj.contact_methods.all()
            if item.is_active and item.method_type == ContactMethod.MethodType.EMAIL
        ]

        primary = next(
            (item for item in emails if item.is_primary),
            None,
        )

        selected = primary or (emails[0] if emails else None)

        return selected.value if selected else None

    def get_primary_phone(self, obj):
        """
        Return the party's preferred active phone or messaging number.

        Accepts:
            A Party instance.

        Returns:
            The selected phone string or None.
        """

        phones = [
            item
            for item in obj.contact_methods.all()
            if item.is_active
            and item.method_type
            in {
                ContactMethod.MethodType.PHONE,
                ContactMethod.MethodType.MOBILE,
                ContactMethod.MethodType.WHATSAPP,
            }
        ]

        primary = next(
            (item for item in phones if item.is_primary),
            None,
        )

        selected = primary or (phones[0] if phones else None)

        return selected.value if selected else None

    def get_primary_source(self, obj):
        """
        Return the party's preferred active provenance summary.

        Accepts:
            A Party instance.

        Returns:
            A small source dictionary or None.
        """

        sources = [item for item in obj.sources.all() if item.is_active]

        primary = next(
            (item for item in sources if item.is_primary),
            None,
        )

        selected = primary or (sources[0] if sources else None)

        if selected is None:
            return None

        return {
            "id": str(selected.id),
            "source_type": selected.source_type,
            "platform_name": selected.platform_name,
            "reference_label": selected.reference_label,
        }


class PartyDetailSerializer(serializers.ModelSerializer):
    """
    Produce the complete ordinary CRM party representation.

    Accepts:
        A Party instance with related CRM records prefetched.

    Returns:
        A detailed JSON-compatible CRM party record.
    """

    roles = PartyRoleSerializer(
        many=True,
        read_only=True,
    )

    organisation_profile = OrganisationProfileSerializer(
        read_only=True,
    )

    person_profile = PersonProfileSerializer(
        read_only=True,
    )

    contact_methods = ContactMethodSerializer(
        many=True,
        read_only=True,
    )

    addresses = AddressSerializer(
        many=True,
        read_only=True,
    )

    sources = PartySourceSerializer(
        many=True,
        read_only=True,
    )

    people_affiliations = PartyAffiliationSerializer(
        many=True,
        read_only=True,
    )

    organisation_affiliations = PartyAffiliationSerializer(
        many=True,
        read_only=True,
    )

    merged_into_name = serializers.CharField(
        source="merged_into.display_name",
        read_only=True,
        allow_null=True,
    )

    is_selectable = serializers.BooleanField(
        read_only=True,
    )

    is_traceable = serializers.BooleanField(
        read_only=True,
    )

    class Meta:
        """Configure detailed Party response fields."""

        model = Party
        fields = [
            "id",
            "display_name",
            "entity_kind",
            "status",
            "verification_level",
            "is_archived",
            "archived_at",
            "merged_into",
            "merged_into_name",
            "roles",
            "organisation_profile",
            "person_profile",
            "contact_methods",
            "addresses",
            "sources",
            "people_affiliations",
            "organisation_affiliations",
            "is_selectable",
            "is_traceable",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]

        read_only_fields = fields


class PartyWriteSerializer(serializers.ModelSerializer):
    """
    Validate controlled CRM party creation and ordinary identity updates.

    Accepts:
        Party identity, roles and optional nested creation records.

    Returns:
        A newly created or updated Party instance.
    """

    roles = serializers.ListField(
        child=serializers.ChoiceField(
            choices=PartyRole.Role.choices,
        ),
        write_only=True,
        required=False,
    )

    organisation_profile = OrganisationProfileInputSerializer(
        write_only=True,
        required=False,
    )

    person_profile = PersonProfileInputSerializer(
        write_only=True,
        required=False,
    )

    contact_methods = ContactMethodInputSerializer(
        many=True,
        write_only=True,
        required=False,
    )

    addresses = AddressInputSerializer(
        many=True,
        write_only=True,
        required=False,
    )

    sources = PartySourceInputSerializer(
        many=True,
        write_only=True,
        required=False,
    )

    class Meta:
        """Configure Party create and update fields."""

        model = Party
        fields = [
            "id",
            "display_name",
            "entity_kind",
            "verification_level",
            "roles",
            "organisation_profile",
            "person_profile",
            "contact_methods",
            "addresses",
            "sources",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        """
        Validate profile compatibility and restricted nested updates.

        Accepts:
            Submitted and field-validated party values.

        Returns:
            Validated party values or raises a serializer validation error.
        """

        entity_kind = attrs.get(
            "entity_kind",
            (
                self.instance.entity_kind
                if self.instance
                else Party.EntityKind.ORGANISATION
            ),
        )

        organisation_profile = attrs.get(
            "organisation_profile",
        )

        person_profile = attrs.get(
            "person_profile",
        )

        if entity_kind == Party.EntityKind.INDIVIDUAL and organisation_profile:
            raise serializers.ValidationError(
                {
                    "organisation_profile": (
                        "An individual cannot have an organisation profile."
                    )
                }
            )

        if entity_kind != Party.EntityKind.INDIVIDUAL and person_profile:
            raise serializers.ValidationError(
                {
                    "person_profile": (
                        "An organisation or trading name cannot have a person profile."
                    )
                }
            )

        nested_fields = {
            "organisation_profile",
            "person_profile",
            "contact_methods",
            "addresses",
            "sources",
        }

        if self.instance and nested_fields.intersection(
            attrs.keys(),
        ):
            raise serializers.ValidationError(
                {
                    "detail": (
                        "Related CRM records must be updated "
                        "through their dedicated endpoints."
                    )
                }
            )

        if (
            self.instance
            and entity_kind != self.instance.entity_kind
            and (
                hasattr(
                    self.instance,
                    "organisation_profile",
                )
                or hasattr(
                    self.instance,
                    "person_profile",
                )
                or self.instance.people_affiliations.exists()
                or self.instance.organisation_affiliations.exists()
            )
        ):
            raise serializers.ValidationError(
                {
                    "entity_kind": (
                        "The entity kind cannot be changed after "
                        "profiles or affiliations have been added."
                    )
                }
            )

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        """
        Create a party and its initial related CRM records atomically.

        Accepts:
            Validated party, role, profile, contact, address and source data.

        Returns:
            The newly created Party instance.
        """

        roles = list(
            dict.fromkeys(
                validated_data.pop(
                    "roles",
                    [],
                )
            )
        )

        organisation_profile = validated_data.pop(
            "organisation_profile",
            None,
        )

        person_profile = validated_data.pop(
            "person_profile",
            None,
        )

        contact_methods = validated_data.pop(
            "contact_methods",
            [],
        )

        addresses = validated_data.pop(
            "addresses",
            [],
        )

        sources = validated_data.pop(
            "sources",
            [],
        )

        duplicate_probe = {
            "display_name": validated_data.get(
                "display_name",
            ),
            "entity_kind": validated_data.get(
                "entity_kind",
            ),
            "contact_methods": contact_methods,
            "sources": sources,
        }

        exact_duplicates = [
            item
            for item in find_party_duplicates(
                duplicate_probe,
            )
            if item["classification"] == "EXACT"
        ]

        if exact_duplicates:
            raise serializers.ValidationError(
                {
                    "detail": ("An exact CRM duplicate already exists."),
                    "code": "exact_duplicate",
                    "duplicates": exact_duplicates,
                }
            )

        request = self.context.get("request")
        user = request.user if request and request.user.is_authenticated else None

        party = Party.objects.create(
            created_by=user,
            updated_by=user,
            **validated_data,
        )

        for role in roles:
            PartyRole.objects.create(
                party=party,
                role=role,
            )

        if party.entity_kind == Party.EntityKind.INDIVIDUAL:
            PersonProfile.objects.create(
                party=party,
                **(person_profile or {"preferred_name": (party.display_name)}),
            )
        else:
            OrganisationProfile.objects.create(
                party=party,
                **(organisation_profile or {"trading_name": (party.display_name)}),
            )

        for contact_method in contact_methods:
            ContactMethod.objects.create(
                party=party,
                **contact_method,
            )

        for address in addresses:
            Address.objects.create(
                party=party,
                **address,
            )

        for source in sources:
            PartySource.objects.create(
                party=party,
                discovered_by=user,
                **source,
            )

        return party

    @transaction.atomic
    def update(self, instance, validated_data):
        """
        Update party identity and optionally replace active business roles.

        Accepts:
            Existing Party instance and validated update values.

        Returns:
            The updated Party instance.
        """

        roles = validated_data.pop(
            "roles",
            None,
        )

        request = self.context.get("request")

        if request and request.user.is_authenticated:
            validated_data["updated_by"] = request.user

        instance = super().update(
            instance,
            validated_data,
        )

        if roles is not None:
            desired_roles = set(roles)

            for existing_role in instance.roles.all():
                should_be_active = existing_role.role in desired_roles

                if existing_role.is_active != should_be_active:
                    existing_role.is_active = should_be_active
                    existing_role.save(
                        update_fields={
                            "is_active",
                            "deactivated_at",
                            "updated_at",
                        }
                    )

            existing_values = set(
                instance.roles.values_list(
                    "role",
                    flat=True,
                )
            )

            for new_role in desired_roles - existing_values:
                PartyRole.objects.create(
                    party=instance,
                    role=new_role,
                )

        return instance

    def to_representation(self, instance):
        """
        Return the complete party response after create or update.

        Accepts:
            A saved Party instance.

        Returns:
            The PartyDetailSerializer representation.
        """

        return PartyDetailSerializer(
            instance,
            context=self.context,
        ).data


class QuickSupplierCreateSerializer(serializers.Serializer):
    """
    Validate and create a traceable minimal supplier quickly.

    Accepts:
        A supplier name and at least one contact or source detail.

    Returns:
        A new Party carrying an active supplier role and provenance record.
    """

    display_name = serializers.CharField(
        max_length=255,
    )

    entity_kind = serializers.ChoiceField(
        choices=Party.EntityKind.choices,
        default=Party.EntityKind.TRADING_NAME,
    )

    email = serializers.EmailField(
        required=False,
        allow_blank=True,
    )

    phone = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
    )

    source_type = serializers.ChoiceField(
        choices=PartySource.SourceType.choices,
        required=False,
    )

    platform_name = serializers.CharField(
        max_length=120,
        required=False,
        allow_blank=True,
    )

    seller_name = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    external_id = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    profile_url = serializers.URLField(
        max_length=1000,
        required=False,
        allow_blank=True,
    )

    listing_url = serializers.URLField(
        max_length=1000,
        required=False,
        allow_blank=True,
    )

    market_name = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    location_details = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    referrer_name = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    source_notes = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    def validate(self, attrs):
        """
        Require enough information to trace the supplier later.

        Accepts:
            Field-validated quick-supplier values.

        Returns:
            Validated values or raises a serializer validation error.
        """

        traceable_fields = [
            attrs.get("email"),
            attrs.get("phone"),
            attrs.get("platform_name"),
            attrs.get("external_id"),
            attrs.get("profile_url"),
            attrs.get("listing_url"),
            attrs.get("market_name"),
            attrs.get("location_details"),
            attrs.get("referrer_name"),
            attrs.get("source_notes"),
        ]

        if not any(traceable_fields):
            raise serializers.ValidationError(
                {
                    "detail": (
                        "Provide at least one phone, email, marketplace, "
                        "market, referral, URL or source-note detail."
                    ),
                    "code": "supplier_source_required",
                }
            )

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        """
        Create the minimal supplier, contact details and provenance record.

        Accepts:
            Validated quick-supplier values.

        Returns:
            The newly created supplier Party.
        """

        request = self.context.get("request")
        user = request.user if request and request.user.is_authenticated else None

        email = validated_data.pop(
            "email",
            "",
        )

        phone = validated_data.pop(
            "phone",
            "",
        )

        source_type = validated_data.pop(
            "source_type",
            None,
        )

        source_notes = validated_data.pop(
            "source_notes",
            "",
        )

        display_name = validated_data.pop(
            "display_name",
        )

        entity_kind = validated_data.pop(
            "entity_kind",
        )

        if source_type is None:
            if (
                validated_data.get("platform_name")
                or validated_data.get("profile_url")
                or validated_data.get("listing_url")
            ):
                source_type = PartySource.SourceType.ONLINE_MARKETPLACE
            elif validated_data.get("market_name") or validated_data.get(
                "location_details"
            ):
                source_type = PartySource.SourceType.PHYSICAL_MARKET
            elif validated_data.get("referrer_name"):
                source_type = PartySource.SourceType.REFERRAL
            else:
                source_type = PartySource.SourceType.DIRECT_CONTACT

        contact_probe = []

        if email:
            contact_probe.append(
                {
                    "method_type": "EMAIL",
                    "value": email,
                }
            )

        if phone:
            contact_probe.append(
                {
                    "method_type": "PHONE",
                    "value": phone,
                }
            )

        source_probe = {
            **validated_data,
            "source_type": source_type,
            "seller_name": (validated_data.get("seller_name") or display_name),
            "notes": source_notes,
        }

        duplicate_probe = {
            "display_name": display_name,
            "entity_kind": entity_kind,
            "contact_methods": contact_probe,
            "sources": [
                source_probe,
            ],
        }

        exact_duplicates = [
            item
            for item in find_party_duplicates(
                duplicate_probe,
            )
            if item["classification"] == "EXACT"
        ]

        if exact_duplicates:
            raise serializers.ValidationError(
                {
                    "detail": ("An exact CRM supplier duplicate exists."),
                    "code": "exact_duplicate",
                    "duplicates": exact_duplicates,
                }
            )

        party = Party.objects.create(
            display_name=display_name,
            entity_kind=entity_kind,
            verification_level=(Party.VerificationLevel.MINIMAL),
            created_by=user,
            updated_by=user,
        )

        PartyRole.objects.create(
            party=party,
            role=PartyRole.Role.SUPPLIER,
        )

        if entity_kind == Party.EntityKind.INDIVIDUAL:
            PersonProfile.objects.create(
                party=party,
                preferred_name=display_name,
            )
        else:
            OrganisationProfile.objects.create(
                party=party,
                trading_name=display_name,
            )

        if email:
            ContactMethod.objects.create(
                party=party,
                method_type=(ContactMethod.MethodType.EMAIL),
                value=email,
                is_primary=True,
            )

        if phone:
            ContactMethod.objects.create(
                party=party,
                method_type=(ContactMethod.MethodType.PHONE),
                value=phone,
                is_primary=True,
            )

        PartySource.objects.create(
            party=party,
            source_type=source_type,
            seller_name=(
                validated_data.pop(
                    "seller_name",
                    "",
                )
                or display_name
            ),
            notes=source_notes,
            discovered_by=user,
            is_primary=True,
            **validated_data,
        )

        return party

    def to_representation(self, instance):
        """
        Return the complete party response after quick supplier creation.

        Accepts:
            The newly created supplier Party.

        Returns:
            The PartyDetailSerializer representation.
        """

        return PartyDetailSerializer(
            instance,
            context=self.context,
        ).data


class DuplicateCheckSerializer(serializers.Serializer):
    """
    Validate duplicate-check input and execute duplicate discovery.

    Accepts:
        An existing party or identifying party, contact and source values.

    Returns:
        Validated input and a list produced by ``get_matches``.
    """

    party_id = serializers.PrimaryKeyRelatedField(
        queryset=Party.objects.all(),
        source="party",
        required=False,
    )

    display_name = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    entity_kind = serializers.ChoiceField(
        choices=Party.EntityKind.choices,
        required=False,
    )

    email = serializers.EmailField(
        required=False,
        allow_blank=True,
    )

    phone = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    platform_name = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    seller_name = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    external_id = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    profile_url = serializers.URLField(
        required=False,
        allow_blank=True,
    )

    listing_url = serializers.URLField(
        required=False,
        allow_blank=True,
    )

    def validate(self, attrs):
        """
        Require either an existing party or at least one identifying value.

        Accepts:
            Field-validated duplicate-check values.

        Returns:
            Validated values or raises a serializer validation error.
        """

        if attrs.get("party"):
            return attrs

        identifying_values = [
            attrs.get("display_name"),
            attrs.get("email"),
            attrs.get("phone"),
            attrs.get("external_id"),
            attrs.get("profile_url"),
            attrs.get("listing_url"),
        ]

        if not any(identifying_values):
            raise serializers.ValidationError(
                {"detail": ("Provide party_id or at least one identifying value.")}
            )

        return attrs

    def get_matches(self):
        """
        Run duplicate discovery for the validated request.

        Accepts:
            No additional values after serializer validation.

        Returns:
            A list of exact, strong and weak possible duplicates.
        """

        party = self.validated_data.get(
            "party",
        )

        if party:
            probe = build_duplicate_probe_from_party(
                party,
            )
            exclude_party_id = party.id
        else:
            contact_methods = []

            if self.validated_data.get("email"):
                contact_methods.append(
                    {
                        "method_type": "EMAIL",
                        "value": self.validated_data["email"],
                    }
                )

            if self.validated_data.get("phone"):
                contact_methods.append(
                    {
                        "method_type": "PHONE",
                        "value": self.validated_data["phone"],
                    }
                )

            probe = {
                "display_name": self.validated_data.get(
                    "display_name",
                ),
                "entity_kind": self.validated_data.get(
                    "entity_kind",
                ),
                "contact_methods": contact_methods,
                "sources": [
                    {
                        "platform_name": (
                            self.validated_data.get(
                                "platform_name",
                            )
                        ),
                        "seller_name": (
                            self.validated_data.get(
                                "seller_name",
                            )
                        ),
                        "external_id": (
                            self.validated_data.get(
                                "external_id",
                            )
                        ),
                        "profile_url": (
                            self.validated_data.get(
                                "profile_url",
                            )
                        ),
                        "listing_url": (
                            self.validated_data.get(
                                "listing_url",
                            )
                        ),
                    }
                ],
            }
            exclude_party_id = None

        return find_party_duplicates(
            probe,
            exclude_party_id=exclude_party_id,
        )


class PartyLifecycleSerializer(serializers.Serializer):
    """
    Validate the reason supplied for a CRM lifecycle operation.

    Accepts:
        A human-readable reason.

    Returns:
        A validated reason dictionary.
    """

    reason = serializers.CharField(
        min_length=3,
        max_length=2000,
    )


class PartyMergeSerializer(serializers.Serializer):
    """
    Validate a request to merge the current party into another party.

    Accepts:
        A target Party and merge reason.

    Returns:
        Validated target and reason values.
    """

    target_party = serializers.PrimaryKeyRelatedField(
        queryset=Party.objects.exclude(
            status=Party.Status.MERGED,
        )
    )

    reason = serializers.CharField(
        min_length=3,
        max_length=2000,
    )


class PartyNoteSerializer(serializers.ModelSerializer):
    """
    Serialise and validate ordinary and confidential CRM notes.

    Accepts:
        A PartyNote instance or note request data.

    Returns:
        A note representation or validated PartyNote instance.
    """

    author_name = serializers.SerializerMethodField()

    class Meta:
        """Configure PartyNote API fields."""

        model = PartyNote
        fields = [
            "id",
            "party",
            "note_type",
            "content",
            "is_confidential",
            "author",
            "author_name",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "author",
            "author_name",
            "created_at",
            "updated_at",
        ]

    def get_author_name(self, obj):
        """
        Return the author's preferred display name.

        Accepts:
            A PartyNote instance.

        Returns:
            The author's full name, username or None.
        """

        if obj.author is None:
            return None

        return obj.author.get_full_name() or obj.author.username

    def validate(self, attrs):
        """
        Enforce the confidential-note management permission.

        Accepts:
            Field-validated note values.

        Returns:
            Validated values or raises PermissionDenied.
        """

        request = self.context.get("request")

        confidential = attrs.get(
            "is_confidential",
            (self.instance.is_confidential if self.instance else False),
        )

        note_type = attrs.get(
            "note_type",
            (self.instance.note_type if self.instance else PartyNote.NoteType.GENERAL),
        )

        confidential = confidential or note_type == PartyNote.NoteType.CONFIDENTIAL

        if (
            confidential
            and request
            and not request.user.has_perm("crm.manage_confidentialnote")
        ):
            raise serializers.ValidationError(
                {
                    "is_confidential": (
                        "You do not have permission to manage confidential CRM notes."
                    )
                }
            )

        return attrs


class PartyInteractionSerializer(serializers.ModelSerializer):
    """
    Serialise and validate CRM communication and interaction history.

    Accepts:
        A PartyInteraction instance or interaction request data.

    Returns:
        An interaction representation or validated model instance.
    """

    party_name = serializers.CharField(
        source="party.display_name",
        read_only=True,
    )

    contact_name = serializers.CharField(
        source="contact_party.display_name",
        read_only=True,
        allow_null=True,
    )

    interaction_type_display = serializers.CharField(
        source="get_interaction_type_display",
        read_only=True,
    )

    staff_member_name = serializers.SerializerMethodField()

    class Meta:
        """Configure PartyInteraction API fields."""

        model = PartyInteraction
        fields = [
            "id",
            "party",
            "party_name",
            "contact_party",
            "contact_name",
            "interaction_type",
            "interaction_type_display",
            "occurred_at",
            "subject",
            "summary",
            "staff_member",
            "staff_member_name",
            "follow_up_at",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "staff_member",
            "staff_member_name",
            "created_at",
            "updated_at",
        ]

    def get_staff_member_name(self, obj):
        """
        Return the responsible staff member's display name.

        Accepts:
            A PartyInteraction instance.

        Returns:
            Full name, username or None.
        """

        if obj.staff_member is None:
            return None

        return obj.staff_member.get_full_name() or obj.staff_member.username


class PartyStatusHistorySerializer(serializers.ModelSerializer):
    """
    Serialise immutable CRM party status history.

    Accepts:
        A PartyStatusHistory instance.

    Returns:
        A JSON-compatible status-history representation.
    """

    changed_by_name = serializers.SerializerMethodField()

    class Meta:
        """Configure PartyStatusHistory response fields."""

        model = PartyStatusHistory
        fields = [
            "id",
            "party",
            "previous_status",
            "new_status",
            "reason",
            "changed_by",
            "changed_by_name",
            "metadata",
            "created_at",
        ]

        read_only_fields = fields

    def get_changed_by_name(self, obj):
        """
        Return the status-change actor's display name.

        Accepts:
            A PartyStatusHistory instance.

        Returns:
            Full name, username or None.
        """

        if obj.changed_by is None:
            return None

        return obj.changed_by.get_full_name() or obj.changed_by.username


class PartyMergeRecordSerializer(serializers.ModelSerializer):
    """
    Serialise an immutable CRM party merge record.

    Accepts:
        A PartyMergeRecord instance.

    Returns:
        A JSON-compatible merge-history representation.
    """

    source_name = serializers.CharField(
        source="source_party.display_name",
        read_only=True,
    )

    target_name = serializers.CharField(
        source="target_party.display_name",
        read_only=True,
    )

    merged_by_name = serializers.SerializerMethodField()

    class Meta:
        """Configure PartyMergeRecord response fields."""

        model = PartyMergeRecord
        fields = [
            "id",
            "source_party",
            "source_name",
            "target_party",
            "target_name",
            "reason",
            "merged_by",
            "merged_by_name",
            "summary",
            "created_at",
        ]

        read_only_fields = fields

    def get_merged_by_name(self, obj):
        """
        Return the merge actor's display name.

        Accepts:
            A PartyMergeRecord instance.

        Returns:
            Full name, username or None.
        """

        if obj.merged_by is None:
            return None

        return obj.merged_by.get_full_name() or obj.merged_by.username


class PartyIdentifierSerializer(serializers.ModelSerializer):
    """Serialise sensitive identifiers without exposing plaintext."""

    value = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
    )

    masked_value = serializers.CharField(
        read_only=True,
    )

    class Meta:
        model = PartyIdentifier
        fields = [
            "id",
            "party",
            "identifier_type",
            "label",
            "value",
            "masked_value",
            "issuing_country",
            "issue_date",
            "expiry_date",
            "is_verified",
            "is_active",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "masked_value",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        """
        Require plaintext when creating an identifier.

        Args:
            attrs: Field-validated serializer values.

        Returns:
            dict: Validated values.

        Raises:
            ValidationError: If a new identifier has no value.
        """

        if self.instance is None and not attrs.get(
            "value",
        ):
            raise serializers.ValidationError(
                {"value": ("An identifier value is required.")}
            )

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        """
        Encrypt and create an identifier.

        Args:
            validated_data: Validated identifier metadata and plaintext value.

        Returns:
            PartyIdentifier: Created identifier.
        """

        value = validated_data.pop(
            "value",
        )

        request = self.context.get(
            "request",
        )

        user = request.user if request and request.user.is_authenticated else None

        identifier = PartyIdentifier(
            created_by=user,
            updated_by=user,
            **validated_data,
        )

        identifier.set_value(value)
        identifier.save()

        return identifier

    @transaction.atomic
    def update(
        self,
        instance,
        validated_data,
    ):
        """
        Update identifier metadata and optionally rotate encrypted plaintext.

        Args:
            instance: Existing PartyIdentifier.
            validated_data: Validated replacement values.

        Returns:
            PartyIdentifier: Updated identifier.
        """

        value = validated_data.pop(
            "value",
            None,
        )

        request = self.context.get(
            "request",
        )

        if value is not None:
            instance.set_value(value)

        for field_name, field_value in validated_data.items():
            setattr(
                instance,
                field_name,
                field_value,
            )

        if request and request.user.is_authenticated:
            instance.updated_by = request.user

        instance.save()

        return instance


class PartyBankAccountSerializer(serializers.ModelSerializer):
    """Serialise payment details while masking encrypted values."""

    account_number = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
    )

    iban = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
    )

    masked_account_number = serializers.CharField(
        read_only=True,
    )

    masked_iban = serializers.CharField(
        read_only=True,
    )

    class Meta:
        model = PartyBankAccount
        fields = [
            "id",
            "party",
            "payment_method",
            "account_name",
            "bank_name",
            "provider_name",
            "account_number",
            "masked_account_number",
            "iban",
            "masked_iban",
            "swift_bic",
            "currency",
            "country_code",
            "verification_status",
            "is_primary",
            "is_active",
            "notes",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "masked_account_number",
            "masked_iban",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        """
        Require an account number or IBAN for new records.

        Args:
            attrs: Field-validated payment-account values.

        Returns:
            dict: Validated values.

        Raises:
            ValidationError: If no payment identifier is supplied.
        """

        if self.instance is None and not (
            attrs.get("account_number") or attrs.get("iban")
        ):
            raise serializers.ValidationError(
                {"detail": ("Provide an account number or IBAN.")}
            )

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        """
        Encrypt and create a payment account.

        Args:
            validated_data: Validated metadata and plaintext payment values.

        Returns:
            PartyBankAccount: Created payment account.
        """

        account_number = validated_data.pop(
            "account_number",
            None,
        )

        iban = validated_data.pop(
            "iban",
            None,
        )

        request = self.context.get(
            "request",
        )

        user = request.user if request and request.user.is_authenticated else None

        account = PartyBankAccount(
            created_by=user,
            updated_by=user,
            **validated_data,
        )

        if account_number:
            account.set_account_number(
                account_number,
            )

        if iban:
            account.set_iban(iban)

        account.save()

        return account

    @transaction.atomic
    def update(
        self,
        instance,
        validated_data,
    ):
        """
        Update payment metadata and optionally replace encrypted values.

        Args:
            instance: Existing PartyBankAccount.
            validated_data: Validated replacement values.

        Returns:
            PartyBankAccount: Updated payment account.
        """

        account_number = validated_data.pop(
            "account_number",
            None,
        )

        iban = validated_data.pop(
            "iban",
            None,
        )

        request = self.context.get(
            "request",
        )

        if account_number is not None:
            instance.set_account_number(
                account_number,
            )

        if iban is not None:
            instance.set_iban(iban)

        for field_name, field_value in validated_data.items():
            setattr(
                instance,
                field_name,
                field_value,
            )

        if request and request.user.is_authenticated:
            instance.updated_by = request.user

        instance.save()

        return instance


class PartyDocumentSerializer(serializers.ModelSerializer):
    """Serialise CRM document metadata without exposing provider credentials."""

    category_display = serializers.CharField(
        source="get_category_display",
        read_only=True,
    )

    verification_status_display = serializers.CharField(
        source="get_verification_status_display",
        read_only=True,
    )

    class Meta:
        model = PartyDocument
        fields = [
            "id",
            "party",
            "category",
            "category_display",
            "original_filename",
            "mime_type",
            "size_bytes",
            "checksum_sha256",
            "storage_provider",
            "description",
            "is_confidential",
            "verification_status",
            "verification_status_display",
            "expires_at",
            "is_active",
            "uploaded_by",
            "deleted_by",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "original_filename",
            "mime_type",
            "size_bytes",
            "checksum_sha256",
            "storage_provider",
            "is_active",
            "uploaded_by",
            "deleted_by",
            "deleted_at",
            "created_at",
            "updated_at",
        ]


class PartyDocumentUploadSerializer(serializers.Serializer):
    """Validate multipart CRM document uploads."""

    party = serializers.PrimaryKeyRelatedField(
        queryset=Party.objects.exclude(
            status=Party.Status.MERGED,
        ),
    )

    file = serializers.FileField()

    category = serializers.ChoiceField(
        choices=PartyDocument.Category.choices,
        default=PartyDocument.Category.OTHER,
    )

    description = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    is_confidential = serializers.BooleanField(
        default=False,
    )

    verification_status = serializers.ChoiceField(
        choices=(PartyDocument.VerificationStatus.choices),
        default=(PartyDocument.VerificationStatus.UNVERIFIED),
    )

    expires_at = serializers.DateField(
        required=False,
        allow_null=True,
    )

    def validate_is_confidential(
        self,
        value,
    ):
        """
        Validate confidential-document creation permission.

        Args:
            value: Submitted confidential flag.

        Returns:
            bool: Validated flag.

        Raises:
            ValidationError: If the user lacks confidential management access.
        """

        request = self.context.get(
            "request",
        )

        if (
            value
            and request
            and not request.user.has_perm("crm.manage_confidential_partydocument")
        ):
            raise serializers.ValidationError(
                "You do not have permission to upload confidential CRM documents."
            )

        return value


class PartyDocumentUpdateSerializer(serializers.ModelSerializer):
    """Validate metadata-only CRM document updates."""

    class Meta:
        model = PartyDocument
        fields = [
            "description",
            "is_confidential",
            "verification_status",
            "expires_at",
        ]

    def validate_is_confidential(
        self,
        value,
    ):
        """
        Validate confidential-document management permission.

        Args:
            value: Submitted confidential flag.

        Returns:
            bool: Validated flag.

        Raises:
            ValidationError: If the user lacks confidential management access.
        """

        request = self.context.get(
            "request",
        )

        if (
            value
            and request
            and not request.user.has_perm("crm.manage_confidential_partydocument")
        ):
            raise serializers.ValidationError(
                "You do not have permission to manage confidential CRM documents."
            )

        return value
