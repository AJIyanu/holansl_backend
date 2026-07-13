from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import (
    Address,
    AffiliationContactRole,
    ContactMethod,
    ContactRole,
    OrganisationProfile,
    Party,
    PartyAffiliation,
    PartyRole,
    PartySource,
    PersonProfile,
)


class CRMFoundationModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="crm.tester",
            email="crm.tester@holansl.com",
            password="TestPassword123!",
        )

    def test_minimal_market_supplier_is_allowed_and_traceable(
        self,
    ):
        supplier = Party.objects.create(
            display_name="  Ade Market   Tools  ",
            entity_kind=Party.EntityKind.TRADING_NAME,
            created_by=self.user,
            updated_by=self.user,
        )

        PartyRole.objects.create(
            party=supplier,
            role=PartyRole.Role.SUPPLIER,
        )

        PartySource.objects.create(
            party=supplier,
            source_type=(PartySource.SourceType.PHYSICAL_MARKET),
            market_name="Lagos Island Market",
            location_details=("Second row beside the electrical section"),
            discovered_by=self.user,
            is_primary=True,
        )

        supplier.refresh_from_db()

        self.assertEqual(
            supplier.display_name,
            "Ade Market Tools",
        )
        self.assertEqual(
            supplier.normalized_name,
            "ade market tools",
        )
        self.assertEqual(
            supplier.name,
            "Ade Market Tools",
        )

        self.assertEqual(
            supplier.verification_level,
            Party.VerificationLevel.MINIMAL,
        )
        self.assertTrue(supplier.has_role(PartyRole.Role.SUPPLIER))
        self.assertTrue(supplier.is_traceable())

    def test_online_marketplace_supplier_can_store_platform_identity(
        self,
    ):
        supplier = Party.objects.create(
            display_name="Best Tools Store",
            entity_kind=Party.EntityKind.TRADING_NAME,
        )

        PartyRole.objects.create(
            party=supplier,
            role=PartyRole.Role.SUPPLIER,
        )

        source = PartySource.objects.create(
            party=supplier,
            source_type=(PartySource.SourceType.ONLINE_MARKETPLACE),
            platform_name="eBay",
            seller_name="best-tools-store",
            external_id="seller-2481",
            profile_url=("https://www.ebay.com/usr/best-tools-store"),
            is_primary=True,
        )

        self.assertEqual(
            source.reference_label,
            "best-tools-store",
        )
        self.assertTrue(supplier.is_traceable())

    def test_party_can_be_both_client_and_supplier(
        self,
    ):
        party = Party.objects.create(
            display_name="Dual Relationship Limited",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        PartyRole.objects.create(
            party=party,
            role=PartyRole.Role.CLIENT,
        )

        PartyRole.objects.create(
            party=party,
            role=PartyRole.Role.SUPPLIER,
        )

        party.refresh_from_db()

        self.assertTrue(party.has_role(PartyRole.Role.CLIENT))
        self.assertTrue(party.has_role(PartyRole.Role.SUPPLIER))

    def test_contact_method_is_normalized_and_duplicate_is_blocked(
        self,
    ):
        party = Party.objects.create(
            display_name="Contact Test Party",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        ContactMethod.objects.create(
            party=party,
            method_type=(ContactMethod.MethodType.EMAIL),
            value=" SALES@Example.COM ",
            is_primary=True,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ContactMethod.objects.create(
                    party=party,
                    method_type=(ContactMethod.MethodType.EMAIL),
                    value="sales@example.com",
                )

    def test_only_one_active_primary_contact_per_type(
        self,
    ):
        party = Party.objects.create(
            display_name="Primary Contact Test",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        ContactMethod.objects.create(
            party=party,
            method_type=(ContactMethod.MethodType.MOBILE),
            value="+234 800 111 2222",
            is_primary=True,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ContactMethod.objects.create(
                    party=party,
                    method_type=(ContactMethod.MethodType.MOBILE),
                    value="+234 800 333 4444",
                    is_primary=True,
                )

    def test_person_can_represent_more_than_one_organisation(
        self,
    ):
        person = Party.objects.create(
            display_name="Musa Ade",
            entity_kind=Party.EntityKind.INDIVIDUAL,
        )

        organisation_one = Party.objects.create(
            display_name="Company One",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        organisation_two = Party.objects.create(
            display_name="Company Two",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        PersonProfile.objects.create(
            party=person,
            first_name="Musa",
            last_name="Ade",
        )

        role = ContactRole.objects.get(
            slug="procurement",
        )

        first_affiliation = PartyAffiliation.objects.create(
            person=person,
            organisation=organisation_one,
            job_title="Procurement Manager",
        )

        second_affiliation = PartyAffiliation.objects.create(
            person=person,
            organisation=organisation_two,
            job_title="Consultant",
        )

        AffiliationContactRole.objects.create(
            affiliation=first_affiliation,
            contact_role=role,
            is_primary=True,
        )

        self.assertEqual(
            person.organisation_affiliations.count(),
            2,
        )
        self.assertEqual(
            first_affiliation.contact_roles.get(),
            role,
        )
        self.assertTrue(second_affiliation.is_current)

    def test_affiliation_rejects_non_person_as_person(
        self,
    ):
        organisation_one = Party.objects.create(
            display_name="Organisation One",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        organisation_two = Party.objects.create(
            display_name="Organisation Two",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        with self.assertRaises(ValidationError):
            PartyAffiliation.objects.create(
                person=organisation_one,
                organisation=organisation_two,
            )

    def test_profiles_enforce_party_kind(
        self,
    ):
        individual = Party.objects.create(
            display_name="Individual Party",
            entity_kind=Party.EntityKind.INDIVIDUAL,
        )

        organisation = Party.objects.create(
            display_name="Organisation Party",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        PersonProfile.objects.create(
            party=individual,
            preferred_name="Individual",
        )

        OrganisationProfile.objects.create(
            party=organisation,
            legal_name=("Organisation Party Limited"),
        )

        with self.assertRaises(ValidationError):
            OrganisationProfile.objects.create(
                party=individual,
                legal_name="Wrong Profile",
            )

    def test_address_accepts_informal_market_location(
        self,
    ):
        party = Party.objects.create(
            display_name="Market Location Supplier",
            entity_kind=Party.EntityKind.TRADING_NAME,
        )

        address = Address.objects.create(
            party=party,
            address_type=Address.AddressType.MARKET,
            location_notes=("Opposite the main gate, stall 14"),
            is_primary=True,
        )

        self.assertEqual(
            address.location_notes,
            "Opposite the main gate, stall 14",
        )

    def test_source_requires_traceable_detail(
        self,
    ):
        party = Party.objects.create(
            display_name="Untraceable Source Test",
            entity_kind=Party.EntityKind.TRADING_NAME,
        )

        with self.assertRaises(ValidationError):
            PartySource.objects.create(
                party=party,
                source_type=PartySource.SourceType.OTHER,
            )

    def test_archiving_sets_and_clears_timestamp(
        self,
    ):
        party = Party.objects.create(
            display_name="Archive Test",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        party.is_archived = True
        party.save(
            update_fields={
                "is_archived",
            }
        )

        self.assertIsNotNone(party.archived_at)

        party.is_archived = False
        party.save(
            update_fields={
                "is_archived",
            }
        )

        self.assertIsNone(party.archived_at)

    def test_default_contact_roles_and_custom_permissions_exist(
        self,
    ):
        expected_slugs = {
            "general",
            "procurement",
            "accounts",
            "technical",
            "delivery",
            "management",
            "sales",
            "other",
        }

        existing_slugs = set(
            ContactRole.objects.values_list(
                "slug",
                flat=True,
            )
        )

        self.assertTrue(expected_slugs.issubset(existing_slugs))

        permission = Permission.objects.get(
            content_type__app_label="crm",
            codename="deactivate_party",
        )

        self.assertEqual(
            permission.name,
            "Can deactivate CRM party",
        )
