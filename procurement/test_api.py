import base64
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from crm.models import Party, PartyRole
from crm.storage.base import StoredDocument
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

    def upload(self, *, document_id, filename, **kwargs):
        path = f"fake/{document_id}/{filename}"
        return StoredDocument(
            provider="supabase", external_file_id=path, storage_path=path
        )

    def delete(self, *, external_file_id, storage_path):
        self.deleted.append(external_file_id)
