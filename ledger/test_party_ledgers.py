from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission, Group
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.serializers import format_permission
from crm.models import Party, PartyRole
from .models import PartyLedgerSelection, CashBookEntry, CashBookJournal


class PartyLedgerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ledger-reader", email="ledger-reader@example.test", is_staff=True
        )
        self.client = APIClient()
        self.grant("view_salesledger", "view_purchaseledger")
        self.buyer = self.party("Alpha client", "CLIENT")
        self.supplier = self.party("Beta supplier", "SUPPLIER")
        self.both = self.party("Both roles", "CLIENT", "SUPPLIER")

    def party(self, name, *roles):
        party = Party.objects.create(display_name=name)
        for role in roles:
            PartyRole.objects.create(party=party, role=role)
        return party

    def grant(self, *codes):
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="ledger", codename__in=codes
            )
        )
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_authenticate(self.user)

    def select(self, kind, party):
        return self.client.put(
            f"/ledger/party-ledgers/{kind}/",
            {"party_id": str(party.pk) if party else None},
            format="json",
        )

    def test_separate_permissions_for_all_operations(self):
        self.grant("view_salesledger")
        self.assertEqual(
            self.client.get("/ledger/party-ledgers/sales/").status_code, 200
        )
        self.assertEqual(self.select("sales", self.buyer).status_code, 200)
        self.assertEqual(
            self.client.get("/ledger/party-ledgers/purchase/").status_code, 403
        )
        self.assertEqual(
            self.client.get("/ledger/party-ledgers/purchase/parties/").status_code, 403
        )
        self.assertEqual(self.select("purchase", self.supplier).status_code, 403)
        self.grant()
        self.assertEqual(
            self.client.get("/ledger/party-ledgers/sales/parties/").status_code, 403
        )
        self.assertEqual(self.select("sales", self.buyer).status_code, 403)
        permission = Permission.objects.get(
            content_type__app_label="ledger", codename="view_salesledger"
        )
        self.assertEqual(format_permission(permission), "ledger.salesledger.view")

    def test_lookup_role_filter_search_pagination_and_minimal_fields(self):
        response = self.client.get(
            "/ledger/party-ledgers/sales/parties/?search=ALPHA&page_size=1"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["id"], str(self.buyer.pk))
        self.assertEqual(set(row), {"id", "display_name", "entity_kind", "status"})
        result = self.client.get("/ledger/party-ledgers/purchase/parties/").data
        self.assertEqual(
            {row["id"] for row in result["results"]},
            {str(self.supplier.pk), str(self.both.pk)},
        )
        result = self.client.get(
            "/ledger/party-ledgers/sales/parties/?page_size=1"
        ).data
        self.assertEqual(result["count"], 2)
        self.assertIsNotNone(result["next"])

    def test_selection_per_user_and_ledger_and_clear(self):
        self.assertIsNone(
            self.client.get("/ledger/party-ledgers/sales/").data["selected_party"]
        )
        self.select("sales", self.buyer)
        self.select("purchase", self.supplier)
        self.select("sales", self.buyer)
        self.assertEqual(PartyLedgerSelection.objects.count(), 2)
        self.assertEqual(
            self.client.get("/ledger/party-ledgers/sales/").data["selected_party"][
                "id"
            ],
            str(self.buyer.pk),
        )
        other = get_user_model().objects.create_user(
            username="other", email="other@example.test", is_staff=True
        )
        other.user_permissions.set(self.user.user_permissions.all())
        self.client.force_authenticate(other)
        self.assertIsNone(
            self.client.get("/ledger/party-ledgers/sales/").data["selected_party"]
        )
        self.select("sales", self.both)
        self.client.force_authenticate(self.user)
        self.assertEqual(
            self.client.get("/ledger/party-ledgers/sales/").data["selected_party"][
                "id"
            ],
            str(self.buyer.pk),
        )
        self.assertEqual(self.select("sales", None).status_code, 200)
        self.assertIsNone(
            self.client.get("/ledger/party-ledgers/sales/").data["selected_party"]
        )
        self.assertEqual(
            self.client.get("/ledger/party-ledgers/purchase/").data["selected_party"][
                "id"
            ],
            str(self.supplier.pk),
        )
        self.assertFalse(CashBookEntry.objects.exists())
        self.assertFalse(CashBookJournal.objects.exists())

    def test_ineligible_and_stale_selection(self):
        self.assertEqual(self.select("sales", self.supplier).status_code, 404)
        self.select("sales", self.buyer)
        self.buyer.is_archived = True
        self.buyer.save()
        self.assertIsNone(
            self.client.get("/ledger/party-ledgers/sales/").data["selected_party"]
        )
        self.assertEqual(self.select("sales", self.buyer).status_code, 404)
        PartyRole.objects.filter(party=self.both, role="CLIENT").update(is_active=False)
        self.assertEqual(self.select("sales", self.both).status_code, 404)
        result = self.client.get("/ledger/party-ledgers/sales/parties/").data
        self.assertEqual(result["count"], 0)
        self.assertEqual(self.select("purchase", self.both).status_code, 200)

    def test_validation_and_unsupported_methods(self):
        self.assertEqual(
            self.client.put(
                "/ledger/party-ledgers/sales/", {"party_id": "bad"}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.put(
                "/ledger/party-ledgers/sales/", {}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.get("/ledger/party-ledgers/other/").status_code, 404
        )
        self.assertEqual(
            self.client.post(
                "/ledger/party-ledgers/sales/", {}, format="json"
            ).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(
                "/ledger/party-ledgers/sales/parties/?search=" + "x" * 101
            ).status_code,
            400,
        )

    def test_staff_and_authentication_and_role_grants(self):
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(
            self.client.get("/ledger/party-ledgers/sales/").status_code, 403
        )
        self.client.force_authenticate(None)
        self.assertIn(
            self.client.get("/ledger/party-ledgers/sales/").status_code, [401, 403]
        )
        self.user.is_staff = True
        self.user.save()
        self.grant()
        group = Group.objects.create(name="Ledger viewers")
        group.permissions.add(
            Permission.objects.get(
                content_type__app_label="ledger", codename="view_salesledger"
            )
        )
        self.user.groups.add(group)
        self.client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
        self.assertEqual(
            self.client.get("/ledger/party-ledgers/sales/").status_code, 200
        )
