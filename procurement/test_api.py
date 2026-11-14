import base64
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from crm.models import Party, PartyRole
from crm.storage.base import DownloadedDocument, StoredDocument
from . import analytics
from .models import ItemActivity, ItemRequest, RequestedItem


class ProcurementItemRequestTests(APITestCase):
    def setUp(self):
        cache.clear()
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
                "change_requesteditem",
                "add_requesteditemimage",
                "delete_requesteditemimage",
                "add_supplierquote",
                "view_supplierquote",
                "download_supplierquote",
                "add_clientquotation",
                "view_clientquotation",
                "issue_clientquotation",
                "communicate_clientquotation",
                "change_clientquotation",
                "revise_clientquotation",
                "remove_clientquotation",
                "add_purchaseorder",
                "view_purchaseorder",
                "change_purchaseorderstatus",
                "download_purchaseorder",
                "add_purchase",
                "view_purchase",
                "change_purchaseitemstatus",
                "add_waybill",
                "view_waybill",
                "communicate_waybill",
                "add_itemverdict",
                "view_itemverdict",
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
        self.supplier = Party.objects.create(
            display_name="Trusted Supplier Limited",
            entity_kind=Party.EntityKind.ORGANISATION,
        )
        PartyRole.objects.create(
            party=self.supplier, role=PartyRole.Role.SUPPLIER, is_active=True
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

    def test_supplier_quote_multipart_items_and_empty_expiry(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        response = self.client.post(
            reverse("supplier-quote-list"),
            {
                "supplier": str(self.supplier.pk),
                "received_via": "EMAIL",
                "quotation_date": "2026-11-13",
                "expires_on": "",
                "items": json.dumps(
                    [
                        {"item": row["id"], "quantity": "1", "unit_price": "25000"}
                        for row in created.data["items"]
                    ]
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(len(response.data["items"]), 2)
        self.assertIsNone(response.data["expires_on"])

    def test_client_quotation_accepts_other_clients_items_without_discount(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        other = Party.objects.create(
            display_name="Other client", entity_kind=Party.EntityKind.ORGANISATION
        )
        PartyRole.objects.create(
            party=other, role=PartyRole.Role.CLIENT, is_active=True
        )
        response = self.client.post(
            reverse("client-quotation-list"),
            {
                "client": str(other.pk),
                "quotation_date": "2026-11-13",
                "expires_on": "2026-11-30",
                "items": [
                    {
                        "item": row["id"],
                        "description": row["name"],
                        "quantity": "1",
                        "unit_price": "1000",
                    }
                    for row in created.data["items"]
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(str(response.data["client"]), str(other.pk))
        self.assertEqual(len(response.data["items"]), 2)
        self.assertEqual(
            ItemRequest.objects.get(pk=created.data["id"]).client_id, self.crm_client.pk
        )

    def test_supplier_quote_links_each_item_activity(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        item_ids = [row["id"] for row in created.data["items"]]
        response = self.client.post(
            reverse("supplier-quote-list"),
            {
                "supplier": str(self.supplier.pk),
                "received_via": "EMAIL",
                "items": [
                    {
                        "item": item_id,
                        "quantity": "1",
                        "unit_price": "25000",
                        "tax_enabled": True,
                    }
                    for item_id in item_ids
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["items"]), 2)
        for item_id in item_ids:
            activity = ItemActivity.objects.get(
                item_id=item_id, title_code="SUPPLIER_QUOTE_ADDED"
            )
            self.assertEqual(activity.metadata["quote_id"], str(response.data["id"]))
            self.assertEqual(activity.created_by, self.user)

    def test_client_quotation_combines_items_and_allocates_daily_reference(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        payload = {
            "client": str(self.crm_client.pk),
            "quotation_date": "2026-11-13",
            "expires_on": "2026-11-30",
            "currency": "NGN",
            "tax_enabled": True,
            "tax_rate": "7.50",
            "discount_type": "PERCENT",
            "discount_value": "5",
            "items": [
                {
                    "item": row["id"],
                    "description": row["name"],
                    "quantity": row["quantity"],
                    "unit_price": "10000",
                }
                for row in created.data["items"]
            ],
        }
        first = self.client.post(
            reverse("client-quotation-list"), payload, format="json"
        )
        second = self.client.post(
            reverse("client-quotation-list"), payload, format="json"
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(first.data["reference"], "HOL-QTN-261113-01")
        self.assertEqual(second.data["reference"], "HOL-QTN-261113-02")
        with patch(
            "procurement.services.get_document_storage",
            return_value=(storage := FakePictureStorage()),
        ):
            issued = self.client.post(
                reverse("client-quotation-issue", args=[first.data["reference"]]),
                {},
                format="json",
            )
            downloaded = self.client.get(
                reverse("client-quotation-download", args=[first.data["reference"]])
            )
            printed = self.client.get(
                reverse(
                    "client-quotation-print-document", args=[first.data["reference"]]
                )
            )
            with patch(
                "procurement.services.send_document_email",
                return_value=SimpleNamespace(provider_message_id="provider-1"),
            ):
                emailed = self.client.post(
                    reverse("client-quotation-email", args=[first.data["reference"]]),
                    {
                        "request_key": "64c24cc0-a946-4c94-bdeb-b69ff4dbde50",
                        "destination": "procurement@acme.test",
                        "subject": "Quotation",
                        "message": "Please find the quotation attached.",
                    },
                    format="json",
                )
            revised_payload = {
                **payload,
                "items": [dict(row) for row in payload["items"]],
            }
            revised_payload["items"][0]["unit_price"] = "12000"
            revised = self.client.patch(
                reverse("client-quotation-detail", args=[first.data["reference"]]),
                revised_payload,
                format="json",
            )
            reissued = self.client.post(
                reverse("client-quotation-issue", args=[first.data["reference"]]),
                {},
                format="json",
            )
            removed = self.client.post(
                reverse(
                    "client-quotation-remove-document",
                    args=[first.data["reference"]],
                ),
                {},
                format="json",
            )
        self.assertEqual(issued.status_code, status.HTTP_200_OK)
        self.assertEqual(issued.data["status"], "ISSUED")
        self.assertTrue(issued.data["document_available"])
        self.assertEqual(downloaded.status_code, status.HTTP_200_OK)
        self.assertEqual(printed.status_code, status.HTTP_200_OK)
        self.assertEqual(emailed.status_code, status.HTTP_200_OK)
        self.assertEqual(revised.status_code, status.HTTP_200_OK, revised.data)
        self.assertEqual(revised.data["version"], 2)
        self.assertEqual(revised.data["status"], "DRAFT")
        self.assertEqual(reissued.status_code, status.HTTP_200_OK, reissued.data)
        self.assertEqual(len(reissued.data["revisions"]), 2)
        self.assertEqual(removed.status_code, status.HTTP_200_OK, removed.data)
        self.assertFalse(removed.data["document_available"])
        self.assertEqual(
            removed.data["revisions"][0]["removed_by_name"], self.user.email
        )
        self.assertTrue(storage.content.startswith(b"%PDF"))
        for row in created.data["items"]:
            self.assertTrue(
                ItemActivity.objects.filter(
                    item_id=row["id"], title_code="CLIENT_QUOTATION_ISSUED"
                ).exists()
            )
            activities = ItemActivity.objects.filter(item_id=row["id"])
            self.assertTrue(
                activities.filter(title_code="CLIENT_QUOTATION_DOWNLOAD").exists()
            )
            self.assertTrue(
                activities.filter(title_code="CLIENT_QUOTATION_PRINT").exists()
            )
            self.assertTrue(
                activities.filter(title_code="CLIENT_QUOTATION_EMAIL").exists()
            )
            self.assertTrue(
                activities.filter(title_code="CLIENT_QUOTATION_REVISED").exists()
            )
            self.assertTrue(
                activities.filter(
                    title_code="CLIENT_QUOTATION_DOCUMENT_REMOVED"
                ).exists()
            )
            issue_description = (
                activities.filter(title_code="CLIENT_QUOTATION_ISSUED")
                .order_by("occurred_at")
                .first()
                .description
            )
            self.assertIn("subtotal", issue_description)
            self.assertIn("VAT 7.5%", issue_description)
            self.assertIn("Draft to Issued", issue_description)

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

    def test_item_edit_records_changed_fields_and_rejects_noop(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        item_id = created.data["items"][0]["id"]

        edited = self.client.patch(
            reverse("requested-item-edit", args=[item_id]),
            {
                "brand": "Danfoss",
                "quantity": "3.0000",
                "specification": "Marine-rated, 250 bar",
            },
            format="json",
        )

        self.assertEqual(edited.status_code, status.HTTP_200_OK)
        activity = ItemActivity.objects.get(item_id=item_id, title_code="ITEM_UPDATED")
        self.assertEqual(
            set(activity.metadata["changes"]),
            {"brand", "quantity", "specification"},
        )
        self.assertIn(self.user.email, activity.description)
        for label in ["brand", "quantity", "specification"]:
            self.assertIn(label, activity.description)

        unchanged = self.client.patch(
            reverse("requested-item-edit", args=[item_id]),
            {"brand": "Danfoss"},
            format="json",
        )
        self.assertEqual(unchanged.status_code, status.HTTP_400_BAD_REQUEST)

    def test_completed_item_cannot_be_edited(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        item_id = created.data["items"][0]["id"]
        closed = self.client.post(
            reverse("requested-item-change-status", args=[item_id]),
            {"status": "CLOSED", "reason": "Client cancelled the request."},
            format="json",
        )
        self.assertEqual(closed.status_code, status.HTTP_200_OK)

        edited = self.client.patch(
            reverse("requested-item-edit", args=[item_id]),
            {"brand": "Danfoss"},
            format="json",
        )

        self.assertEqual(edited.status_code, status.HTTP_400_BAD_REQUEST)

    def test_picture_add_and_remove_are_attributed_activities(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        item_id = created.data["items"][0]["id"]
        storage = FakePictureStorage()
        pictures = [picture_file("sample-one.png"), picture_file("sample-two.png")]
        with patch("procurement.services.get_document_storage", return_value=storage):
            added = self.client.post(
                reverse("requested-item-images", args=[item_id]),
                {"files": pictures},
                format="multipart",
            )
            self.assertEqual(added.status_code, status.HTTP_201_CREATED)
            self.assertEqual(len(added.data), 2)
            activity = ItemActivity.objects.get(
                item_id=item_id, title_code="PICTURES_ADDED"
            )
            self.assertIn("added 2 picture samples", activity.description)

            removed = self.client.delete(
                reverse("requested-item-images", args=[item_id]),
                {"images": [row["id"] for row in added.data]},
                format="json",
            )
            self.assertEqual(removed.status_code, status.HTTP_200_OK)
            activity = ItemActivity.objects.get(
                item_id=item_id, title_code="PICTURES_REMOVED"
            )
            self.assertIn("removed 2 picture samples", activity.description)
            self.assertEqual(len(storage.deleted), 2)

    def test_picture_batches_append_to_existing_image_and_after_removal(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        item_id = created.data["items"][0]["id"]
        item = RequestedItem.objects.get(pk=item_id)
        url = reverse("requested-item-images", args=[item_id])
        storage = FakePictureStorage()
        with patch("procurement.services.get_document_storage", return_value=storage):
            first = self.client.post(
                url, {"files": [picture_file("original.png")]}, format="multipart"
            )
            self.assertEqual(first.status_code, status.HTTP_201_CREATED)
            original_id = first.data[0]["id"]

            for attempt in range(2):
                added = self.client.post(
                    url,
                    {"files": [picture_file(f"{attempt}-{i}.png") for i in range(2)]},
                    format="multipart",
                )
                self.assertEqual(added.status_code, status.HTTP_201_CREATED)
                self.assertEqual(len(added.data), 2)
                self.assertEqual(
                    list(
                        item.images.order_by("position").values_list(
                            "position", flat=True
                        )
                    ),
                    [0, 1, 2],
                )
                self.assertEqual(str(item.images.get(position=0).pk), original_id)
                removed = self.client.delete(
                    url, {"images": [row["id"] for row in added.data]}, format="json"
                )
                self.assertEqual(removed.status_code, status.HTTP_200_OK)

        self.assertEqual(item.images.count(), 1)
        self.assertEqual(item.activities.filter(title_code="PICTURES_ADDED").count(), 3)

    def test_picture_count_and_size_limits_are_enforced(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        item_id = created.data["items"][0]["id"]
        storage = FakePictureStorage()
        with patch("procurement.services.get_document_storage", return_value=storage):
            too_many = self.client.post(
                reverse("requested-item-images", args=[item_id]),
                {"files": [picture_file(f"sample-{index}.png") for index in range(6)]},
                format="multipart",
            )
            self.assertEqual(too_many.status_code, status.HTTP_400_BAD_REQUEST)

            oversized = picture_file("oversized.png", size=5 * 1024 * 1024 + 1)
            too_large = self.client.post(
                reverse("requested-item-images", args=[item_id]),
                {"files": [oversized]},
                format="multipart",
            )
            self.assertEqual(too_large.status_code, status.HTTP_400_BAD_REQUEST)

            closed = self.client.post(
                reverse("requested-item-change-status", args=[item_id]),
                {"status": "CLOSED", "reason": "Client cancelled the request."},
                format="json",
            )
            self.assertEqual(closed.status_code, status.HTTP_200_OK)
            completed_upload = self.client.post(
                reverse("requested-item-images", args=[item_id]),
                {"files": [picture_file("late.png")]},
                format="multipart",
            )
            self.assertEqual(completed_upload.status_code, status.HTTP_400_BAD_REQUEST)

    def test_analytics_returns_six_cards_charts_and_cached_response(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        item_id = created.data["items"][0]["id"]
        delivered = self.client.post(
            reverse("requested-item-change-status", args=[item_id]),
            {"status": "DELIVERED", "reason": "Accepted by the client."},
            format="json",
        )
        self.assertEqual(delivered.status_code, status.HTTP_200_OK)

        first = self.client.get(
            reverse("procurement-analytics-summary"), {"range": "year"}
        )
        second = self.client.get(
            reverse("procurement-analytics-summary"), {"range": "year"}
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(first.data["cards"]),
            {
                "received",
                "active",
                "suspended",
                "closed",
                "delivered",
                "not_delivered",
            },
        )
        self.assertEqual(first.data["cards"]["received"], 2)
        self.assertEqual(first.data["cards"]["active"], 1)
        self.assertEqual(first.data["cards"]["delivered"], 1)
        self.assertEqual(len(first.data["delivered_by_month"]), 12)
        self.assertEqual(first.data["received_by_client"][0]["count"], 2)
        self.assertFalse(first.data["cache"]["hit"])
        self.assertTrue(second.data["cache"]["hit"])

    def test_purchase_order_accepts_any_tracked_item_and_records_activity(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        response = self.client.post(
            reverse("purchase-order-list"),
            {
                "client": str(self.crm_client.pk),
                "client_reference": "CLIENT-PO-44",
                "urgency": "URGENT",
                "items": [
                    {
                        "item": created.data["items"][0]["id"],
                        "quantity": "5",
                        "unit_price": "12500",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertRegex(response.data["reference"], r"^HOL-PO-\d{6}-01$")
        self.assertEqual(response.data["status"], "QUEUED")
        self.assertTrue(
            ItemActivity.objects.filter(
                item_id=created.data["items"][0]["id"],
                title_code="PURCHASE_ORDER_RECEIVED",
            ).exists()
        )

    def test_purchase_order_document_is_limited_to_five_mb(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        with patch(
            "procurement.services.get_document_storage",
            return_value=FakePictureStorage(),
        ):
            response = self.client.post(
                reverse("purchase-order-list"),
                {
                    "client": str(self.crm_client.pk),
                    "client_reference": "CLIENT-PO-LARGE",
                    "items": json.dumps(
                        [{"item": created.data["items"][0]["id"], "quantity": "1"}]
                    ),
                    "attachment": picture_file("po.png", 5 * 1024 * 1024 + 1),
                },
                format="multipart",
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("5 MB", str(response.data))

    def test_po_reason_is_optional_and_purchase_item_status_is_recorded(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        item = created.data["items"][0]
        po = self.client.post(
            reverse("purchase-order-list"),
            {
                "client": str(self.crm_client.pk),
                "client_reference": "CLIENT-PO-STATUS",
                "items": [{"item": item["id"], "quantity": "1"}],
            },
            format="json",
        )
        changed = self.client.post(
            reverse("purchase-order-status", args=[po.data["id"]]),
            {"status": "ACTIVE", "reason": ""},
            format="json",
        )
        self.assertEqual(changed.status_code, status.HTTP_200_OK, changed.data)
        purchase = self.client.post(
            reverse("purchase-list"),
            {
                "purchased_from": str(self.supplier.pk),
                "items": [{"item": item["id"], "quantity": "1", "unit_price": "500"}],
            },
            format="json",
        )
        line = purchase.data["items"][0]
        self.assertEqual(line["status"], "PAID")
        updated = self.client.post(
            reverse("purchase-item-status", args=[purchase.data["id"], line["id"]]),
            {"status": "TRANSIT", "reason": ""},
            format="json",
        )
        self.assertEqual(updated.status_code, status.HTTP_200_OK, updated.data)
        self.assertEqual(updated.data["status"], "TRANSIT")

    def test_waybill_is_stored_and_verdicts_are_limited_by_waybilled_quantity(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        with patch(
            "procurement.services.get_document_storage",
            return_value=FakePictureStorage(),
        ):
            waybill = self.client.post(
                reverse("waybill-list"),
                {
                    "client": str(self.crm_client.pk),
                    "delivery_personnel": "Tolu Ade",
                    "delivered_to": "Acme Stores",
                    "delivery_address": "Ibadan",
                    "items": [
                        {
                            "item": created.data["items"][0]["id"],
                            "quantity": "2",
                        }
                    ],
                },
                format="json",
            )
        self.assertEqual(waybill.status_code, status.HTTP_201_CREATED, waybill.data)
        self.assertTrue(waybill.data["document_available"])
        line_id = waybill.data["items"][0]["id"]
        accepted = self.client.post(
            reverse("item-verdict-list"),
            {
                "waybill_item": line_id,
                "verdict": "ACCEPTED",
                "quantity": "1",
                "client_recipient": "Bola",
            },
            format="json",
        )
        self.assertEqual(accepted.status_code, status.HTTP_201_CREATED, accepted.data)
        too_much = self.client.post(
            reverse("item-verdict-list"),
            {
                "waybill_item": line_id,
                "verdict": "REJECTED",
                "quantity": "2",
                "reason": "Wrong specification",
            },
            format="json",
        )
        self.assertEqual(too_much.status_code, status.HTTP_400_BAD_REQUEST)

    def test_accepted_verdict_delivers_item_and_fulfils_linked_po(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        item = created.data["items"][0]
        po = self.client.post(
            reverse("purchase-order-list"),
            {
                "client": str(self.crm_client.pk),
                "client_reference": "CLIENT-PO-AUTO",
                "items": [{"item": item["id"], "quantity": "2"}],
            },
            format="json",
        )
        with patch(
            "procurement.services.get_document_storage",
            return_value=FakePictureStorage(),
        ):
            waybill = self.client.post(
                reverse("waybill-list"),
                {
                    "client": str(self.crm_client.pk),
                    "purchase_order": po.data["id"],
                    "delivery_personnel": "Tolu Ade",
                    "delivered_to": self.crm_client.display_name,
                    "delivery_address": "Ibadan",
                    "items": [{"item": item["id"], "quantity": "2"}],
                },
                format="json",
            )
        verdict = self.client.post(
            reverse("item-verdict-list"),
            {
                "waybill_item": waybill.data["items"][0]["id"],
                "verdict": "ACCEPTED",
                "quantity": "2",
            },
            format="json",
        )
        self.assertEqual(verdict.status_code, status.HTTP_201_CREATED, verdict.data)
        self.assertEqual(
            RequestedItem.objects.get(pk=item["id"]).status,
            RequestedItem.Status.DELIVERED,
        )
        po_detail = self.client.get(
            reverse("purchase-order-detail", args=[po.data["id"]])
        )
        self.assertEqual(po_detail.data["status"], "FULFILLED")

    def test_rejected_verdict_sets_linked_po_attention_required(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        item = created.data["items"][0]
        po = self.client.post(
            reverse("purchase-order-list"),
            {
                "client": str(self.crm_client.pk),
                "client_reference": "CLIENT-PO-REJECT",
                "items": [{"item": item["id"], "quantity": "2"}],
            },
            format="json",
        )
        with patch(
            "procurement.services.get_document_storage",
            return_value=FakePictureStorage(),
        ):
            waybill = self.client.post(
                reverse("waybill-list"),
                {
                    "client": str(self.crm_client.pk),
                    "purchase_order": po.data["id"],
                    "delivery_personnel": "Tolu Ade",
                    "delivered_to": self.crm_client.display_name,
                    "delivery_address": "Ibadan",
                    "items": [{"item": item["id"], "quantity": "2"}],
                },
                format="json",
            )
        rejected = self.client.post(
            reverse("item-verdict-list"),
            {
                "waybill_item": waybill.data["items"][0]["id"],
                "verdict": "REJECTED",
                "quantity": "1",
                "reason": "Wrong specification",
            },
            format="json",
        )
        self.assertEqual(rejected.status_code, status.HTTP_201_CREATED, rejected.data)
        po_detail = self.client.get(
            reverse("purchase-order-detail", args=[po.data["id"]])
        )
        self.assertEqual(po_detail.data["status"], "ATTENTION_REQUIRED")

    def test_analytics_cache_regenerates_after_activity_threshold(self):
        created = self.client.post(
            reverse("item-request-list"), self.request_payload(), format="json"
        )
        item_id = created.data["items"][0]["id"]
        thresholds = {"week": 1, "month": 1, "quarter": 1, "year": 1}
        with self.settings(SUMMARY_CACHE_THRESHOLDS=thresholds):
            first = self.client.get(
                reverse("procurement-analytics-summary"), {"range": "year"}
            )
            self.assertFalse(first.data["cache"]["hit"])
            comment = self.client.post(
                reverse("requested-item-activities", args=[item_id]),
                {
                    "category": "COMMENT",
                    "title_code": "COMMENT",
                    "description": "Fresh activity.",
                },
                format="json",
            )
            self.assertEqual(comment.status_code, status.HTTP_201_CREATED)
            refreshed = self.client.get(
                reverse("procurement-analytics-summary"), {"range": "year"}
            )
            self.assertFalse(refreshed.data["cache"]["hit"])

    @patch(
        "procurement.analytics.get_cached_ai_analytics",
        side_effect=analytics.AIConfigurationError,
    )
    def test_analytics_ai_missing_configuration_returns_503(self, mocked):
        response = self.client.get(
            reverse("procurement-analytics-ai-insight"), {"range": "year"}
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], "ai_insight_unavailable")
        mocked.assert_called_once_with("year")

    @patch("procurement.analytics.get_cached_ai_analytics", side_effect=RuntimeError)
    def test_analytics_ai_provider_runtime_failure_returns_502(self, mocked):
        response = self.client.get(
            reverse("procurement-analytics-ai-insight"), {"range": "year"}
        )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data["code"], "ai_insight_failed")
        mocked.assert_called_once_with("year")

    @patch("procurement.analytics.genai.Client")
    def test_analytics_ai_generation_uses_live_client(self, client_class):
        client = client_class.return_value
        client.models.generate_content.return_value.text = json.dumps(
            {
                "summary": "Delivery activity is stable.",
                "observations": [],
                "delivery_concerns": [],
                "demand_patterns": [],
                "recommendations": [],
                "disclaimer": "Aggregate operational analysis only.",
            }
        )

        result = analytics.generate_ai_analytics("year")

        self.assertEqual(result["insight"]["summary"], "Delivery activity is stable.")
        client.models.generate_content.assert_called_once()


def picture_file(name, size=None):
    content = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    if size and size > len(content):
        content += b"\0" * (size - len(content))
    return SimpleUploadedFile(name, content, content_type="image/png")


class FakePictureStorage:
    def __init__(self):
        self.deleted = []
        self.content = b""

    def upload(self, *, document_id, filename, **kwargs):
        file_object = kwargs.get("file_object")
        if file_object:
            self.content = file_object.read()
        path = f"fake/{document_id}/{filename}"
        return StoredDocument(
            provider="supabase", external_file_id=path, storage_path=path
        )

    def delete(self, *, external_file_id, storage_path):
        self.deleted.append(external_file_id)

    def download(self, *, filename, mime_type, **kwargs):
        from io import BytesIO

        return DownloadedDocument(
            file_object=BytesIO(self.content),
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(self.content),
        )
