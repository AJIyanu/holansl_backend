from decimal import Decimal as D
from uuid import uuid4
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from procurement import (
    tests as fixtures,
    services as s,
    document_registry as registry,
)
from procurement import test_document_registry as document_fixtures
from .models import FinanceSubmission, PartyLedgerTransaction, SalesInvoice
from .invoices import invoice_action, save_invoice
from .procurement_bridge import act
from .operations import operate, recognized


class BridgeTests(TestCase):
    doc = fixtures.ProcurementTests.doc
    offer = fixtures.ProcurementTests.offer
    po = fixtures.ProcurementTests.po
    purchase = fixtures.ProcurementTests.purchase
    receive_deliver = fixtures.ProcurementTests.receive_deliver
    quote_document = document_fixtures.DocumentRegistryTests.quote_document

    @classmethod
    def setUpTestData(cls):
        fixtures.ProcurementTests.setUpTestData.__func__(cls)

    def setUp(self):
        fixtures.ProcurementTests.setUp(self)
        registry.set_client_code(self.user, self.request, "ABCD")

    def action(self, action, source, **extra):
        return act(
            self.user,
            {
                "action": action,
                "source_id": str(source.pk),
                "request_key": str(uuid4()),
                "date": str(timezone.localdate()),
                "reason": "Reviewed source document",
                **extra,
            },
        )

    def invoice(self, po_line=None):
        source = s.create_finance_request(
            self.user,
            {
                "request": self.request,
                "kind": "INVOICE",
                "currency": "NGN",
                "lines": [
                    {
                        "item": self.item,
                        "po_line": po_line,
                        "quantity": D(10),
                        "amount": D(1500),
                    }
                ],
            },
        )
        FinanceSubmission.objects.create(request=source, actor=self.user)
        result = self.action("IMPORT_INVOICE", source)
        invoice = SalesInvoice.objects.get(pk=result["id"])
        invoice_action(
            actor=self.user,
            invoice_id=invoice.pk,
            action="confirm",
            payload={"request_key": str(uuid4()), "version": invoice.version},
        )
        invoice.refresh_from_db()
        return source, invoice

    def test_quotation_approval_and_inbox(self):
        version = self.quote_document()
        registry.request_approval(self.user, version)
        result = self.api.get("/ledger/procurement-inbox/")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["results"][0]["kind"], "QUOTATION")
        self.action("APPROVE", version.quotation_revision)
        registry.require_ledger_approval(version.quotation_revision)
        self.assertEqual(registry.approval_status(version), "APPROVED")
        with self.assertRaises(ValidationError):
            self.action("REJECT", version.quotation_revision)

    def test_invoice_before_purchase_order_then_acceptance_and_bill_import(self):
        source, invoice = self.invoice()
        self.assertIsNone(invoice.posted_transaction_id)
        self.assertEqual(self.action("IMPORT_INVOICE", source)["id"], str(invoice.pk))
        line = invoice.items.get()
        response = self.api.get(
            f"/procurement/lookups/?kind=invoice-lines&request={self.request.pk}"
        )
        self.assertEqual(response.status_code, 200, response.data)
        order = s.create_po(
            self.user,
            {
                "request": self.request,
                "reference": "DIRECT",
                "verbal": True,
                "evidence": "Paid invoice",
                "lines": [
                    {"invoice_line": line.pk, "quantity": D(10), "unit_price": D(150)}
                ],
            },
        )
        attempt = order.lines.get().attempts.get()
        purchase = self.purchase(attempt, self.offer())
        bill = self.action("IMPORT_BILL", purchase)
        self.assertEqual(self.action("IMPORT_BILL", purchase)["id"], bill["id"])
        delivery = self.receive_deliver(purchase)
        invoice_action(
            actor=self.user,
            invoice_id=invoice.pk,
            action="deliver",
            payload={
                "request_key": str(uuid4()),
                "version": invoice.version,
                "delivery_date": str(timezone.localdate()),
            },
        )
        invoice.refresh_from_db()
        posted = invoice.posted_transaction
        with self.assertRaises(ValidationError):
            operate(
                actor=self.user,
                party_id=invoice.party_id,
                kind="sales",
                payload={
                    "request_key": str(uuid4()),
                    "action": "REVERSE",
                    "transaction_id": str(posted.pk),
                    "date": str(timezone.localdate()),
                    "reason": "Cancellation",
                },
            )
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
                        "reason": "",
                    }
                ],
            },
        )
        self.assertEqual(recognized(invoice.posted_transaction), D(1500))
        self.assertEqual(invoice.posted_transaction.recognitions.count(), 1)

    def test_invoice_after_acceptance_recognizes_once(self):
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
                        "reason": "",
                    }
                ],
            },
        )
        _, invoice = self.invoice(attempt.po_line)
        invoice_action(
            actor=self.user,
            invoice_id=invoice.pk,
            action="deliver",
            payload={
                "request_key": str(uuid4()),
                "version": invoice.version,
                "delivery_date": str(timezone.localdate()),
            },
        )
        invoice.refresh_from_db()
        self.assertEqual(recognized(invoice.posted_transaction), D(1500))

    def test_other_finance_request_review_does_not_post_cash(self):
        source = s.create_finance_request(
            self.user,
            {
                "request": self.request,
                "kind": "ADVANCE",
                "currency": "NGN",
                "lines": [{"item": self.item, "quantity": D(10), "amount": D(1000)}],
            },
        )
        FinanceSubmission.objects.create(request=source, actor=self.user)
        self.action("APPROVE_REQUEST", source)
        self.assertFalse(PartyLedgerTransaction.objects.exists())

    def test_procurement_discount_preserves_source_lines_and_posts_net(self):
        _, invoice = self.invoice()
        item = invoice.items.get()
        original_id = item.pk
        source_id = item.source_item_id
        invoice = save_invoice(
            actor=self.user,
            party_id=invoice.party_id,
            invoice_id=invoice.pk,
            payload={
                "request_key": str(uuid4()),
                "version": invoice.version,
                "date": str(invoice.date),
                "currency": invoice.currency,
                "billed_to": invoice.billed_to,
                "discount_percent": "10",
                "items": [
                    {
                        "id": str(item.pk),
                        "description": item.description,
                        "quantity": str(item.quantity),
                        "unit": item.unit,
                        "unit_price": str(item.unit_price),
                    }
                ],
            },
        )
        item.refresh_from_db()
        self.assertEqual(item.pk, original_id)
        self.assertEqual(item.source_item_id, source_id)
        self.assertEqual(item.amount, D("1350.00"))
        self.assertEqual(item.unit_price, D("150.00"))
        for action in ["confirm", "deliver"]:
            invoice_action(
                actor=self.user,
                invoice_id=invoice.pk,
                action=action,
                payload={
                    "request_key": str(uuid4()),
                    "version": invoice.version,
                    "delivery_date": str(timezone.localdate()),
                },
            )
            invoice.refresh_from_db()
        from .models import ProcurementInvoiceLine

        self.assertEqual(invoice.posted_transaction.amount, D("1350.00"))
        self.assertEqual(
            ProcurementInvoiceLine.objects.get(
                link__transaction=invoice.posted_transaction
            ).amount,
            D("1350.00"),
        )
