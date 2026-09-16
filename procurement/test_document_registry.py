"""Document identity, immutable source snapshots and ledger approval boundaries."""

from django.test import TestCase
from rest_framework.exceptions import ValidationError
from . import (
    tests as fixtures,
    models as m,
    services as s,
    document_registry as registry,
)


class DocumentRegistryTests(TestCase):
    doc = fixtures.ProcurementTests.doc
    offer = fixtures.ProcurementTests.offer
    po = fixtures.ProcurementTests.po
    purchase = fixtures.ProcurementTests.purchase
    receive_deliver = fixtures.ProcurementTests.receive_deliver
    post = fixtures.ProcurementTests.post

    @classmethod
    def setUpTestData(cls):
        fixtures.ProcurementTests.setUpTestData.__func__(cls)

    def setUp(self):
        fixtures.ProcurementTests.setUp(self)
        registry.set_client_code(self.user, self.request, "ABCD")

    def quote_document(self):
        self.po()
        return registry.prepare(
            self.user,
            {
                "request": self.request,
                "kind": "QT",
                "quotation_revision": m.ClientQuotationRevision.objects.get(),
            },
        )

    def test_format_and_repeated_preparation_keeps_identity(self):
        version = self.quote_document()
        self.assertRegex(version.register.number, r"^Hol-ABCD-QT-\d{4}-00001$")
        again = registry.prepare(
            self.user,
            {
                "request": self.request,
                "kind": "QT",
                "quotation_revision": version.quotation_revision,
            },
        )
        self.assertEqual(again.pk, version.pk)
        self.assertNotIn("estimated_unit_cost", str(version.snapshot))
        self.assertNotIn("supplier_offer", str(version.snapshot))

    def test_client_code_is_stable(self):
        with self.assertRaises(ValidationError):
            registry.set_client_code(self.user, self.request, "XYZ")
        self.assertEqual(self.client_party.document_code.code, "ABCD")

    def test_shared_serial_and_waybill_has_no_prices(self):
        attempt, offer = self.po()
        delivery = self.receive_deliver(self.purchase(attempt, offer))
        quote = registry.prepare(
            self.user,
            {
                "request": self.request,
                "kind": "QT",
                "quotation_revision": m.ClientQuotationRevision.objects.get(),
            },
        )
        waybill = registry.prepare(
            self.user, {"request": self.request, "kind": "WB", "delivery": delivery}
        )
        self.assertEqual(waybill.register.serial, quote.register.serial + 1)
        self.assertIn("-WB-", waybill.register.number)
        self.assertNotIn("price", str(waybill.snapshot))
        self.assertEqual(registry.approval_status(waybill), "NOT_REQUIRED")

    def test_requesting_approval_never_grants_approval(self):
        version = self.quote_document()
        registry.request_approval(self.user, version)
        self.assertEqual(registry.approval_status(version), "PENDING")
        with self.assertRaises(registry.IntegrationUnavailable):
            s.issue_quote(
                self.user,
                version.quotation_revision,
                {
                    "recipient": self.client_party,
                    "method": "email",
                    "evidence": "Must not send",
                },
            )

    def test_superuser_cannot_forge_approval(self):
        version = self.quote_document()
        response = self.post(
            f"document-versions/{version.id}/request-approval/", {"approved": True}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.post(
                f"document-versions/{version.id}/generate/",
                {"format": "PDF", "draft": False},
            ).status_code,
            503,
        )

    def test_invoice_and_receipt_cannot_originate_from_procurement(self):
        for kind in ["IN", "RC"]:
            response = self.post(
                "document-versions/", {"request": str(self.request.id), "kind": kind}
            )
            self.assertEqual(response.status_code, 400)

    def test_document_register_api_contains_revision_snapshot(self):
        version = self.quote_document()
        response = self.api.get(
            f"/procurement/document-register/{version.register_id}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["versions"][0]["approval_status"], "DRAFT")

    def test_receiving_contact_lookup_is_scoped_to_client(self):
        response = self.api.get(
            f"/procurement/lookups/?kind=contacts&request={self.request.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["id"] for row in response.data["results"]], [str(self.client_party.id)]
        )
