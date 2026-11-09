from decimal import Decimal
from uuid import uuid4
from io import BytesIO
from unittest.mock import patch, Mock
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, transaction
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError, PermissionDenied
from crm.models import Party, PartyRole
from crm.storage.base import StoredDocument, DownloadedDocument, DocumentStorageError
from .models import (
    PurchaseBill,
    PurchasePayment,
    LedgerAccount,
    CashBookEntry,
    CashBookJournal,
    PartyLedgerTransaction,
)
from .purchases import save_bill, deliver_bill, create_payment
from .purchase_uploads import update_evidence, validated_file
from .invoices import today
from .operations import operate


class PurchaseTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="supplier-accountant",
            email="supplier@example.test",
            password="test",
        )
        self.party = Party.objects.create(display_name="Example supplier")
        PartyRole.objects.create(party=self.party, role="SUPPLIER")
        self.bank = LedgerAccount.objects.create(
            name="Paying bank", kind="BANK", currency="NGN"
        )
        self.api = APIClient()
        self.api.force_authenticate(self.actor)

    def payload(self, **changes):
        return {
            "request_key": str(uuid4()),
            "date": str(today()),
            "currency": "NGN",
            "reference": str(uuid4()),
            "items": [
                {
                    "description": "Switch",
                    "quantity": "2",
                    "unit": "pcs",
                    "unit_price": "50000.00",
                }
            ],
            "account_id": str(self.bank.pk),
            "payment_method": "BANK_TRANSFER",
            "bank_name": "Example bank",
            **changes,
        }

    def bill(self, **changes):
        return save_bill(
            actor=self.actor, party_id=self.party.pk, payload=self.payload(**changes)
        )

    def pay(self, **changes):
        return create_payment(
            actor=self.actor, party_id=self.party.pk, payload=self.payload(**changes)
        )

    def file(self):
        from PIL import Image

        stream = BytesIO()
        Image.new("RGB", (2, 2)).save(stream, format="PNG")
        return SimpleUploadedFile(
            "receipt.png", stream.getvalue(), content_type="image/png"
        )

    def storage(self):
        backend = Mock()
        backend.upload.return_value = StoredDocument(
            provider="supabase",
            external_file_id="private-object",
            storage_path="private/path",
        )
        return backend

    def test_bill_debt_only_after_delivery_and_retry_once(self):
        bill = self.bill(tax_enabled=True)
        self.assertFalse(PartyLedgerTransaction.objects.exists())
        payload = {
            "request_key": str(uuid4()),
            "version": 1,
            "delivery_date": str(today()),
        }
        result = deliver_bill(actor=self.actor, pk=bill.pk, payload=payload)
        self.assertEqual(result.transaction.amount, Decimal("100000"))
        self.assertEqual(result.transaction.journal.debit_account, "PURCHASE_CLEARING")
        self.assertEqual(result.transaction.journal.credit_account, "PAYABLE")
        self.assertEqual(
            deliver_bill(actor=self.actor, pk=bill.pk, payload=payload).pk, bill.pk
        )
        self.assertEqual(PartyLedgerTransaction.objects.count(), 1)
        self.assertFalse(CashBookEntry.objects.exists())
        with self.assertRaises(DatabaseError), transaction.atomic():
            PurchaseBill.objects.filter(pk=bill.pk).update(subtotal=1)

    def test_already_delivered_bill_and_fixed_percent_discount(self):
        first = self.bill(
            delivered=True,
            tax_enabled=True,
            discount_enabled=True,
            discount_type="AMOUNT",
            discount_value="10000",
        )
        second = self.bill(
            delivered=True,
            tax_enabled=True,
            discount_enabled=True,
            discount_type="PERCENT",
            discount_value="10",
        )
        for bill in [first, second]:
            self.assertEqual(bill.subtotal, Decimal("90000"))
            self.assertEqual(bill.snapshot["tax"], "6750.00")
            self.assertEqual(bill.transaction.amount, Decimal("90000"))
        self.assertEqual(CashBookJournal.objects.count(), 4)

    def test_undelivered_bill_edit_audit_stale_version_and_duplicate(self):
        bill = self.bill(reference="SUP-001")
        edited = save_bill(
            actor=self.actor,
            party_id=self.party.pk,
            bill_id=bill.pk,
            payload=self.payload(
                version=1,
                reference="SUP-001",
                discount_enabled=True,
                discount_type="PERCENT",
                discount_value="5",
            ),
        )
        self.assertEqual(edited.version, 2)
        self.assertEqual(edited.events.count(), 2)
        with self.assertRaises(ValidationError):
            save_bill(
                actor=self.actor,
                party_id=self.party.pk,
                bill_id=bill.pk,
                payload=self.payload(version=1, reference="SUP-001"),
            )
        with self.assertRaises(ValidationError):
            self.bill(reference="sup-001")

    def test_payment_tax_combinations_and_adjustable_withholding(self):
        for tax, wht, rate, expected in [
            (False, False, "2", "100000"),
            (True, False, "2", "107500"),
            (False, True, "2", "98000"),
            (True, True, "2", "105500"),
            (True, True, "5", "102500"),
        ]:
            payment = self.pay(
                tax_enabled=tax, withholding_enabled=wht, withholding_percent=rate
            )
            self.assertEqual(payment.paid, Decimal(expected))
            self.assertEqual(payment.transaction.cash_entry.amount, Decimal(expected))
            self.assertEqual(payment.transaction.amount, Decimal("100000"))
            totals = {}
            for j in payment.transaction.cash_entry.journal.all():
                totals[j.debit_account] = totals.get(j.debit_account, 0) + j.amount
                totals[j.credit_account] = totals.get(j.credit_account, 0) - j.amount
            self.assertEqual(totals["PAYABLE"], Decimal("100000"))
            self.assertEqual(totals["CASH"], -Decimal(expected))
            self.assertEqual(sum(totals.values()), 0)
            self.assertEqual(totals.get("INPUT_TAX", 0), Decimal("7500") if tax else 0)
            self.assertEqual(
                totals.get("WHT_PAYABLE", 0), -Decimal(rate) * 1000 if wht else 0
            )

    def test_payment_copy_advance_then_allocated_payment(self):
        bill = self.bill(tax_enabled=True)
        payment = self.pay(
            bill_id=str(bill.pk),
            bill_version=1,
            items=[
                {
                    "description": "Edited receipt item",
                    "quantity": "1",
                    "unit": "pcs",
                    "unit_price": "40000",
                }
            ],
        )
        bill.refresh_from_db()
        self.assertIsNone(bill.transaction_id)
        self.assertEqual(bill.subtotal, Decimal("100000"))
        self.assertEqual(payment.subtotal, Decimal("40000"))
        self.assertEqual(payment.snapshot["allocations"], [])
        bill = deliver_bill(
            actor=self.actor,
            pk=bill.pk,
            payload={
                "request_key": str(uuid4()),
                "version": 1,
                "delivery_date": str(today()),
            },
        )
        with self.assertRaises(ValidationError):
            self.pay(bill_id=str(bill.pk), bill_version=2)
        paid = self.pay(
            bill_id=str(bill.pk),
            bill_version=2,
            tax_enabled=True,
            withholding_enabled=True,
            items=[
                {
                    "description": "Remaining goods",
                    "quantity": "1",
                    "unit": "pcs",
                    "unit_price": "60000",
                }
            ],
        )
        self.assertEqual(paid.snapshot["allocations"][0]["amount"], "60000.00")
        from .models import PartyPaymentAllocation

        self.assertEqual(
            PartyPaymentAllocation.objects.get(payment=payment.transaction).amount,
            Decimal("40000"),
        )

    def test_retry_file_upload_once_and_evidence_only_edit(self):
        backend = self.storage()
        payload = self.payload(receipt_number="SUP-R001", tax_enabled=True)
        with patch(
            "ledger.purchase_uploads.get_document_storage", return_value=backend
        ):
            payment = create_payment(
                actor=self.actor,
                party_id=self.party.pk,
                payload=payload,
                uploaded_file=self.file(),
            )
            self.assertEqual(
                create_payment(
                    actor=self.actor,
                    party_id=self.party.pk,
                    payload=payload,
                    uploaded_file=self.file(),
                ).pk,
                payment.pk,
            )
            self.assertEqual(backend.upload.call_count, 1)
            row = update_evidence(
                actor=self.actor,
                pk=payment.pk,
                payload={
                    "request_key": str(uuid4()),
                    "version": 1,
                    "receipt_number": "SUP-R002",
                },
            )
            self.assertEqual(row.filename, "receipt.png")
            self.assertEqual(payment.evidence.count(), 2)
            self.assertEqual(payment.transaction.cash_entry.amount, Decimal("107500"))
            with self.assertRaises(ValidationError):
                update_evidence(
                    actor=self.actor,
                    pk=payment.pk,
                    payload={
                        "request_key": str(uuid4()),
                        "version": 2,
                        "receipt_number": "bad",
                        "amount": "1",
                    },
                )
            with self.assertRaises(ValidationError):
                update_evidence(
                    actor=self.actor,
                    pk=payment.pk,
                    payload={
                        "request_key": str(uuid4()),
                        "version": 1,
                        "receipt_number": "stale",
                    },
                )
        with self.assertRaises(DatabaseError), transaction.atomic():
            PurchasePayment.objects.filter(pk=payment.pk).update(subtotal=1)

    def test_file_validation_storage_failures_and_compensation(self):
        with self.assertRaises(ValidationError):
            validated_file(
                SimpleUploadedFile(
                    "bad.pdf", b"not pdf", content_type="application/pdf"
                )
            )
        backend = self.storage()
        backend.upload.side_effect = DocumentStorageError("secret")
        with (
            patch("ledger.purchase_uploads.get_document_storage", return_value=backend),
            self.assertRaises(ValidationError),
        ):
            create_payment(
                actor=self.actor,
                party_id=self.party.pk,
                payload=self.payload(),
                uploaded_file=self.file(),
            )
        self.assertFalse(CashBookEntry.objects.exists())
        backend = self.storage()
        with (
            patch("ledger.purchase_uploads.get_document_storage", return_value=backend),
            patch(
                "ledger.purchase_uploads.PurchaseReceiptEvidence.objects.create",
                side_effect=RuntimeError("db failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            create_payment(
                actor=self.actor,
                party_id=self.party.pk,
                payload=self.payload(),
                uploaded_file=self.file(),
            )
        backend.delete.assert_called_once()
        self.assertFalse(CashBookEntry.objects.exists())

    def test_private_download_uses_recorded_provider_and_scopes_evidence(self):
        backend = self.storage()
        with patch(
            "ledger.purchase_uploads.get_document_storage", return_value=backend
        ):
            payment = create_payment(
                actor=self.actor,
                party_id=self.party.pk,
                payload=self.payload(),
                uploaded_file=self.file(),
            )
        row = payment.evidence.first()
        backend.download.return_value = DownloadedDocument(
            BytesIO(b"file"), "receipt.png", "image/png", 4
        )
        with patch(
            "ledger.purchase_api.get_document_storage", return_value=backend
        ) as factory:
            response = self.api.get(
                f"/ledger/purchase-payments/{payment.pk}/evidence/{row.pk}/download/"
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertEqual(response["Cache-Control"], "private, no-store")
            factory.assert_called_once_with("supabase")
            backend.download.return_value.file_object.close()
        self.assertEqual(
            self.api.get(
                f"/ledger/purchase-payments/{uuid4()}/evidence/{row.pk}/download/"
            ).status_code,
            404,
        )
        response = self.api.get(f"/ledger/purchase-payments/{payment.pk}/")
        self.assertNotIn("storage_path", str(response.data))
        self.assertNotIn("external_file_id", str(response.data))

    def test_permissions_scope_and_atomic_failure(self):
        staff = get_user_model().objects.create_user(
            username="purchase-limited", is_staff=True
        )
        staff.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="ledger",
                codename__in=[
                    "view_purchaseledger",
                    "view_purchasepayment",
                    "add_purchasepayment",
                    "add_purchasetransaction",
                ],
            )
        )
        with self.assertRaises(PermissionDenied):
            create_payment(actor=staff, party_id=self.party.pk, payload=self.payload())
        self.api.force_authenticate(staff)
        payment = self.pay()
        self.assertEqual(
            self.api.patch(
                f"/ledger/purchase-payments/{payment.pk}/", {}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.api.get(
                f"/ledger/purchase-payments/{payment.pk}/evidence/{payment.evidence.first().pk}/download/"
            ).status_code,
            403,
        )
        with self.assertRaises(ValidationError):
            self.pay(currency="GBP")
        with self.assertRaises(ValidationError):
            self.pay(bill_id=str(uuid4()), bill_version=1)
        with self.assertRaises(ValidationError):
            self.bill(
                discount_enabled=True, discount_type="AMOUNT", discount_value="100000"
            )

    def test_reversal_and_refunds_restore_actual_cash_tax_and_withholding(self):
        for action in ["REVERSE", "REFUND"]:
            payment = self.pay(tax_enabled=True, withholding_enabled=True)
            result = operate(
                actor=self.actor,
                party_id=self.party.pk,
                kind="purchase",
                payload={
                    "request_key": str(uuid4()),
                    "action": action,
                    "transaction_id": str(payment.transaction_id),
                    "date": str(today()),
                    "reason": "Supplier returned payment",
                    "amount": "100000",
                },
            )
            reverse = PartyLedgerTransaction.objects.get(pk=result["id"])
            self.assertEqual(reverse.cash_entry.kind, "RECEIPT")
            self.assertEqual(reverse.cash_entry.amount, Decimal("105500"))
            self.assertEqual(reverse.cash_entry.journal.count(), 3)

    def test_multipart_create_and_later_scan_update(self):
        import json

        backend = self.storage()
        with patch(
            "ledger.purchase_uploads.get_document_storage", return_value=backend
        ):
            response = self.api.post(
                f"/ledger/purchase-payments/parties/{self.party.pk}/",
                {
                    "payload": json.dumps(self.payload(receipt_number="R-001")),
                    "file": self.file(),
                },
                format="multipart",
            )
            self.assertEqual(response.status_code, 201, response.data)
            pk = response.data["id"]
            edited = self.api.patch(
                f"/ledger/purchase-payments/{pk}/",
                {
                    "payload": json.dumps(
                        {
                            "request_key": str(uuid4()),
                            "version": 1,
                            "receipt_number": "R-002",
                        }
                    ),
                    "file": self.file(),
                },
                format="multipart",
            )
            self.assertEqual(edited.status_code, 200, edited.data)
            self.assertEqual(edited.data["version"], 2)
            self.assertEqual(backend.upload.call_count, 2)
            self.assertEqual(CashBookEntry.objects.count(), 1)
            self.assertEqual(PurchasePayment.objects.get(pk=pk).evidence.count(), 2)
