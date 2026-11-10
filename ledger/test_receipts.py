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
    SalesReceipt,
    CashBookEntry,
    CashBookJournal,
    LedgerAccount,
    PartyLedgerTransaction,
)
from .receipts import create_receipt
from .receipt_email import send_receipt
from .receipt_words import amount_in_words
from .invoice_documents import render_invoice_pdf
from .invoices import today, save_invoice, invoice_action
from .operations import operate


class ReceiptTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="receipt-author", email="receipt@example.test", password="test"
        )
        self.party = Party.objects.create(display_name="ABC Client")
        PartyRole.objects.create(party=self.party, role="CLIENT")
        self.bank = LedgerAccount.objects.create(
            name="Receipt bank", kind="BANK", currency="NGN"
        )
        self.api = APIClient()
        self.api.force_authenticate(self.actor)

    def payload(self, **changes):
        return {
            "request_key": str(uuid4()),
            "date": str(today()),
            "currency": "NGN",
            "billed_to": "ABC Client",
            "billing_address": "Client address",
            "recipient_email": "client@example.test",
            "notes": "Thank you",
            "account_id": str(self.bank.pk),
            "payment_method": "BANK_TRANSFER",
            "bank_name": "Example Bank",
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
        return create_receipt(
            actor=self.actor, party_id=self.party.pk, payload=self.payload(**changes)
        )

    def ledger_totals(self, receipt):
        totals = {}
        for row in receipt.transaction.cash_entry.journal.all():
            totals[row.debit_account] = totals.get(row.debit_account, 0) + row.amount
            totals[row.credit_account] = totals.get(row.credit_account, 0) - row.amount
        return totals

    def test_all_four_tax_combinations_and_exact_posting(self):
        for vat, wht, received in [
            (False, False, "100000"),
            (True, False, "107500"),
            (False, True, "98000"),
            (True, True, "105500"),
        ]:
            with self.subTest(vat=vat, wht=wht):
                receipt = self.create(vat_paid=vat, withholding_applied=wht)
                self.assertEqual(receipt.received, Decimal(received))
                self.assertEqual(receipt.transaction.amount, Decimal("100000"))
                self.assertEqual(
                    receipt.transaction.cash_entry.amount, Decimal(received)
                )
                self.assertEqual(receipt.transaction.effect, -1)
                totals = self.ledger_totals(receipt)
                self.assertEqual(totals["CASH"], Decimal(received))
                self.assertEqual(totals["RECEIVABLE"], Decimal("-100000"))
                self.assertEqual(
                    totals.get("WHT_RECEIVABLE", 0), Decimal("2000") if wht else 0
                )
                self.assertEqual(
                    totals.get("VAT_PAYABLE", 0), Decimal("-7500") if vat else 0
                )
                self.assertEqual(sum(totals.values()), 0)
                self.assertNotIn("INCOME", totals)

    def test_retry_conflict_and_immutable_snapshot(self):
        payload = self.payload(vat_paid=True, withholding_applied=True)
        first = create_receipt(
            actor=self.actor, party_id=self.party.pk, payload=payload
        )
        self.assertEqual(
            create_receipt(
                actor=self.actor, party_id=self.party.pk, payload=payload
            ).pk,
            first.pk,
        )
        with self.assertRaises(ValidationError):
            create_receipt(
                actor=self.actor,
                party_id=self.party.pk,
                payload={**payload, "vat_paid": False},
            )
        self.assertEqual(CashBookEntry.objects.count(), 1)
        self.assertEqual(CashBookJournal.objects.count(), 3)
        with self.assertRaises(DatabaseError), transaction.atomic():
            SalesReceipt.objects.filter(pk=first.pk).update(snapshot={})

    def test_invoice_prefill_editable_and_advance_does_not_post_invoice(self):
        invoice = save_invoice(
            actor=self.actor, party_id=self.party.pk, payload=self.payload()
        )
        receipt = self.create(
            invoice_id=str(invoice.pk),
            invoice_version=invoice.version,
            items=[
                {
                    "description": "Edited switch",
                    "quantity": "1",
                    "unit": "each",
                    "unit_price": "40000",
                }
            ],
        )
        self.assertEqual(receipt.subtotal, Decimal("40000"))
        invoice.refresh_from_db()
        self.assertIsNone(invoice.posted_transaction_id)
        self.assertEqual(invoice.items.first().description, "Smart switch")
        self.assertEqual(PartyLedgerTransaction.objects.count(), 1)
        self.assertEqual(receipt.snapshot["allocations"], [])
        self.assertEqual(invoice.items.first().amount, Decimal("100000"))

    def test_posted_invoice_allocates_goods_only(self):
        invoice = save_invoice(
            actor=self.actor, party_id=self.party.pk, payload=self.payload()
        )
        invoice_action(
            actor=self.actor,
            invoice_id=invoice.pk,
            action="confirm",
            payload={"request_key": str(uuid4()), "version": 1},
        )
        invoice = invoice_action(
            actor=self.actor,
            invoice_id=invoice.pk,
            action="deliver",
            payload={
                "request_key": str(uuid4()),
                "version": 1,
                "delivery_date": str(today()),
            },
        )
        receipt = self.create(
            invoice_id=str(invoice.pk),
            invoice_version=invoice.version,
            vat_paid=True,
            withholding_applied=True,
        )
        self.assertEqual(receipt.snapshot["allocations"][0]["amount"], "100000.00")
        self.assertNotEqual(receipt.reference, invoice.reference)
        self.assertTrue(receipt.reference.endswith("-002"))

    def test_scope_validation_and_rollback(self):
        for changes in [
            {"currency": "GBP"},
            {"payment_method": "CASH"},
            {"bank_name": ""},
            {"invoice_id": str(uuid4()), "invoice_version": 1},
            {"items": []},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.create(**changes)
        self.assertFalse(SalesReceipt.objects.exists())
        self.assertFalse(CashBookEntry.objects.exists())
        with patch(
            "ledger.receipts.SalesReceipt.objects.create",
            side_effect=RuntimeError("rollback"),
        ):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertFalse(CashBookEntry.objects.exists())
        self.assertFalse(CashBookJournal.objects.exists())

    def test_permissions_api_and_no_update_delete(self):
        staff = get_user_model().objects.create_user(username="limited", is_staff=True)
        for permission in [
            "view_salesledger",
            "view_salesreceipt",
            "add_salesreceipt",
            "add_salestransaction",
        ]:
            staff.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label="ledger", codename=permission
                )
            )
        with self.assertRaises(PermissionDenied):
            create_receipt(actor=staff, party_id=self.party.pk, payload=self.payload())
        receipt = self.create()
        self.api.force_authenticate(staff)
        self.assertEqual(
            self.api.get(f"/ledger/sales-receipts/{receipt.pk}/").status_code, 200
        )
        self.assertEqual(
            self.api.get(f"/ledger/sales-receipts/{receipt.pk}/pdf/").status_code, 403
        )
        self.assertEqual(
            self.api.post(
                f"/ledger/sales-receipts/{receipt.pk}/email/", {}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.api.patch(
                f"/ledger/sales-receipts/{receipt.pk}/", {}, format="json"
            ).status_code,
            405,
        )
        self.assertEqual(
            self.api.delete(f"/ledger/sales-receipts/{receipt.pk}/").status_code, 405
        )

    def test_pdf_words_and_template(self):
        receipt = self.create(vat_paid=True, withholding_applied=True)
        pdf = render_invoice_pdf(receipt.snapshot)
        self.assertEqual(pdf, render_invoice_pdf(receipt.snapshot))
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(pdf)
        text = "\n".join(page.get_textpage().get_text_range() for page in document)
        for value in [
            "RECEIPT",
            "105,500.00",
            "One hundred and five thousand five hundred naira only",
            "Withholding tax",
            "Example Bank",
            "24, Damin Plaza, MKO Abiola Way, Ringroad, Ibadan, Oyo, Nigeria.",
            "Signature and Date",
        ]:
            self.assertIn(value, text)
        document.close()
        self.assertNotIn(receipt.reference + ".1", text)
        self.assertLess(
            text.index("Amount received in words"), text.index("Signature and Date")
        )
        self.assertEqual(
            amount_in_words(Decimal("1.01"), "GBP"),
            "One pound sterling and one penny only",
        )
        self.assertEqual(
            amount_in_words(Decimal("2.02"), "USD"), "Two US dollars and two cents only"
        )

    def test_email_attachment_once_and_unknown_outcome(self):
        receipt = self.create()
        payload = {"request_key": str(uuid4()), "destination": "client@example.test"}
        with patch(
            "ledger.receipt_email.send_document_email",
            return_value=ProviderResult(status="SENT", provider_message_id="mock"),
        ) as send:
            row = send_receipt(actor=self.actor, receipt_id=receipt.pk, payload=payload)
            self.assertEqual(row.status, "SENT")
            send_receipt(actor=self.actor, receipt_id=receipt.pk, payload=payload)
            self.assertEqual(send.call_count, 1)
            self.assertTrue(send.call_args.kwargs["content"].startswith(b"%PDF"))
            self.assertIn("payment receipt", send.call_args.kwargs["message"])
        with patch(
            "ledger.receipt_email.send_document_email",
            side_effect=RuntimeError("private"),
        ) as send:
            payload["request_key"] = str(uuid4())
            self.assertEqual(
                send_receipt(
                    actor=self.actor, receipt_id=receipt.pk, payload=payload
                ).status,
                "UNKNOWN",
            )
            send_receipt(actor=self.actor, receipt_id=receipt.pk, payload=payload)
            self.assertEqual(send.call_count, 1)

    def test_reversal_restores_actual_cash_and_all_journal_legs(self):
        receipt = self.create(vat_paid=True, withholding_applied=True)
        result = operate(
            actor=self.actor,
            party_id=self.party.pk,
            kind="sales",
            payload={
                "request_key": str(uuid4()),
                "action": "REVERSE",
                "transaction_id": str(receipt.transaction_id),
                "date": str(today()),
                "reason": "Entered in error",
            },
        )
        reversal = PartyLedgerTransaction.objects.get(pk=result["id"])
        self.assertEqual(reversal.amount, Decimal("100000"))
        self.assertEqual(reversal.cash_entry.amount, Decimal("105500"))
        self.assertEqual(reversal.cash_entry.journal.count(), 3)
        totals = {}
        for row in CashBookJournal.objects.all():
            totals[row.debit_account] = totals.get(row.debit_account, 0) + row.amount
            totals[row.credit_account] = totals.get(row.credit_account, 0) - row.amount
        self.assertTrue(all(value == 0 for value in totals.values()))

    def test_refund_returns_matching_tax_and_cash_portions(self):
        receipt = self.create(vat_paid=True, withholding_applied=True)
        for value in ["33333.33", "66666.67"]:
            operate(
                actor=self.actor,
                party_id=self.party.pk,
                kind="sales",
                payload={
                    "request_key": str(uuid4()),
                    "action": "REFUND",
                    "amount": value,
                    "transaction_id": str(receipt.transaction_id),
                    "date": str(today()),
                    "reason": "Refund advance",
                },
            )
        self.assertEqual(
            sum(row.amount for row in CashBookEntry.objects.filter(kind="EXPENSE")),
            Decimal("105500"),
        )
