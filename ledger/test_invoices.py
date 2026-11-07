from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError, transaction
from django.test import TestCase
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.test import APIClient
from notifications.data import ProviderResult
from crm.models import Party, PartyRole
from .models import (
    SalesInvoice,
    InvoiceRevision,
    PartyLedgerTransaction,
    CashBookEntry,
    CashBookJournal,
    InvoiceEmail,
)
from .invoices import save_invoice, invoice_action, today
from .invoice_email import send_invoice
from .invoice_documents import render_invoice_pdf


class InvoiceTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="invoice-author", email="invoice@example.test", password="test"
        )
        self.party = Party.objects.create(display_name="ABC Client")
        PartyRole.objects.create(party=self.party, role="CLIENT")
        self.api = APIClient()
        self.api.force_authenticate(self.actor)

    def payload(self, **changes):
        return {
            "request_key": str(uuid4()),
            "date": str(today()),
            "currency": "NGN",
            "billed_to": "ABC Client",
            "billing_address": "Client billing address",
            "recipient_email": "client@example.test",
            "notes": "Thank you",
            "items": [
                {
                    "description": "Smart switch",
                    "quantity": "2",
                    "unit": "each",
                    "unit_price": "50000.00",
                }
            ],
            **changes,
        }

    def create(self, **changes):
        return save_invoice(
            actor=self.actor, party_id=self.party.pk, payload=self.payload(**changes)
        )

    def action(self, invoice, action, **changes):
        result = invoice_action(
            actor=self.actor,
            invoice_id=invoice.pk,
            action=action,
            payload={
                "request_key": str(uuid4()),
                "version": invoice.version,
                **changes,
            },
        )
        invoice.refresh_from_db()
        return result

    def test_draft_and_confirmation_do_not_post_and_reference_is_stable(self):
        payload = self.payload()
        invoice = save_invoice(
            actor=self.actor, party_id=self.party.pk, payload=payload
        )
        self.assertEqual(
            save_invoice(actor=self.actor, party_id=self.party.pk, payload=payload).pk,
            invoice.pk,
        )
        self.assertIsNone(invoice.reference)
        self.action(invoice, "confirm")
        self.assertRegex(invoice.reference, r"^HOL-ABC-\d{6}-001$")
        snapshot = invoice.confirmed_revision.snapshot
        self.assertEqual(snapshot["items"][0]["reference"], invoice.reference + ".1")
        self.assertEqual(snapshot["subtotal"], "100000.00")
        self.assertEqual(snapshot["vat"], "7500.00")
        self.assertEqual(snapshot["total"], "107500.00")
        self.assertFalse(PartyLedgerTransaction.objects.exists())
        self.assertFalse(CashBookEntry.objects.exists())
        self.assertFalse(CashBookJournal.objects.exists())
        second = self.create()
        self.action(second, "confirm")
        self.assertTrue(second.reference.endswith("-002"))

    def test_edit_reorders_and_keeps_old_confirmed_snapshot(self):
        invoice = self.create(
            items=[
                {
                    "description": "First",
                    "quantity": "1",
                    "unit": "box",
                    "unit_price": "1.00",
                },
                {
                    "description": "Second",
                    "quantity": "1.25",
                    "unit": "metre",
                    "unit_price": "2.50",
                },
            ]
        )
        self.action(invoice, "confirm")
        old = invoice.confirmed_revision
        reference = invoice.reference
        items = [
            {
                "id": str(r.pk),
                "description": r.description,
                "quantity": str(r.quantity),
                "unit": r.unit,
                "unit_price": str(r.unit_price),
            }
            for r in reversed(list(invoice.items.all()))
        ]
        invoice = save_invoice(
            actor=self.actor,
            party_id=self.party.pk,
            invoice_id=invoice.pk,
            payload=self.payload(version=invoice.version, items=items),
        )
        self.assertIsNone(invoice.confirmed_revision_id)
        self.assertEqual(invoice.subtotal, Decimal("4.13"))
        self.action(invoice, "confirm")
        old.refresh_from_db()
        self.assertEqual(old.snapshot["items"][0]["description"], "First")
        self.assertEqual(
            invoice.confirmed_revision.snapshot["items"][0]["description"], "Second"
        )
        self.assertEqual(invoice.reference, reference)
        self.assertEqual(invoice.revisions.count(), 2)
        with self.assertRaises(DatabaseError), transaction.atomic():
            InvoiceRevision.objects.filter(pk=old.pk).update(snapshot={})

    def test_delivery_posts_goods_once_without_vat_cash_or_earned_revenue(self):
        invoice = self.create()
        self.action(invoice, "confirm")
        payload = {
            "request_key": str(uuid4()),
            "version": invoice.version,
            "delivery_date": str(today()),
        }
        first = invoice_action(
            actor=self.actor, invoice_id=invoice.pk, action="deliver", payload=payload
        )
        again = invoice_action(
            actor=self.actor, invoice_id=invoice.pk, action="deliver", payload=payload
        )
        self.assertEqual(first.posted_transaction_id, again.posted_transaction_id)
        self.assertEqual(PartyLedgerTransaction.objects.count(), 1)
        self.assertEqual(first.posted_transaction.amount, Decimal("100000"))
        self.assertEqual(
            first.posted_transaction.journal.credit_account, "DEFERRED_REVENUE"
        )
        self.assertEqual(CashBookJournal.objects.count(), 1)
        self.assertFalse(CashBookEntry.objects.exists())
        self.assertEqual(
            first.confirmed_revision.snapshot["delivery_status"], "Delivered"
        )
        with self.assertRaises(ValidationError):
            save_invoice(
                actor=self.actor,
                party_id=self.party.pk,
                invoice_id=invoice.pk,
                payload=self.payload(version=first.version),
            )

    def test_stale_edits_unconfirmed_delivery_and_foreign_items_fail(self):
        invoice = self.create()
        with self.assertRaises(ValidationError):
            self.action(invoice, "deliver", delivery_date=str(today()))
        with self.assertRaises(ValidationError):
            save_invoice(
                actor=self.actor,
                party_id=self.party.pk,
                invoice_id=invoice.pk,
                payload=self.payload(version=99),
            )
        foreign = self.create().items.get()
        with self.assertRaises(ValidationError):
            save_invoice(
                actor=self.actor,
                party_id=self.party.pk,
                invoice_id=invoice.pk,
                payload=self.payload(
                    version=invoice.version,
                    items=[
                        {
                            "id": str(foreign.pk),
                            "description": "Wrong source",
                            "quantity": "1",
                            "unit": "each",
                            "unit_price": "20",
                        }
                    ],
                ),
            )
        invoice.refresh_from_db()
        self.assertEqual(invoice.version, 1)

    def test_pdf_is_complete_deterministic_and_escaped(self):
        invoice = self.create(
            items=[
                {
                    "description": f"Switch <b>{i}</b>",
                    "quantity": "1",
                    "unit": "each",
                    "unit_price": "10.00",
                }
                for i in range(40)
            ]
        )
        self.action(invoice, "confirm")
        snapshot = invoice.confirmed_revision.snapshot
        snapshot = {**snapshot, "company": {**snapshot["company"], "phone": ""}}
        pdf = render_invoice_pdf(snapshot)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(pdf) as document:
            self.assertGreater(len(document), 1)
            text = ""
            for page in document:
                text += page.get_textpage().get_text_range()
            for expected in [
                "INVOICE",
                invoice.reference,
                "S/N",
                "Switch <b>39</b>",
                "VAT (7.5%)",
                "Signature and Date",
                "For the client",
                "For Holan Mgt",
                "2348073033767, 2348090964567",
                "pcs",
                "Billed to",
            ]:
                self.assertIn(expected, text)
            for item in invoice.confirmed_revision.snapshot["items"]:
                self.assertNotIn(item["reference"], text)
            self.assertNotIn("proforma", text.lower())
        self.assertEqual(snapshot["company"]["phone"], "")
        self.assertEqual(pdf, render_invoice_pdf(snapshot))
        response = self.api.get(f"/ledger/sales-invoices/{invoice.pk}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")

    @patch(
        "ledger.invoice_email.send_document_email",
        return_value=ProviderResult(provider_message_id="test-provider-id"),
    )
    def test_email_attaches_confirmed_pdf_and_deduplicates(self, send):
        invoice = self.create()
        self.action(invoice, "confirm")
        payload = {
            "request_key": str(uuid4()),
            "version": invoice.version,
            "destination": "client@example.test",
        }
        first = send_invoice(actor=self.actor, invoice_id=invoice.pk, payload=payload)
        again = send_invoice(actor=self.actor, invoice_id=invoice.pk, payload=payload)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(first.status, "SENT")
        self.assertNotIn("proforma", first.subject.lower() + first.message.lower())
        self.assertEqual(send.call_args.kwargs["filename"], invoice.reference + ".pdf")
        self.assertTrue(send.call_args.kwargs["content"].startswith(b"%PDF-"))
        self.assertFalse(PartyLedgerTransaction.objects.exists())
        with self.assertRaises(ValidationError):
            send_invoice(
                actor=self.actor,
                invoice_id=invoice.pk,
                payload={**payload, "destination": "someoneelse@example.test"},
            )

    @patch(
        "ledger.invoice_email.send_document_email",
        side_effect=RuntimeError("private provider response"),
    )
    def test_uncertain_email_attempt_is_not_automatically_resent(self, send):
        invoice = self.create()
        self.action(invoice, "confirm")
        payload = {
            "request_key": str(uuid4()),
            "version": invoice.version,
            "destination": "client@example.test",
            "subject": "Your requested invoice",
            "message": "Please review the attached invoice.",
        }
        row = send_invoice(actor=self.actor, invoice_id=invoice.pk, payload=payload)
        send_invoice(actor=self.actor, invoice_id=invoice.pk, payload=payload)
        self.assertEqual(row.status, "UNKNOWN")
        self.assertEqual(send.call_count, 1)
        self.assertNotIn(
            "private provider response", str(InvoiceEmail.objects.values().get())
        )

    def test_assigned_permissions_are_enforced_and_old_route_is_closed(self):
        invoice = self.create()
        user = get_user_model().objects.create_user(
            username="invoice-reader", is_staff=True
        )
        user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="ledger",
                codename__in=["view_salesledger", "view_salesinvoice"],
            )
        )
        self.api.force_authenticate(user)
        self.assertEqual(
            self.api.get(f"/ledger/sales-invoices/{invoice.pk}/").status_code, 200
        )
        self.assertEqual(
            self.api.post(
                f"/ledger/sales-invoices/{invoice.pk}/confirm/",
                {"request_key": str(uuid4()), "version": 1},
                format="json",
            ).status_code,
            403,
        )
        with self.assertRaises(PermissionDenied):
            save_invoice(actor=user, party_id=self.party.pk, payload=self.payload())
        self.api.force_authenticate(self.actor)
        response = self.api.post(
            f"/ledger/party-ledgers/sales/parties/{self.party.pk}/transactions/",
            {
                "request_key": str(uuid4()),
                "transaction_type": "INVOICE",
                "date": str(today()),
                "amount": "100",
                "description": "Bypass",
                "reference": "BYPASS",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PartyLedgerTransaction.objects.exists())

    def test_overflow_and_invalid_items_rejected(self):
        for items in [
            [],
            [
                {
                    "description": "Overflow",
                    "quantity": "99999999",
                    "unit": "each",
                    "unit_price": "9999999999999999.99",
                }
            ],
            [
                {
                    "description": "Bad quantity",
                    "quantity": "0",
                    "unit": "each",
                    "unit_price": "1",
                }
            ],
        ]:
            with self.assertRaises(ValidationError):
                self.create(items=items)
        self.assertFalse(SalesInvoice.objects.exists())

    def test_discount_is_recoverable_and_posts_net_goods_only(self):
        payload = self.payload(discount_percent="10")
        invoice = save_invoice(
            actor=self.actor, party_id=self.party.pk, payload=payload
        )
        retried = save_invoice(
            actor=self.actor, party_id=self.party.pk, payload=payload
        )
        self.assertEqual(retried.pk, invoice.pk)
        with self.assertRaises(ValidationError):
            save_invoice(
                actor=self.actor,
                party_id=self.party.pk,
                payload={**payload, "discount_percent": "11"},
            )
        self.assertEqual(invoice.items.get().amount, Decimal("90000.00"))
        self.assertEqual(invoice.items.get().unit_price, Decimal("50000.00"))
        response = self.api.get(f"/ledger/sales-invoices/{invoice.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["pricing"]["discount_percent"], "10.00")
        self.assertEqual(response.data["pricing"]["gross_subtotal"], "100000.00")
        self.action(invoice, "confirm")
        snapshot = invoice.confirmed_revision.snapshot
        self.assertEqual(snapshot["discount_amount"], "10000.00")
        self.assertEqual(snapshot["subtotal"], "90000.00")
        self.assertEqual(snapshot["vat"], "6750.00")
        self.assertEqual(snapshot["total"], "96750.00")
        self.assertEqual(snapshot["items"][0]["gross_amount"], "100000.00")
        self.action(invoice, "deliver", delivery_date=str(today()))
        self.assertEqual(invoice.posted_transaction.amount, Decimal("90000.00"))
        self.assertEqual(invoice.posted_transaction.journal.amount, Decimal("90000.00"))
        self.assertEqual(
            invoice.confirmed_revision.snapshot["discount_percent"], "10.00"
        )
        self.assertFalse(CashBookEntry.objects.exists())

    def test_discount_edit_is_not_applied_twice_and_preserves_old_revision(self):
        invoice = self.create(discount_percent="10")
        self.action(invoice, "confirm")
        old = invoice.confirmed_revision
        invoice = save_invoice(
            actor=self.actor,
            party_id=self.party.pk,
            invoice_id=invoice.pk,
            payload=self.payload(version=invoice.version, discount_percent="20"),
        )
        self.assertEqual(invoice.subtotal, Decimal("80000.00"))
        self.action(invoice, "confirm")
        old.refresh_from_db()
        self.assertEqual(old.snapshot["subtotal"], "90000.00")
        self.assertEqual(old.snapshot["discount_percent"], "10.00")
        self.assertEqual(
            invoice.confirmed_revision.snapshot["discount_percent"], "20.00"
        )
        invoice = save_invoice(
            actor=self.actor,
            party_id=self.party.pk,
            invoice_id=invoice.pk,
            payload=self.payload(version=invoice.version, discount_percent="0"),
        )
        self.assertEqual(invoice.subtotal, Decimal("100000.00"))

    def test_discount_rounding_is_per_item_and_invalid_discounts_fail(self):
        rows = [
            {
                "description": "Small item",
                "quantity": "1",
                "unit": "pcs",
                "unit_price": ".05",
            }
        ] * 2
        invoice = self.create(discount_percent="10", items=rows)
        self.assertEqual(invoice.subtotal, Decimal(".08"))
        self.assertEqual(
            list(invoice.items.values_list("amount", flat=True)), [Decimal(".04")] * 2
        )
        for rate in ["-1", "100.01", "101", "NaN", "1.001", "100"]:
            with self.subTest(rate=rate), self.assertRaises(ValidationError):
                self.create(discount_percent=rate)

    def test_zero_discount_retries_preserve_legacy_submission_shape(self):
        payload = self.payload()
        invoice = save_invoice(
            actor=self.actor, party_id=self.party.pk, payload=payload
        )
        self.assertNotIn("discount_percent", invoice.operations.get().payload)
        again = save_invoice(
            actor=self.actor,
            party_id=self.party.pk,
            payload={**payload, "discount_percent": "0.00"},
        )
        self.assertEqual(again.pk, invoice.pk)

    def test_pdf_discount_visibility_and_compact_quantity_with_custom_units(self):
        import pypdfium2 as pdfium

        for rate in ["0", "12.50"]:
            with self.subTest(rate=rate):
                invoice = self.create(
                    discount_percent=rate,
                    items=[
                        {
                            "description": "Weight",
                            "quantity": "4.0000",
                            "unit": "kilogram",
                            "unit_price": "10.00",
                        },
                        {
                            "description": "Custom",
                            "quantity": "1.2500",
                            "unit": "tray",
                            "unit_price": "2.00",
                        },
                    ],
                )
                self.action(invoice, "confirm")
                with pdfium.PdfDocument(
                    render_invoice_pdf(invoice.confirmed_revision.snapshot)
                ) as doc:
                    text = "".join(page.get_textpage().get_text_range() for page in doc)
                self.assertIn("4 kg", text)
                self.assertIn("1.25 tray", text)
                self.assertNotIn("4.0000", text)
                if rate == "0":
                    self.assertNotIn("Discount", text)
                else:
                    self.assertIn("Discount (12.5%)", text)
