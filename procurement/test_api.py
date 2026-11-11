from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from crm.models import Party, PartyRole
from .models import ItemActivity, ItemRequest, RequestedItem


class ProcurementItemRequestTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="procurement.tester",
            email="procurement.tester@holansl.com",
            password="TestPassword123!",
            is_staff=True,
        )
        permissions = Permission.objects.filter(
            content_type__app_label="procurement",
            codename__in=[
                "add_itemrequest",
                "view_itemrequest",
                "view_requesteditem",
                "view_itemtracker",
                "add_itemactivity",
                "change_itemstatus",
            ],
        )
        self.user.user_permissions.add(*permissions)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.crm_client = Party.objects.create(
            display_name="Acme Marine Limited",
            entity_kind=Party.EntityKind.ORGANISATION,
        )
        PartyRole.objects.create(
            party=self.crm_client,
            role=PartyRole.Role.CLIENT,
            is_active=True,
        )

    def request_payload(self):
        return {
            "client": str(self.crm_client.pk),
            "received_via": "WHATSAPP",
            "items": [
                {
                    "name": "Hydraulic pump",
                    "description": "Pump for deck machinery",
                    "quantity": "2.0000",
                    "unit": "pieces",
                    "urgency": "HIGH",
                },
                {
                    "name": "Pressure gauge",
                    "description": "Stainless pressure gauge",
                    "quantity": "1.0000",
                    "unit": "piece",
                },
            ],
        }

    def test_each_batch_item_gets_unique_code_and_tracker(self):
        response = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        request = ItemRequest.objects.get()
        items = list(request.items.order_by("code"))
        self.assertEqual(len(items), 2)
        self.assertRegex(items[0].code, r"^Item-ACM-\d{10}-00$")
        self.assertRegex(items[1].code, r"^Item-ACM-\d{10}-01$")
        self.assertNotEqual(items[0].code, items[1].code)
        for item in items:
            activity = item.activities.get()
            self.assertEqual(activity.title_code, "REQUEST_RECEIVED")
            self.assertEqual(activity.status_result, RequestedItem.Status.OPEN)

    def test_daily_sequence_continues_across_request_batches(self):
        first = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        payload = self.request_payload()
        payload["items"] = [payload["items"][0]]
        second = self.client.post(reverse("item-request-list"), payload, format="json")

        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        newest = RequestedItem.objects.order_by("-created_at").first()
        self.assertTrue(newest.code.endswith("-02"))

    def test_supplier_only_party_cannot_receive_item_request(self):
        supplier = Party.objects.create(
            display_name="Supplier Only",
            entity_kind=Party.EntityKind.ORGANISATION,
        )
        PartyRole.objects.create(
            party=supplier,
            role=PartyRole.Role.SUPPLIER,
            is_active=True,
        )
        payload = self.request_payload()
        payload["client"] = str(supplier.pk)

        response = self.client.post(
            reverse("item-request-list"), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ItemRequest.objects.count(), 0)

    def test_custom_channel_requires_description(self):
        payload = self.request_payload()
        payload["received_via"] = "CUSTOM"

        response = self.client.post(
            reverse("item-request-list"), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("received_via_custom", response.data)

    def test_activity_is_attributed_and_terminal_item_rejects_more_activity(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        item_id = created.data["items"][0]["id"]
        comment = self.client.post(
            reverse("requested-item-activities", args=[item_id]),
            {
                "category": "COMMENT",
                "title_code": "GENERAL_UPDATE",
                "description": "Client confirmed the specification.",
            },
            format="json",
        )
        self.assertEqual(comment.status_code, status.HTTP_201_CREATED)
        self.assertEqual(comment.data["title"], "Comment")
        self.assertEqual(
            comment.data["created_by_name"],
            self.user.get_full_name() or self.user.email,
        )

        closed = self.client.post(
            reverse("requested-item-change-status", args=[item_id]),
            {"status": "CLOSED", "reason": "Client cancelled the request."},
            format="json",
        )
        self.assertEqual(closed.status_code, status.HTTP_200_OK)
        rejected = self.client.post(
            reverse("requested-item-activities", args=[item_id]),
            {
                "category": "ACTIVITY",
                "title_code": "SOURCING",
                "description": "Should not be accepted.",
            },
            format="json",
        )
        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ItemActivity.objects.filter(item_id=item_id).count(), 3)
