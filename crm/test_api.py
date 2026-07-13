"""
CRM Stage 3 API and service tests.

This module verifies permissions, quick supplier creation, duplicates,
lifecycle history, confidential notes, merging and deletion protection.

Test methods accept the Django test database and return no value; assertions
report whether the expected CRM behaviour occurred.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import AuditLog

from .models import (
    ContactMethod,
    Party,
    PartyMergeRecord,
    PartyNote,
    PartyRole,
    PartySource,
    PartyStatusHistory,
)


class CRMStageThreeAPITests(APITestCase):
    """
    Verify the CRM Stage 3 API and business-operation behaviour.

    Accepts:
        Django's isolated test database and DRF test client.

    Returns:
        No direct value; each test produces assertions.
    """

    def setUp(self):
        """
        Create authorised and unauthorised users for each API test.

        Accepts:
            The isolated Django test database.

        Returns:
            None.
        """

        User = get_user_model()

        self.user = User.objects.create_user(
            username="crm.api.user",
            email="crm.api.user@holansl.com",
            password="TestPassword123!",
        )

        self.no_permission_user = User.objects.create_user(
            username="crm.no.permission",
            email="crm.no.permission@holansl.com",
            password="TestPassword123!",
        )

        self.superuser = User.objects.create_superuser(
            username="crm.superuser",
            email="crm.superuser@holansl.com",
            password="TestPassword123!",
        )

        self._grant_permissions(
            self.user,
            [
                "view_party",
                "add_party",
                "change_party",
                "deactivate_party",
                "archive_party",
                "block_party",
                "merge_party",
                "view_party_history",
                "view_partynote",
                "add_partynote",
                "change_partynote",
                "delete_partynote",
                "view_partyinteraction",
                "add_partyinteraction",
                "change_partyinteraction",
                "delete_partyinteraction",
            ],
        )

        self.client = APIClient()
        self.client.force_authenticate(
            self.user,
        )

    def _grant_permissions(
        self,
        user,
        codenames,
    ):
        """
        Assign selected CRM permissions directly to a test user.

        Accepts:
            User instance and iterable of CRM permission codenames.

        Returns:
            None.
        """

        permissions = Permission.objects.filter(
            content_type__app_label="crm",
            codename__in=codenames,
        )

        user.user_permissions.add(
            *permissions,
        )

        if hasattr(user, "_perm_cache"):
            del user._perm_cache

    def test_party_list_requires_view_permission(self):
        """
        Confirm that authenticated users still require crm.view_party.

        Accepts:
            The configured test users and party-list endpoint.

        Returns:
            None.
        """

        Party.objects.create(
            display_name="Visible CRM Party",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        unauthorised_client = APIClient()
        unauthorised_client.force_authenticate(
            self.no_permission_user,
        )

        response = unauthorised_client.get(
            reverse("party-list"),
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_quick_create_supports_ebay_supplier(self):
        """
        Confirm that an eBay seller becomes a traceable supplier party.

        Accepts:
            Valid quick-supplier request data.

        Returns:
            None.
        """

        response = self.client.post(
            reverse("party-quick-create"),
            {
                "display_name": "Best Marine Parts",
                "platform_name": "eBay",
                "seller_name": "best-marine-parts",
                "external_id": "seller-1048",
                "profile_url": ("https://www.ebay.com/usr/best-marine-parts"),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )

        party = Party.objects.get(
            pk=response.data["id"],
        )

        self.assertTrue(party.has_role(PartyRole.Role.SUPPLIER))

        self.assertTrue(party.is_traceable())

        source = party.sources.get()

        self.assertEqual(
            source.platform_name,
            "eBay",
        )

        self.assertEqual(
            source.external_id,
            "seller-1048",
        )

    def test_quick_supplier_requires_traceable_information(self):
        """
        Reject a quick supplier with no contact or source information.

        Accepts:
            Supplier name without traceability values.

        Returns:
            None.
        """

        response = self.client.post(
            reverse("party-quick-create"),
            {
                "display_name": "Unknown Supplier",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        self.assertEqual(
            response.data["code"],
            "supplier_source_required",
        )

    def test_duplicate_check_detects_matching_marketplace_id(self):
        """
        Confirm marketplace platform and seller ID produce an exact match.

        Accepts:
            Existing marketplace supplier and matching duplicate-check input.

        Returns:
            None.
        """

        party = Party.objects.create(
            display_name="Jumia Marine Store",
            entity_kind=Party.EntityKind.TRADING_NAME,
        )

        PartyRole.objects.create(
            party=party,
            role=PartyRole.Role.SUPPLIER,
        )

        PartySource.objects.create(
            party=party,
            source_type=(PartySource.SourceType.ONLINE_MARKETPLACE),
            platform_name="Jumia",
            seller_name="Jumia Marine Store",
            external_id="JM-8891",
            is_primary=True,
        )

        response = self.client.post(
            reverse("party-duplicate-check"),
            {
                "display_name": "Different Seller Name",
                "platform_name": "Jumia",
                "external_id": "JM-8891",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["count"],
            1,
        )

        self.assertEqual(
            response.data["results"][0]["classification"],
            "EXACT",
        )

    def test_deactivation_creates_history_and_audit_records(self):
        """
        Confirm a lifecycle operation preserves both CRM and central history.

        Accepts:
            Active party and deactivation reason.

        Returns:
            None.
        """

        party = Party.objects.create(
            display_name="Lifecycle Test Party",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        response = self.client.post(
            reverse(
                "party-deactivate",
                args=[
                    party.id,
                ],
            ),
            {
                "reason": ("No longer approved for new transactions."),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        party.refresh_from_db()

        self.assertEqual(
            party.status,
            Party.Status.INACTIVE,
        )

        history = PartyStatusHistory.objects.get(
            party=party,
        )

        self.assertEqual(
            history.previous_status,
            Party.Status.ACTIVE,
        )

        self.assertEqual(
            history.new_status,
            Party.Status.INACTIVE,
        )

        self.assertTrue(
            AuditLog.objects.filter(
                app_label="crm",
                resource="party",
                action="status_change",
                object_id=str(party.id),
            ).exists()
        )

    def test_merge_moves_roles_contacts_and_notes(self):
        """
        Confirm merging preserves useful child records on the target party.

        Accepts:
            Compatible source and target organisation parties.

        Returns:
            None.
        """

        source = Party.objects.create(
            display_name="Duplicate Supplier Limited",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        target = Party.objects.create(
            display_name="Correct Supplier Limited",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        PartyRole.objects.create(
            party=source,
            role=PartyRole.Role.SUPPLIER,
        )

        ContactMethod.objects.create(
            party=source,
            method_type=ContactMethod.MethodType.PHONE,
            value="+234 800 123 4567",
            is_primary=True,
        )

        PartyNote.objects.create(
            party=source,
            note_type=PartyNote.NoteType.PROCUREMENT,
            content="Supplier contacted through the old record.",
            author=self.user,
        )

        response = self.client.post(
            reverse(
                "party-merge",
                args=[
                    source.id,
                ],
            ),
            {
                "target_party": str(target.id),
                "reason": ("Both records represent the same supplier."),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        source.refresh_from_db()
        target.refresh_from_db()

        self.assertEqual(
            source.status,
            Party.Status.MERGED,
        )

        self.assertEqual(
            source.merged_into_id,
            target.id,
        )

        self.assertTrue(target.has_role(PartyRole.Role.SUPPLIER))

        self.assertEqual(
            target.contact_methods.count(),
            1,
        )

        self.assertEqual(
            target.notes.count(),
            1,
        )

        self.assertTrue(
            PartyMergeRecord.objects.filter(
                source_party=source,
                target_party=target,
            ).exists()
        )

    def test_confidential_notes_are_hidden_without_permission(self):
        """
        Confirm confidential notes are excluded from ordinary note readers.

        Accepts:
            One public and one confidential note.

        Returns:
            None.
        """

        party = Party.objects.create(
            display_name="Confidential Note Party",
            entity_kind=Party.EntityKind.ORGANISATION,
        )

        PartyNote.objects.create(
            party=party,
            content="Public operational note.",
            author=self.user,
        )

        PartyNote.objects.create(
            party=party,
            note_type=PartyNote.NoteType.CONFIDENTIAL,
            content="Restricted management information.",
            is_confidential=True,
            author=self.user,
        )

        response = self.client.get(
            reverse("party-note-list"),
            {
                "party": str(party.id),
            },
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["count"],
            1,
        )

        self.assertEqual(
            response.data["results"][0]["content"],
            "Public operational note.",
        )


def test_superuser_can_delete_an_unused_party(self):
    """
    Confirm that a superuser can permanently delete an unused CRM party.

    Returns:
        None.
    """

    party = Party.objects.create(
        display_name="Unused CRM Party",
        entity_kind=Party.EntityKind.ORGANISATION,
    )

    superuser_client = APIClient()

    superuser_client.force_authenticate(
        self.superuser,
    )

    response = superuser_client.delete(
        reverse(
            "party-detail",
            args=[
                party.id,
            ],
        )
    )

    self.assertEqual(
        response.status_code,
        status.HTTP_204_NO_CONTENT,
    )

    self.assertFalse(
        Party.objects.filter(
            pk=party.id,
        ).exists()
    )
