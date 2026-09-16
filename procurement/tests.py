"""Isolated procurement lifecycle, rejection, scope and storage regression tests."""

from datetime import timedelta
from decimal import Decimal as D
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction, connection
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError

from crm.models import Party, PartyRole
from crm.storage import StoredDocument, DownloadedDocument, DocumentStorageError
from . import models as m, services as s, tracking


class ProcurementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from accounts.models import Department

        cls.procurement_department, _ = Department.objects.get_or_create(
            name="Procurement", defaults={"code": "PROC"}
        )
        cls.user = get_user_model().objects.create_superuser(
            username="procurement-test",
            email="procurement@example.invalid",
            password="local-test-only",
        )
        cls.client_party = Party.objects.create(
            display_name="Procurement test client", entity_kind="INDIVIDUAL"
        )
        PartyRole.objects.create(party=cls.client_party, role="CLIENT")
        cls.supplier = Party.objects.create(display_name="Supplier one")
        cls.supplier2 = Party.objects.create(display_name="Supplier two")
        for supplier in [cls.supplier, cls.supplier2]:
            PartyRole.objects.create(party=supplier, role="SUPPLIER")

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.request = s.create_request(
            self.user,
            {
                "client": self.client_party,
                "contact": self.client_party,
                "items": [{"description": "Pump", "quantity": D(10), "unit": "each"}],
            },
        )
        s.submit_request(self.user, self.request)
        self.item = self.request.items.get()

    def test_registration_defaults_and_immediate_received_milestone(self):
        self.user.is_superuser = False
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])
        request = s.create_request(
            self.user,
            {
                "client": self.client_party,
                "items": [
                    {"description": "Registered item", "quantity": D(2), "unit": "each"}
                ],
            },
        )
        self.assertEqual(request.owner_id, self.user.pk)
        self.assertEqual(request.department_id, self.procurement_department.pk)
        self.assertIsNone(request.submitted_at)
        item = request.items.get()
        self.assertEqual(item.events.filter(kind="REQUEST_RECEIVED").count(), 1)
        milestone = tracking.tracker(item)["milestones"][0]
        self.assertEqual(milestone["state"], "RECORDED")
        self.assertEqual(
            milestone["latest_event"]["created_by_name"], item.created_by_name
        )

    def test_submit_does_not_duplicate_registration_event(self):
        self.assertEqual(self.item.events.filter(kind="REQUEST_RECEIVED").count(), 1)

    def test_assignment_fields_cannot_be_supplied_by_form_api(self):
        response = self.post(
            "requests/",
            {
                "client": str(self.client_party.pk),
                "owner": str(self.user.pk),
                "department": str(self.procurement_department.pk),
                "items": [{"description": "Item", "quantity": "1", "unit": "each"}],
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_legacy_registered_draft_is_received_without_rewriting_audit(self):
        request = s.make(
            m.ProcurementRequest,
            self.user,
            client=self.client_party,
            client_name=str(self.client_party),
            owner=self.user,
            department=self.procurement_department,
        )
        item = s.make(
            m.RequestItem,
            self.user,
            request=request,
            description="Old draft",
            quantity=D(1),
            unit="each",
        )
        milestone = tracking.tracker(item)["milestones"][0]
        self.assertEqual(milestone["state"], "RECORDED")
        self.assertEqual(
            milestone["latest_event"]["created_by_name"], item.created_by_name
        )
        self.assertEqual(item.events.count(), 0)

    def test_milestone_history_filter_and_supplier_details(self):
        self.offer()
        response = self.api.get(
            f"/procurement/events/?item={self.item.pk}&milestone=ITEM_SOURCED"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["kind"], "ITEM_SOURCED")
        self.assertEqual(
            response.data["results"][0]["details"]["supplier"], "Supplier one"
        )

    def test_draft_item_edit_updates_and_audits_with_version_guard(self):
        request = s.create_request(
            self.user,
            {
                "client": self.client_party,
                "items": [
                    {"description": "Draft pump", "quantity": D(1), "unit": "each"}
                ],
            },
        )
        item = request.items.get()
        payload = {
            "description": "Corrected pump",
            "quantity": "2",
            "unit": "each",
            "specification": "Updated spec",
            "expected_version": request.version,
        }
        response = self.api.patch(
            f"/procurement/items/{item.pk}/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        self.assertEqual(response.status_code, 200, response.data)
        item.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(item.quantity, D(2))
        self.assertEqual(item.description, "Corrected pump")
        self.assertEqual(request.version, payload["expected_version"] + 1)
        self.assertIn("Draft pump", item.events.get(kind="DRAFT_ITEM_UPDATED").reason)
        stale = self.api.patch(
            f"/procurement/items/{item.pk}/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        self.assertEqual(stale.status_code, 400)
        with self.assertRaises(ValidationError):
            s.submit_request(self.user, request, payload["expected_version"])

    def test_submitted_item_rejects_direct_edits(self):
        response = self.api.patch(
            f"/procurement/items/{self.item.pk}/",
            {
                "description": "Changed",
                "quantity": "3",
                "unit": "each",
                "expected_version": self.request.version,
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        self.assertEqual(response.status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.description, "Pump")

    def test_item_edit_requires_assigned_change_permission(self):
        with patch.object(
            self.user,
            "has_perm",
            side_effect=lambda permission: (
                permission != "procurement.change_requestitem"
            ),
        ):
            response = self.api.patch(
                f"/procurement/items/{self.item.pk}/",
                {
                    "description": "Changed",
                    "quantity": "3",
                    "unit": "each",
                    "expected_version": self.request.version,
                },
                format="json",
                HTTP_IDEMPOTENCY_KEY=str(uuid4()),
            )
        self.assertEqual(response.status_code, 403)

    def doc(self, category, request=None):
        return s.make(
            m.ProcurementDocument,
            self.user,
            request=request or self.request,
            category=category,
            filename="evidence.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            checksum="0" * 64,
            storage_provider="supabase",
            external_file_id="private-key",
            storage_path="private-path",
        )

    def offer(self, supplier=None, attempt=None):
        return s.create_offer(
            self.user,
            {
                "request": self.request,
                "supplier": supplier or self.supplier,
                "currency": "NGN",
                "lines": [
                    {
                        "item": self.item,
                        "attempt": attempt,
                        "quantity": D(10),
                        "unit_price": D(100),
                    }
                ],
            },
        ).lines.get()

    def po(self):
        offer = self.offer()
        revision = s.create_quote(
            self.user,
            {
                "request": self.request,
                "currency": "NGN",
                "document": self.doc("QUOTATION"),
                "lines": [
                    {
                        "item": self.item,
                        "supplier_offer": offer,
                        "description": "Pump",
                        "quantity": D(10),
                        "unit_price": D(150),
                    }
                ],
            },
        )
        with patch("procurement.document_registry.require_ledger_approval"):
            s.issue_quote(
                self.user,
                revision,
                {
                    "recipient": self.client_party,
                    "method": "email",
                    "evidence": "Recorded sending reference",
                },
            )
        po = s.create_po(
            self.user,
            {
                "request": self.request,
                "reference": "CLIENT-PO",
                "verbal": True,
                "evidence": "Client confirmed by phone",
                "lines": [
                    {
                        "quote_line": revision.lines.get(),
                        "quantity": D(10),
                        "unit_price": D(150),
                    }
                ],
            },
        )
        return po.lines.get().attempts.get(), offer

    def purchase(self, attempt, offer, qty=10):
        return s.create_purchase(
            self.user,
            {
                "request": self.request,
                "supplier": offer.quotation.supplier,
                "currency": "NGN",
                "lines": [
                    {
                        "attempt": attempt,
                        "supplier_offer": offer,
                        "quantity": D(qty),
                        "unit_price": D(100),
                    }
                ],
            },
        )

    def receive_deliver(self, purchase, qty=10):
        receipt = s.create_receipt(
            self.user,
            {
                "purchase": purchase,
                "lines": [{"purchase_line": purchase.lines.get(), "quantity": D(qty)}],
            },
        )
        delivery = s.create_delivery(
            self.user,
            {
                "request": self.request,
                "destination": "Client site",
                "recipient": self.client_party,
                "waybill": self.doc("WAYBILL"),
                "lines": [{"receipt_line": receipt.lines.get(), "quantity": D(qty)}],
            },
        )
        s.dispatch(self.user, delivery)
        return delivery

    def rejected(self, qty=10):
        attempt, offer = self.po()
        delivery = self.receive_deliver(self.purchase(attempt, offer))
        return s.record_outcome(
            self.user,
            delivery,
            {
                "receiver": self.client_party,
                "signed_waybill": self.doc("SIGNED_WAYBILL"),
                "lines": [
                    {
                        "delivery_line": delivery.lines.get(),
                        "accepted": D(10 - qty),
                        "rejected": D(qty),
                        "reason": "Client identified counterfeit item",
                    }
                ],
            },
        ).lines.get()

    def resolve(self, rejection, kind, qty=10, **kwargs):
        return s.resolve_rejection(
            self.user,
            rejection,
            {
                "kind": kind,
                "quantity": D(qty),
                "reason": "Staff documented supplier response",
                **kwargs,
            },
        )

    def post(self, url, data, key=None):
        return self.api.post(
            "/procurement/" + url,
            data,
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(key or uuid4()),
        )

    def test_full_acceptance_and_ledger_available(self):
        attempt, offer = self.po()
        delivery = self.receive_deliver(self.purchase(attempt, offer))
        s.record_outcome(
            self.user,
            delivery,
            {
                "receiver": self.client_party,
                "signed_waybill": self.doc("SIGNED_WAYBILL"),
                "lines": [
                    {
                        "delivery_line": delivery.lines.get(),
                        "accepted": D(10),
                        "rejected": D(0),
                    }
                ],
            },
        )
        response = self.api.get(f"/procurement/items/{self.item.id}/tracker/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["accepted_quantity"], "10.0000")
        self.assertTrue(response.data["finance"]["available"])
        self.assertEqual(response.data["attempts"][0]["status"], "ACCEPTED")
        self.assertTrue(
            self.item.events.filter(kind="ITEM_ACCEPTED", created_by=self.user).exists()
        )

    def test_different_supplier_replacement_preserves_po_and_history(self):
        rejection = self.rejected(4)
        action = self.resolve(rejection, "DIFFERENT_SUPPLIER_CHOSEN", 4)
        attempt = action.replacement_attempt
        offer = self.offer(self.supplier2, attempt)
        delivery = self.receive_deliver(self.purchase(attempt, offer, 4), 4)
        s.record_outcome(
            self.user,
            delivery,
            {
                "receiver": self.client_party,
                "signed_waybill": self.doc("SIGNED_WAYBILL"),
                "lines": [
                    {
                        "delivery_line": delivery.lines.get(),
                        "accepted": D(4),
                        "rejected": D(0),
                    }
                ],
            },
        )
        summary = tracking.tracker(self.item)
        self.assertEqual(D(summary["ordered_quantity"]), 10)
        self.assertEqual(D(summary["accepted_quantity"]), 10)
        self.assertEqual(self.item.attempts.count(), 2)
        self.assertEqual(m.ClientPurchaseOrderLine.objects.count(), 1)
        self.assertTrue(self.item.events.filter(kind="ITEM_REJECTED").exists())

    def test_return_accepted_and_same_supplier_resupply(self):
        rejection = self.rejected()
        self.resolve(rejection, "RETURNED_TO_SUPPLIER")
        self.resolve(rejection, "SUPPLIER_ACCEPTED_RETURN")
        action = self.resolve(rejection, "SAME_SUPPLIER_REPLACEMENT")
        replacement = self.purchase(
            action.replacement_attempt, self.offer(attempt=action.replacement_attempt)
        )
        self.receive_deliver(replacement)
        self.assertEqual(self.item.attempts.count(), 2)

    def test_return_refused_can_choose_another_supplier(self):
        rejection = self.rejected()
        self.resolve(rejection, "RETURNED_TO_SUPPLIER")
        self.resolve(rejection, "SUPPLIER_REFUSED_RETURN")
        self.resolve(rejection, "DIFFERENT_SUPPLIER_CHOSEN")
        with self.assertRaises(ValidationError):
            self.resolve(rejection, "SAME_SUPPLIER_REPLACEMENT")

    def test_partial_refund_report_does_not_set_payment(self):
        rejection = self.rejected()
        self.resolve(
            rejection,
            "REFUND_REPORTED",
            refund_amount=D(500),
            currency="NGN",
            document=self.doc("RECEIPT"),
        )
        self.resolve(rejection, "CLOSED_REJECTED")
        self.assertFalse(self.item.events.filter(kind="PAYMENT_RECEIVED").exists())
        self.assertEqual(D(tracking.tracker(self.item)["closed_rejected_quantity"]), 10)
        with self.assertRaises(ValidationError):
            self.resolve(rejection, "DIFFERENT_SUPPLIER_CHOSEN", 1)

    def test_replacement_can_be_rejected_and_replaced_again(self):
        rejection = self.rejected()
        attempt = self.resolve(
            rejection, "DIFFERENT_SUPPLIER_CHOSEN"
        ).replacement_attempt
        delivery = self.receive_deliver(
            self.purchase(attempt, self.offer(self.supplier2, attempt))
        )
        second = s.record_outcome(
            self.user,
            delivery,
            {
                "receiver": self.client_party,
                "lines": [
                    {
                        "delivery_line": delivery.lines.get(),
                        "accepted": D(0),
                        "rejected": D(10),
                        "reason": "Replacement specification incorrect",
                    }
                ],
            },
        ).lines.get()
        third = self.resolve(second, "DIFFERENT_SUPPLIER_CHOSEN").replacement_attempt
        self.assertEqual(third.po_line, attempt.po_line)
        self.assertEqual(self.item.attempts.count(), 3)
        self.assertEqual(D(tracking.tracker(self.item)["ordered_quantity"]), 10)

    def test_reorder_is_new_cycle_same_client(self):
        new = s.reorder(
            self.user, self.item, {"quantity": D(3), "reason": "Client reordered"}
        )
        self.assertEqual(new.client_id, self.request.client_id)
        self.assertEqual(new.items.get().previous_item, self.item)
        self.assertTrue(new.items.get().events.filter(kind="REQUEST_RECEIVED").exists())
        self.assertTrue(self.item.events.filter(kind="ITEM_REORDERED").exists())

    def test_over_purchase_and_receive_are_rejected(self):
        attempt, offer = self.po()
        purchase = self.purchase(attempt, offer)
        with self.assertRaises(ValidationError):
            self.purchase(attempt, offer, 1)
        with self.assertRaises(ValidationError):
            self.receive_deliver(purchase, 11)
        self.assertEqual(m.GoodsReceipt.objects.count(), 0)

    def test_partial_multi_supplier_purchases(self):
        attempt, offer = self.po()
        self.receive_deliver(self.purchase(attempt, offer, 6), 6)
        self.receive_deliver(self.purchase(attempt, self.offer(self.supplier2), 4), 4)
        self.assertEqual(
            D(tracking.tracker(self.item)["attempts"][0]["quantities"]["received"]), 10
        )

    def test_rejection_reason_and_signed_acceptance_required(self):
        attempt, offer = self.po()
        delivery = self.receive_deliver(self.purchase(attempt, offer))
        for row in [
            {"accepted": D(0), "rejected": D(10)},
            {"accepted": D(10), "rejected": D(0)},
        ]:
            with self.assertRaises(ValidationError):
                s.record_outcome(
                    self.user,
                    delivery,
                    {
                        "receiver": self.client_party,
                        "lines": [{"delivery_line": delivery.lines.get(), **row}],
                    },
                )
        self.assertEqual(m.DeliveryOutcome.objects.count(), 0)

    def test_client_receiver_must_belong_to_client(self):
        attempt, offer = self.po()
        purchase = self.purchase(attempt, offer)
        receipt = s.create_receipt(
            self.user,
            {
                "purchase": purchase,
                "lines": [{"purchase_line": purchase.lines.get(), "quantity": D(10)}],
            },
        )
        unrelated = Party.objects.create(
            display_name="Unrelated receiver", entity_kind="INDIVIDUAL"
        )
        with self.assertRaises(ValidationError):
            s.create_delivery(
                self.user,
                {
                    "request": self.request,
                    "destination": "Site",
                    "recipient": unrelated,
                    "waybill": self.doc("WAYBILL"),
                    "lines": [{"receipt_line": receipt.lines.get(), "quantity": D(10)}],
                },
            )

    def test_expiry_is_system_event_once_and_retains_po(self):
        self.po()
        revision = m.ClientQuotationRevision.objects.get()
        revision.expires_at = timezone.now() - timedelta(seconds=1)
        revision.save()
        self.assertTrue(s.expire_revision(revision))
        self.assertFalse(s.expire_revision(revision))
        entry = self.item.events.get(kind="QUOTATION_EXPIRED")
        self.assertIsNone(entry.created_by)
        self.assertEqual(entry.created_by_name, "System")
        self.assertEqual(m.ClientPurchaseOrder.objects.count(), 1)

    def test_api_idempotency_and_payload_conflict(self):
        payload = {
            "client": str(self.client_party.id),
            "items": [{"description": "Valve", "quantity": "1", "unit": "each"}],
        }
        key = uuid4()
        first = self.post("requests/", payload, key)
        second = self.post("requests/", payload, key)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.data, first.data)
        payload["items"][0]["quantity"] = "2"
        self.assertEqual(self.post("requests/", payload, key).status_code, 409)
        self.assertEqual(m.ProcurementRequest.objects.count(), 2)

    def test_api_rejects_unknown_state_and_no_idempotency(self):
        response = self.post(f"requests/{self.request.id}/submit/", {"status": "PAID"})
        self.assertEqual(response.status_code, 400)
        response = self.api.post(
            "/procurement/requests/",
            {
                "client": str(self.client_party.id),
                "items": [{"description": "Valve", "quantity": 1, "unit": "each"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        response = self.api.patch(
            f"/procurement/items/{self.item.id}/", {"status": "PAID"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def staff(self, all_scope=False):
        user = get_user_model().objects.create_user(
            username="scope-test", password="test", is_staff=True
        )
        perms = Permission.objects.filter(content_type__app_label="procurement")
        if not all_scope:
            perms = perms.exclude(
                codename__in=[
                    "view_all_procurementrequest",
                    "view_department_procurementrequest",
                ]
            )
        user.user_permissions.add(*perms)
        return user

    def test_scope_protects_reads_mutations_and_document_download(self):
        user = self.staff()
        self.api.force_authenticate(user)
        self.assertEqual(self.api.get("/procurement/requests/").data["count"], 0)
        self.assertEqual(
            self.api.get(f"/procurement/items/{self.item.id}/tracker/").status_code, 404
        )
        self.assertEqual(
            self.post(
                "supplier-quotations/",
                {
                    "request": str(self.request.id),
                    "supplier": str(self.supplier.id),
                    "currency": "NGN",
                    "lines": [
                        {"item": str(self.item.id), "quantity": 1, "unit_price": 10}
                    ],
                },
            ).status_code,
            404,
        )
        doc = self.doc("WAYBILL")
        self.assertEqual(
            self.api.get(f"/procurement/documents/{doc.id}/download/").status_code, 404
        )

    def test_staff_permissions_required_not_role_name(self):
        user = get_user_model().objects.create_user(
            username="CEO-test", password="test", is_staff=True
        )
        self.api.force_authenticate(user)
        self.assertEqual(self.api.get("/procurement/requests/").status_code, 403)

    def test_cross_request_document_rejected(self):
        other = s.create_request(
            self.user,
            {
                "client": self.client_party,
                "items": [{"description": "Other", "quantity": D(1), "unit": "each"}],
            },
        )
        with self.assertRaises(ValidationError):
            s.create_offer(
                self.user,
                {
                    "request": self.request,
                    "supplier": self.supplier,
                    "currency": "NGN",
                    "document": self.doc("OTHER", other),
                    "lines": [
                        {"item": self.item, "quantity": D(1), "unit_price": D(1)}
                    ],
                },
            )

    def test_private_upload_download_and_no_storage_key_exposure(self):
        with patch("procurement.documents.get_document_storage") as factory:
            storage = factory.return_value
            storage.upload.return_value = StoredDocument(
                provider="supabase",
                external_file_id="secret-key",
                storage_path="secret/path",
            )
            storage.download.return_value = DownloadedDocument(
                BytesIO(b"test"), "proof.pdf", "application/pdf"
            )
            file = SimpleUploadedFile(
                "proof.pdf", b"%PDF-1.4 evidence", content_type="application/pdf"
            )
            response = self.api.post(
                "/procurement/documents/",
                {"request": str(self.request.id), "category": "OTHER", "file": file},
                format="multipart",
                HTTP_IDEMPOTENCY_KEY=str(uuid4()),
            )
            self.assertEqual(response.status_code, 201, response.data)
            self.assertNotIn("storage_path", response.data)
            factory.assert_called_with("supabase")
            downloaded = self.api.get(
                f"/procurement/documents/{response.data['id']}/download/"
            )
            self.assertEqual(downloaded.status_code, 200)
            self.assertEqual(downloaded["Cache-Control"], "private, no-store")
            with patch.object(connection, "close"):
                downloaded.close()

    def test_storage_failure_does_not_fallback_or_persist_document(self):
        with patch("procurement.documents.get_document_storage") as factory:
            factory.return_value.upload.side_effect = DocumentStorageError(
                "provider secret"
            )
            response = self.api.post(
                "/procurement/documents/",
                {
                    "request": str(self.request.id),
                    "category": "OTHER",
                    "file": SimpleUploadedFile(
                        "proof.pdf", b"proof", content_type="application/pdf"
                    ),
                },
                format="multipart",
                HTTP_IDEMPOTENCY_KEY=str(uuid4()),
            )
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("provider secret", str(response.data))
            self.assertEqual(m.ProcurementDocument.objects.count(), 0)
            factory.assert_called_once_with("supabase")

    def test_finance_submission_does_not_claim_paid(self):
        obj = s.create_finance_request(
            self.user,
            {
                "request": self.request,
                "kind": "ADVANCE",
                "currency": "NGN",
                "lines": [{"item": self.item, "quantity": D(10), "amount": D(1000)}],
            },
        )
        self.assertEqual(
            self.post(f"finance-requests/{obj.id}/submit/", {}).status_code, 200
        )
        from ledger.models import FinanceSubmission, JournalEntry

        self.assertTrue(FinanceSubmission.objects.filter(source=obj).exists())
        self.assertFalse(JournalEntry.objects.exists())
        self.assertFalse(self.item.events.filter(kind="PAYMENT_RECEIVED").exists())

    def test_history_cannot_be_deleted_or_overwritten(self):
        entry = self.item.events.first()
        with self.assertRaises(DjangoValidationError):
            entry.delete()
        with self.assertRaises(DjangoValidationError):
            entry.reason = "overwrite"
            entry.save()
        with self.assertRaises(DjangoValidationError):
            self.item.events.all().delete()

    def test_database_rejects_negative_quantity(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            s.make(
                m.RequestItem,
                self.user,
                request=self.request,
                description="Bad",
                quantity=D(-1),
                unit="each",
            )

    def test_failed_multiline_write_rolls_back_all_records(self):
        initial = self.item.events.count()
        response = self.post(
            "supplier-quotations/",
            {
                "request": str(self.request.id),
                "supplier": str(self.supplier.id),
                "currency": "NGN",
                "lines": [
                    {"item": str(self.item.id), "quantity": 1, "unit_price": 10},
                    {"item": str(self.item.id), "quantity": -1, "unit_price": 10},
                ],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.item.events.count(), initial)
        self.assertEqual(m.SupplierQuotation.objects.count(), 0)

    def test_quotation_revision_prices_and_reissue_history(self):
        self.po()
        original = m.ClientQuotationRevision.objects.get()
        changed = s.create_quote(
            self.user,
            {
                "request": self.request,
                "currency": "NGN",
                "document": self.doc("QUOTATION"),
                "reason": "Supplier price increased",
                "lines": [
                    {
                        "item": self.item,
                        "supplier_offer": self.item.supplier_offers.get(),
                        "description": "Pump",
                        "quantity": D(10),
                        "unit_price": D(160),
                        "discount_type": "PERCENT",
                        "discount_value": D(10),
                        "tax_rate": D("7.5"),
                    }
                ],
            },
            quotation=original.quotation,
        )
        with patch("procurement.document_registry.require_ledger_approval"):
            s.issue_quote(
                self.user,
                changed,
                {
                    "recipient": self.client_party,
                    "method": "email",
                    "evidence": "Revision sent",
                },
            )
        original.refresh_from_db()
        self.assertEqual(original.lines.get().unit_price, D(150))
        self.assertEqual(
            m.ClientPurchaseOrderLine.objects.get().quote_line.revision_id, original.id
        )
        self.assertTrue(
            self.item.events.filter(
                kind="QUOTATION_REISSUED", reason="Supplier price increased"
            ).exists()
        )
        response = self.api.get(f"/procurement/quotation-revisions/{changed.id}/")
        line = response.data["lines"][0]
        self.assertNotIn("estimated_unit_cost", line)
        self.assertEqual(D(line["discount"]), D(160))
        self.assertEqual(D(line["tax"]), D(108))
        self.assertEqual(D(line["pricing"]["total"]), D(1548))

    def test_quote_api_percentage_contract_and_human_labels(self):
        offer = self.offer()
        payload = {
            "request": str(self.request.pk),
            "currency": "NGN",
            "expires_at": (timezone.localdate() + timedelta(days=7)).isoformat(),
            "lines": [
                {
                    "item": str(self.item.pk),
                    "supplier_offer": str(offer.pk),
                    "description": "Pump on quote",
                    "quantity": "10",
                    "unit_price": "100",
                    "discount_type": "AMOUNT",
                    "discount_value": "10",
                }
            ],
        }
        response = self.post("quotations/", payload)
        self.assertEqual(response.status_code, 201, response.data)
        line = response.data["lines"][0]
        self.assertEqual(D(line["discount"]), D(10))
        self.assertEqual(D(line["tax_rate"]), D("7.5"))
        self.assertEqual(D(line["tax"]), D("74.25"))
        self.assertNotIn("estimated_unit_cost", line)
        self.assertEqual(len(response.data["display_reference"]), 6)
        self.assertIn(self.client_party.display_name, response.data["display_label"])
        payload["lines"][0]["estimated_unit_cost"] = "50"
        rejected = self.post("quotations/", payload)
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("estimated_unit_cost", rejected.data["lines"][0])

    def test_outcome_overrun_does_not_add_events(self):
        rejection = self.rejected(4)
        count = self.item.events.count()
        with self.assertRaises(ValidationError):
            s.record_outcome(
                self.user,
                rejection.outcome.delivery,
                {
                    "receiver": self.client_party,
                    "signed_waybill": self.doc("SIGNED_WAYBILL"),
                    "lines": [
                        {
                            "delivery_line": rejection.delivery_line,
                            "accepted": D(1),
                            "rejected": D(0),
                        }
                    ],
                },
            )
        self.assertEqual(self.item.events.count(), count)

    def test_replacement_cannot_use_the_wrong_supplier(self):
        rejection = self.rejected()
        attempt = self.resolve(
            rejection, "DIFFERENT_SUPPLIER_CHOSEN"
        ).replacement_attempt
        with self.assertRaises(ValidationError):
            self.purchase(attempt, self.offer(attempt=attempt))

    def test_refund_requires_amount_currency_and_evidence(self):
        rejection = self.rejected()
        with self.assertRaises(ValidationError):
            self.resolve(
                rejection, "REFUND_REPORTED", refund_amount=D(100), currency="NGN"
            )
        self.assertFalse(rejection.resolution_actions.exists())

    def test_notification_failure_does_not_control_business_state(self):
        self.po()
        revision = m.ClientQuotationRevision.objects.get()
        revision.expires_at = timezone.now() - timedelta(seconds=1)
        revision.save()
        with patch(
            "notifications.services.notify",
            side_effect=RuntimeError("test provider failure"),
        ):
            self.assertTrue(s.expire_revision(revision))
        self.assertTrue(self.item.events.filter(kind="QUOTATION_EXPIRED").exists())
