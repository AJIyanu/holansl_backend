from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import transaction, DatabaseError
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.test import APIClient
from crm.models import Party, PartyRole
from .models import TaxEntry, CashBookEntry, CashBookJournal, LedgerAccount
from .invoices import save_invoice, invoice_action, today
from .receipts import create_receipt
from .purchases import save_bill, create_payment
from .tax_operations import prepare, decide, balances
from .tax_posting import totals
from .tax_api import policy
from .operations import operate
from .reconciliation import snapshot


class TaxLedgerTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="tax-accountant", email="tax@example.test", password="test"
        )
        self.party = Party.objects.create(display_name="Tax party")
        for role in ["CLIENT", "SUPPLIER"]:
            PartyRole.objects.create(party=self.party, role=role)
        self.bank = LedgerAccount.objects.create(
            name="Tax bank", kind="BANK", currency="NGN"
        )
        self.api = APIClient()
        self.api.force_authenticate(self.actor)

    def data(self, amount="100000", **extra):
        return {
            "request_key": str(uuid4()),
            "date": str(today()),
            "currency": "NGN",
            "billed_to": "Tax party",
            "billing_address": "",
            "recipient_email": "",
            "notes": "",
            "reference": str(uuid4()),
            "account_id": str(self.bank.pk),
            "payment_method": "BANK_TRANSFER",
            "bank_name": "Tax bank",
            "items": [
                {
                    "description": "Goods",
                    "quantity": "1",
                    "unit": "pcs",
                    "unit_price": amount,
                }
            ],
            **extra,
        }

    def invoice(self):
        doc = save_invoice(
            actor=self.actor, party_id=self.party.pk, payload=self.data()
        )
        return invoice_action(
            actor=self.actor,
            invoice_id=doc.pk,
            action="confirm",
            payload={"request_key": str(uuid4()), "version": doc.version},
        )

    def deliver(self, doc):
        return invoice_action(
            actor=self.actor,
            invoice_id=doc.pk,
            action="deliver",
            payload={
                "request_key": str(uuid4()),
                "version": doc.version,
                "delivery_date": str(today()),
            },
        )

    def receipt(self, doc=None, amount="100000", **kw):
        link = (
            {"invoice_id": str(doc.pk), "invoice_version": doc.version} if doc else {}
        )
        return create_receipt(
            actor=self.actor,
            party_id=self.party.pk,
            payload=self.data(amount, vat_paid=True, **link, **kw),
        )

    def request(self, action, amount="100", **kw):
        return prepare(
            actor=self.actor,
            payload={
                "request_key": str(uuid4()),
                "action": action,
                "date": str(today()),
                "period": today().strftime("%Y-%m"),
                "currency": "NGN",
                "amount": amount,
                "description": "Tax review",
                "authority": "NRS",
                "reference": str(uuid4()),
                "account_id": str(self.bank.pk),
                **kw,
            },
        )

    def approve(self, row):
        return decide(
            actor=self.actor,
            pk=row.pk,
            payload={
                "request_key": str(uuid4()),
                "approved": True,
                "reason": "Checked",
            },
        )

    def test_delivery_then_payment_recognises_vat_once(self):
        doc = self.deliver(self.invoice())
        self.receipt(doc)
        t = totals(TaxEntry.objects.all())
        self.assertEqual(t["output_vat"], Decimal("7500"))
        self.assertEqual(t["sales_vat_cash"], Decimal("7500"))
        self.assertEqual(t["party_vat"], 0)
        self.assertEqual(CashBookEntry.objects.count(), 1)
        vat = sum(
            (
                r.amount if r.credit_account == "VAT_PAYABLE" else -r.amount
                for r in CashBookJournal.objects.filter(
                    __import__("django").db.models.Q(debit_account="VAT_PAYABLE")
                    | __import__("django").db.models.Q(credit_account="VAT_PAYABLE")
                )
            ),
            Decimal(0),
        )
        self.assertEqual(vat, 7500)

    def test_partial_payment_delivery_balance_payment(self):
        doc = self.invoice()
        self.receipt(doc, "50000")
        doc = self.deliver(doc)
        self.assertEqual(totals(TaxEntry.objects.all())["party_vat"], Decimal("3750"))
        self.receipt(doc, "50000")
        t = totals(TaxEntry.objects.all())
        self.assertEqual(t["output_vat"], 7500)
        self.assertEqual(t["party_vat"], 0)
        with self.assertRaises(ValidationError):
            self.receipt(doc, "1")

    def test_proforma_has_no_posting_and_payment_is_atomic(self):
        self.invoice()
        self.assertFalse(TaxEntry.objects.exists())
        with patch(
            "ledger.tax_posting.payment_tax", side_effect=ValidationError("failure")
        ):
            with self.assertRaises(ValidationError):
                self.receipt()
        self.assertFalse(CashBookEntry.objects.exists())
        self.assertFalse(CashBookJournal.objects.exists())

    def test_supplier_delivery_and_payment_input_vat_once(self):
        bill = save_bill(
            actor=self.actor,
            party_id=self.party.pk,
            payload=self.data(tax_enabled=True, delivered=True),
        )
        create_payment(
            actor=self.actor,
            party_id=self.party.pk,
            payload=self.data(
                tax_enabled=True,
                withholding_enabled=True,
                bill_id=str(bill.pk),
                bill_version=bill.version,
            ),
        )
        t = totals(TaxEntry.objects.all())
        self.assertEqual(t["input_vat"], 7500)
        self.assertEqual(t["wht_liability"], 2000)
        self.assertEqual(t["party_vat"], 0)

    def test_net_vat_remittance_and_input_offset(self):
        self.receipt()
        create_payment(
            actor=self.actor,
            party_id=self.party.pk,
            payload=self.data("40000", tax_enabled=True),
        )
        row = self.request("REMIT", "4500")
        self.assertEqual(CashBookEntry.objects.count(), 2)
        decision = self.approve(row)
        t = balances("NGN")
        self.assertEqual(t["vat_due"], 0)
        self.assertEqual(t["input_available"], 0)
        self.assertEqual(decision.entry.vat_remitted, 4500)
        self.assertEqual(decision.entry.input_used, 3000)
        with self.assertRaises(ValidationError):
            self.approve(self.request("REMIT", "1"))

    def test_wht_credit_reduces_cit_without_cash(self):
        self.receipt(withholding_applied=True)
        self.approve(self.request("CIT_ASSESS", "5000", tax_type="CIT"))
        self.approve(self.request("CIT_CREDIT", "2000", tax_type="CIT"))
        self.assertEqual(CashBookEntry.objects.count(), 1)
        self.approve(self.request("REMIT", "3000", tax_type="CIT"))
        t = balances("NGN")
        self.assertEqual(t["cit_due"], 0)
        self.assertEqual(t["wht_available"], 0)
        self.assertEqual(t["output_vat"], 7500)

    def test_input_classification_and_rejection_audit(self):
        p = create_payment(
            actor=self.actor,
            party_id=self.party.pk,
            payload=self.data(tax_enabled=True),
        )
        key = f"purchase:payment:{p.pk}"
        self.approve(
            self.request("CLASSIFY", document_key=key, classification="PENDING")
        )
        t = balances("NGN")
        self.assertEqual(t["input_pending"], 7500)
        self.assertEqual(t["input_vat"], 0)
        row = self.request("CLASSIFY", document_key=key, classification="AVAILABLE")
        decide(
            actor=self.actor,
            pk=row.pk,
            payload={
                "request_key": str(uuid4()),
                "approved": False,
                "reason": "Awaiting review",
            },
        )
        self.assertEqual(balances("NGN")["input_pending"], 7500)

    def test_retries_permissions_and_immutability(self):
        self.receipt()
        row = self.request("REMIT", "100")
        decision = self.approve(row)
        self.assertEqual(
            decide(actor=self.actor, pk=row.pk, payload=decision.payload).pk,
            decision.pk,
        )
        with self.assertRaises(DatabaseError), transaction.atomic():
            TaxEntry.objects.filter(pk=decision.entry_id).update(vat_remitted=1)
        user = get_user_model().objects.create_user(
            username="tax-viewer", is_staff=True
        )
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="ledger", codename="view_taxledger"
            )
        )
        with self.assertRaises(PermissionDenied):
            prepare(actor=user, payload=row.payload)
        self.api.force_authenticate(user)
        self.assertEqual(self.api.get("/ledger/tax/").status_code, 200)
        self.assertEqual(self.api.get("/ledger/tax/export/").status_code, 403)

    def test_refund_after_delivery_keeps_vat_until_invoice_credit(self):
        doc = self.invoice()
        receipt = self.receipt(doc)
        doc = self.deliver(doc)
        operate(
            actor=self.actor,
            party_id=self.party.pk,
            kind="sales",
            payload={
                "request_key": str(uuid4()),
                "action": "REFUND",
                "transaction_id": str(receipt.transaction_id),
                "date": str(today()),
                "amount": "100000",
                "reason": "Returned payment",
            },
        )
        t = totals(TaxEntry.objects.all())
        self.assertEqual(t["output_vat"], 7500)
        self.assertEqual(t["sales_vat_cash"], 0)
        self.assertEqual(t["party_vat"], 7500)

    def test_reconciliation_includes_tax_and_actual_cash(self):
        doc = self.deliver(self.invoice())
        self.receipt(doc, withholding_applied=True)
        report = snapshot(
            actor=self.actor,
            party_id=self.party.pk,
            kind="sales",
            payload={
                "request_key": str(uuid4()),
                "date_from": str(today()),
                "date_to": str(today()),
                "currency": "NGN",
            },
        )
        self.assertEqual(report.snapshot["tax"]["vat_cash"], "7500.00")
        self.assertEqual(report.snapshot["tax"]["wht"], "2000.00")
        self.assertEqual(report.snapshot["tax"]["actual_cash"], "105500.00")

    def test_vat_only_settlement(self):
        doc = self.deliver(self.invoice())
        create_receipt(
            actor=self.actor,
            party_id=self.party.pk,
            payload=self.data(
                invoice_id=str(doc.pk), invoice_version=doc.version, vat_paid=False
            ),
        )
        self.approve(self.request("VAT_SETTLE", "7500", document_key=f"sales:{doc.pk}"))
        self.assertEqual(totals(TaxEntry.objects.all())["party_vat"], 0)
        self.assertEqual(totals(TaxEntry.objects.all())["output_vat"], 7500)

    def test_policy_versions_preserve_existing_invoice_rate(self):
        doc = self.invoice()
        policy(
            self.actor,
            {
                "request_key": str(uuid4()),
                "kind": "VAT",
                "rate": "8",
                "effective_date": str(today()),
                "reason": "Test policy",
            },
        )
        self.assertEqual(doc.tax_rate, Decimal("7.5"))
        new = self.invoice()
        self.assertEqual(new.tax_rate, 8)

    def test_reversal_of_remittance_restores_liability_and_cash(self):
        self.receipt()
        original = self.approve(self.request("REMIT", "7500")).entry
        before = CashBookEntry.objects.count()
        self.approve(self.request("REVERSE", source_entry=str(original.pk)))
        self.assertEqual(balances("NGN")["vat_due"], 7500)
        self.assertEqual(CashBookEntry.objects.count(), before + 1)
        with self.assertRaises(ValidationError):
            self.approve(self.request("REVERSE", source_entry=str(original.pk)))

    def test_backfill_only_links_and_is_idempotent(self):
        from .tax_backfill import apply_links, preview

        with patch("ledger.tax_posting.payment_tax"):
            receipt = self.receipt()
        before = (CashBookEntry.objects.count(), CashBookJournal.objects.count())
        self.assertEqual(len(preview()["missing_payments"]), 1)
        self.assertEqual(apply_links(), 1)
        self.assertEqual(apply_links(), 0)
        self.assertEqual(
            before, (CashBookEntry.objects.count(), CashBookJournal.objects.count())
        )
        self.assertEqual(
            TaxEntry.objects.get(source_key=f"payment:{receipt.pk}").output_vat, 7500
        )

    def test_stale_rates_reject_without_cash_posting(self):
        with self.assertRaises(ValidationError):
            self.receipt(expected_vat_rate="8")
        self.assertFalse(CashBookEntry.objects.exists())

    def test_paid_proforma_cannot_be_reduced_below_paid_goods(self):
        doc = self.invoice()
        self.receipt(doc, "50000")
        with self.assertRaises(ValidationError):
            save_invoice(
                actor=self.actor,
                party_id=self.party.pk,
                invoice_id=doc.pk,
                payload=self.data("40000", version=doc.version),
            )
        doc.refresh_from_db()
        self.assertEqual(doc.subtotal, 100000)

    def test_tax_api_permissions_and_exports(self):
        self.receipt()
        self.assertEqual(self.api.get("/ledger/tax/").status_code, 200)
        self.assertEqual(self.api.get("/ledger/tax/export/").status_code, 200)
        staff = get_user_model().objects.create_user(
            username="tax-viewer", is_staff=True
        )
        staff.user_permissions.add(Permission.objects.get(codename="view_taxledger"))
        self.api.force_authenticate(staff)
        self.assertEqual(self.api.get("/ledger/tax/").status_code, 200)
        self.assertEqual(self.api.get("/ledger/tax/export/").status_code, 403)
        self.assertEqual(
            self.api.post(
                "/ledger/tax/requests/", {"action": "REMIT"}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(self.api.get("/ledger/tax/history/").status_code, 403)

    def test_assessment_reversal_cannot_borrow_another_years_cit(self):
        old_period = f"{today().year - 1}-12"
        assessment = self.approve(
            self.request("CIT_ASSESS", "5000", period=old_period)
        ).entry
        self.approve(self.request("REMIT", "5000", period=old_period, tax_type="CIT"))
        self.approve(self.request("CIT_ASSESS", "10000"))
        with self.assertRaises(ValidationError):
            self.approve(
                self.request(
                    "REVERSE", source_entry=str(assessment.pk), period=old_period
                )
            )

    def test_statement_freezes_delivery_vat_after_advance(self):
        doc = self.invoice()
        self.receipt(doc)
        self.deliver(doc)
        report = snapshot(
            actor=self.actor,
            party_id=self.party.pk,
            kind="sales",
            payload={
                "request_key": str(uuid4()),
                "date_from": str(today()),
                "date_to": str(today()),
                "currency": "NGN",
            },
        )
        invoice_row = next(
            row for row in report.snapshot["transactions"] if row["type"] == "INVOICE"
        )
        self.assertEqual(invoice_row["document_vat"], "7500.00")
        self.assertEqual(invoice_row["vat_recognised"], "0.00")
        self.assertEqual(invoice_row["document_reference"], doc.reference)
        self.assertEqual(report.snapshot["print_version"], 2)

    def test_supplier_statement_uses_supplier_receipt_number(self):
        payment = create_payment(
            actor=self.actor,
            party_id=self.party.pk,
            payload=self.data(receipt_number="SUP-RECEIPT-009"),
        )
        report = snapshot(
            actor=self.actor,
            party_id=self.party.pk,
            kind="purchase",
            payload={
                "request_key": str(uuid4()),
                "date_from": str(today()),
                "date_to": str(today()),
                "currency": "NGN",
            },
        )
        row = next(
            row
            for row in report.snapshot["transactions"]
            if row["id"] == str(payment.transaction_id)
        )
        self.assertEqual(row["document_reference"], "SUP-RECEIPT-009")
