from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from uuid import uuid4
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, transaction
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError, PermissionDenied
from crm.storage.base import StoredDocument, DocumentStorageError, DownloadedDocument
from .models import (
    LedgerAccount,
    StaffLedgerEntry,
    StaffRetirement,
    CashBookEntry,
    CashBookJournal,
)
from .staff_ledger import post_money, submit_retirement, decide_retirement, balance
from .staff_reports import statement, report
from .invoices import today


class StaffLedgerTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="staff-accountant",
            email="staff-ledger@example.test",
            password="test",
        )
        self.staff = get_user_model().objects.create_user(
            username="staff-worker", is_staff=True
        )
        self.bank = LedgerAccount.objects.create(
            name="Staff bank", kind="BANK", currency="NGN"
        )
        self.api = APIClient()
        self.api.force_authenticate(self.actor)

    def payload(self, **kwargs):
        return dict(
            request_key=str(uuid4()),
            date=str(today()),
            kind="PAY",
            category="ADVANCE",
            currency="NGN",
            amount="100000.00",
            account_id=str(self.bank.pk),
            description="Site visit",
            **kwargs,
        )

    def pay(self, **kwargs):
        data = self.payload()
        data.update(kwargs)
        return post_money(actor=self.actor, staff_id=self.staff.pk, payload=data)

    def retirement(self, advance, amounts=("75000.00",), **changes):
        data = dict(
            request_key=str(uuid4()),
            advance_id=str(advance.pk),
            date=str(today()),
            description="Visit expenses",
            items=[
                dict(
                    date=str(today()),
                    category="TRANSPORT",
                    description=f"Expense {i}",
                    amount=v,
                )
                for i, v in enumerate(amounts)
            ],
        )
        data.update(changes)
        return submit_retirement(actor=self.actor, staff_id=self.staff.pk, payload=data)

    def approve(self, row, **kwargs):
        data = dict(
            request_key=str(uuid4()),
            approved=True,
            date=str(today()),
            reason="Receipts checked",
            expected_remaining=str(balance(row.advance)[0]),
        )
        data.update(kwargs)
        return decide_retirement(actor=self.actor, pk=row.pk, payload=data)

    def test_salary_allowance_and_levy_do_not_create_staff_debt(self):
        for category in ["SALARY", "ALLOWANCE", "TRANSPORT"]:
            entry = self.pay(category=category)
            self.assertEqual(entry.receivable_delta, 0)
            self.assertEqual(entry.journal.get().debit_account, "EXPENSE")
        entry = self.pay(kind="RECEIVE", category="LEVY", amount="500.00")
        self.assertEqual(entry.journal.get().credit_account, "INCOME")
        self.assertEqual(entry.receivable_delta, 0)

    def test_advance_retirement_and_change_without_duplicate_cash(self):
        advance = self.pay()
        row = self.retirement(advance, ("50000.00", "25000.00"))
        self.assertEqual(StaffLedgerEntry.objects.count(), 1)
        self.assertEqual([i["position"] for i in row.items], [1, 2])
        decision = self.approve(row)
        self.assertEqual(decision.actor, row.actor)  # self-approval allowed and audited
        self.assertEqual(CashBookEntry.objects.count(), 1)
        self.assertEqual(balance(advance), (Decimal("25000"), Decimal("0")))
        self.assertEqual(
            list(
                decision.entry.journal.order_by("description").values_list(
                    "amount", flat=True
                )
            ),
            [Decimal("50000"), Decimal("25000")],
        )
        self.pay(
            kind="RECEIVE",
            category="ADVANCE_RETURN",
            amount="25000.00",
            related_entry=str(advance.pk),
        )
        self.assertEqual(balance(advance), (0, 0))
        self.assertEqual(CashBookEntry.objects.count(), 2)

    def test_overspend_creates_company_payable_and_reimbursement_settles(self):
        advance = self.pay()
        decision = self.approve(self.retirement(advance, ("120000.00",)))
        self.assertEqual(balance(advance), (0, Decimal("20000")))
        self.assertEqual(
            set(decision.entry.journal.values_list("credit_account", flat=True)),
            {"STAFF_RECEIVABLE", "STAFF_PAYABLE"},
        )
        payment = self.pay(
            category="ADVANCE_REIMBURSEMENT",
            amount="20000.00",
            related_entry=str(advance.pk),
        )
        self.assertEqual(payment.journal.get().debit_account, "STAFF_PAYABLE")
        self.assertEqual(balance(advance), (0, 0))
        with self.assertRaises(ValidationError):
            self.pay(
                category="ADVANCE_REIMBURSEMENT",
                amount="1.00",
                related_entry=str(advance.pk),
            )

    def test_loan_repayment_cannot_exceed_outstanding_or_cross_staff_currency(self):
        loan = self.pay(category="LOAN")
        self.pay(
            kind="RECEIVE",
            category="LOAN_REPAYMENT",
            amount="20000.00",
            related_entry=str(loan.pk),
        )
        self.assertEqual(balance(loan)[0], Decimal("80000"))
        for changes in [
            dict(amount="80000.01"),
            dict(currency="USD"),
            dict(related_entry=str(uuid4())),
        ]:
            data = dict(
                kind="RECEIVE",
                category="LOAN_REPAYMENT",
                amount="10.00",
                related_entry=str(loan.pk),
            )
            data.update(changes)
            with self.assertRaises(ValidationError):
                self.pay(**data)

    def test_cash_and_decision_retries_are_idempotent(self):
        data = self.payload()
        first = post_money(actor=self.actor, staff_id=self.staff.pk, payload=data)
        self.assertEqual(
            first.pk,
            post_money(actor=self.actor, staff_id=self.staff.pk, payload=data).pk,
        )
        data["amount"] = "999.00"
        with self.assertRaises(ValidationError):
            post_money(actor=self.actor, staff_id=self.staff.pk, payload=data)
        retirement = self.retirement(first)
        decision = self.approve(retirement)
        again = decide_retirement(
            actor=self.actor, pk=str(retirement.pk), payload=decision.payload
        )
        self.assertEqual(decision.pk, again.pk)
        self.assertEqual(CashBookEntry.objects.count(), 1)
        self.assertEqual(StaffLedgerEntry.objects.count(), 2)

    def test_stale_approval_rejected_and_early_return_accounted(self):
        advance = self.pay()
        row = self.retirement(advance)
        self.pay(
            kind="RECEIVE",
            category="ADVANCE_RETURN",
            amount="50000.00",
            related_entry=str(advance.pk),
        )
        with self.assertRaises(ValidationError):
            self.approve(row, expected_remaining="100000.00")
        self.approve(row)
        self.assertEqual(balance(advance), (0, Decimal("25000")))

    def test_rejection_preserves_audit_and_allows_new_submission(self):
        advance = self.pay()
        row = self.retirement(advance)
        with self.assertRaises(ValidationError):
            self.retirement(advance)
        self.approve(row, approved=False, reason="Missing expense receipt")
        replacement = self.retirement(advance)
        self.approve(replacement)
        self.assertEqual(StaffRetirement.objects.count(), 2)
        with self.assertRaises(ValidationError):
            self.retirement(advance)

    def test_permissions_and_identity_directory(self):
        self.api.force_authenticate(self.staff)
        url = "/ledger/staff-ledger/"
        self.assertEqual(self.api.get(url).status_code, 403)
        self.staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="ledger", codename="view_staffledger"
            )
        )
        self.staff = get_user_model().objects.get(pk=self.staff.pk)
        self.api.force_authenticate(self.staff)
        self.assertEqual(
            self.api.put(
                url, {"staff_id": str(self.staff.pk)}, format="json"
            ).status_code,
            200,
        )
        rows = self.api.get(url + "staff/").data["results"]
        self.assertEqual(set(rows[0]), {"id", "name", "username", "is_active"})
        with self.assertRaises(PermissionDenied):
            post_money(actor=self.staff, staff_id=self.staff.pk, payload=self.payload())
        self.assertEqual(
            self.api.post(
                url + f"staff/{self.staff.pk}/entries/", self.payload(), format="json"
            ).status_code,
            403,
        )

    def test_statement_prior_balance_salary_and_frozen_snapshot(self):
        yesterday = today() - timedelta(days=1)
        advance = self.pay(date=str(yesterday))
        self.pay(category="SALARY", amount="1000.00")
        self.approve(self.retirement(advance))
        payload = dict(
            request_key=str(uuid4()),
            date_from=str(today()),
            date_to=str(today()),
            currency="NGN",
        )
        saved = statement(actor=self.actor, staff_id=self.staff.pk, payload=payload)
        self.assertEqual(saved.snapshot["summary"]["opening_receivable"], "100000.00")
        self.assertEqual(saved.snapshot["summary"]["change_due"], "25000.00")
        self.assertEqual(saved.snapshot["summary"]["paid"], "1000.00")
        self.assertEqual(len(saved.snapshot["transactions"]), 2)
        old = report(
            self.staff, dict(date_from=yesterday, date_to=yesterday, currency="NGN")
        )
        self.assertEqual(old["summary"]["unretired_advances"], "100000.00")
        self.pay(
            kind="RECEIVE",
            category="ADVANCE_RETURN",
            amount="25000.00",
            related_entry=str(advance.pk),
        )
        retry = statement(actor=self.actor, staff_id=self.staff.pk, payload=payload)
        self.assertEqual(saved.pk, retry.pk)
        self.assertEqual(retry.snapshot["summary"]["closing_receivable"], "25000.00")

    def file(self):
        from PIL import Image

        stream = BytesIO()
        Image.new("RGB", (2, 2)).save(stream, format="PNG")
        return SimpleUploadedFile(
            "support.png", stream.getvalue(), content_type="image/png"
        )

    def storage(self):
        storage = Mock()
        storage.upload.return_value = StoredDocument(
            provider="supabase", external_file_id="private", storage_path="private/file"
        )
        return storage

    def test_documents_private_and_multipart_item_order(self):
        import json

        advance = self.pay()
        data = dict(
            request_key=str(uuid4()),
            advance_id=str(advance.pk),
            date=str(today()),
            description="Itemized",
            items=[
                dict(
                    date=str(today()),
                    category="OTHER",
                    description="First",
                    amount="20.00",
                ),
                dict(
                    date=str(today()),
                    category="MEALS",
                    description="Second",
                    amount="30.00",
                ),
            ],
        )
        storage = self.storage()
        with patch("ledger.staff_ledger.get_document_storage", return_value=storage):
            result = self.api.post(
                f"/ledger/staff-ledger/staff/{self.staff.pk}/retirements/",
                {"payload": json.dumps(data), "item_1": self.file()},
                format="multipart",
            )
        self.assertEqual(result.status_code, 201, result.data)
        doc = StaffRetirement.objects.get(pk=result.data["id"]).documents.get()
        self.assertEqual(doc.item_position, 2)
        self.assertNotIn("storage_path", result.data["documents"][0])
        stream = BytesIO(b"private")
        storage.download.return_value = DownloadedDocument(
            file_object=stream, filename="support.png", mime_type="image/png"
        )
        with patch(
            "ledger.staff_api.get_document_storage", return_value=storage
        ) as provider:
            response = self.api.get(
                f"/ledger/staff-ledger/documents/{doc.pk}/download/"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        provider.assert_called_once_with("supabase")
        stream.close()

    def test_upload_failure_rolls_back_money(self):
        storage = self.storage()
        storage.upload.side_effect = DocumentStorageError("unavailable")
        with patch("ledger.staff_ledger.get_document_storage", return_value=storage):
            with self.assertRaises(ValidationError):
                post_money(
                    actor=self.actor,
                    staff_id=self.staff.pk,
                    payload=self.payload(),
                    files={"file": self.file()},
                )
        self.assertFalse(StaffLedgerEntry.objects.exists())
        self.assertFalse(CashBookEntry.objects.exists())
        self.assertFalse(CashBookJournal.objects.exists())

    def test_posted_records_are_immutable_and_cash_cannot_be_changed(self):
        entry = self.pay()
        with self.assertRaises(DatabaseError), transaction.atomic():
            StaffLedgerEntry.objects.filter(pk=entry.pk).update(amount=1)
        response = self.api.patch(
            f"/ledger/cash-book/{entry.cash_entry_id}/",
            {"version": 1, "amount": "2.00", "reason": "Try edit"},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)

    def test_future_and_backdated_settlements_rejected(self):
        with self.assertRaises(ValidationError):
            self.pay(date=str(today() + timedelta(days=1)))
        advance = self.pay()
        with self.assertRaises(ValidationError):
            self.pay(
                kind="RECEIVE",
                category="ADVANCE_RETURN",
                amount="1.00",
                related_entry=str(advance.pk),
                date=str(today() - timedelta(days=1)),
            )

    def test_supporting_file_cleanup_when_later_upload_fails(self):
        advance = self.pay()
        storage = self.storage()
        stored = storage.upload.return_value
        storage.upload.side_effect = [
            stored,
            DocumentStorageError("failed second upload"),
        ]
        payload = dict(
            request_key=str(uuid4()),
            advance_id=str(advance.pk),
            date=str(today()),
            description="Two expenses",
            items=[
                dict(
                    date=str(today()),
                    category="OTHER",
                    description="Expense",
                    amount="20.00",
                )
                for _ in range(2)
            ],
        )
        with patch("ledger.staff_ledger.get_document_storage", return_value=storage):
            with self.assertRaises(ValidationError):
                submit_retirement(
                    actor=self.actor,
                    staff_id=self.staff.pk,
                    payload=payload,
                    files={"item_0": self.file(), "item_1": self.file()},
                )
        self.assertFalse(StaffRetirement.objects.exists())
        storage.delete.assert_called_once_with(
            external_file_id=stored.external_file_id, storage_path=stored.storage_path
        )
        self.assertEqual(CashBookEntry.objects.count(), 1)

    def test_statement_uses_all_rows_not_page_and_keeps_currencies_separate(self):
        for i in range(31):
            self.pay(category="ALLOWANCE", amount="1.00")
        usd = LedgerAccount.objects.create(
            name="Staff USD", kind="BANK", currency="USD"
        )
        self.pay(
            category="LOAN", currency="USD", account_id=str(usd.pk), amount="200.00"
        )
        saved = statement(
            actor=self.actor,
            staff_id=self.staff.pk,
            payload=dict(
                request_key=str(uuid4()),
                date_from=str(today()),
                date_to=str(today()),
                currency="NGN",
            ),
        )
        self.assertEqual(len(saved.snapshot["transactions"]), 31)
        self.assertEqual(saved.snapshot["summary"]["paid"], "31.00")
        self.assertEqual(saved.snapshot["summary"]["closing_receivable"], "0.00")

    def test_view_alone_cannot_retire_approve_download_or_print(self):
        advance = self.pay()
        row = self.retirement(advance)
        self.staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="ledger", codename="view_staffledger"
            )
        )
        actor = get_user_model().objects.get(pk=self.staff.pk)
        self.api.force_authenticate(actor)
        base = "/ledger/staff-ledger/"
        self.assertEqual(
            self.api.post(
                base + f"retirements/{row.pk}/decision/", {}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.api.get(base + f"documents/{uuid4()}/download/").status_code, 403
        )
        self.assertEqual(
            self.api.get(base + f"staff/{self.staff.pk}/statements/").status_code, 403
        )
        with self.assertRaises(PermissionDenied):
            submit_retirement(actor=actor, staff_id=self.staff.pk, payload={})

    def test_individually_assigned_permissions_enable_staff_cash_entry(self):
        self.staff.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="ledger",
                codename__in=[
                    "view_staffledger",
                    "pay_staffledger",
                    "add_cashbookentry",
                ],
            )
        )
        actor = get_user_model().objects.get(pk=self.staff.pk)
        row = post_money(actor=actor, staff_id=self.staff.pk, payload=self.payload())
        self.assertEqual(row.actor_id, actor.pk)
        with self.assertRaises(PermissionDenied):
            post_money(
                actor=actor,
                staff_id=self.staff.pk,
                payload={**self.payload(), "kind": "RECEIVE", "category": "LEVY"},
            )

    def test_empty_or_unmatched_documents_are_rejected(self):
        with self.assertRaises(ValidationError):
            post_money(
                actor=self.actor,
                staff_id=self.staff.pk,
                payload=self.payload(),
                files={"file": SimpleUploadedFile("empty.png", b"")},
            )
        with self.assertRaises(ValidationError):
            post_money(
                actor=self.actor,
                staff_id=self.staff.pk,
                payload=self.payload(),
                files={"item_0": self.file()},
            )
        self.assertFalse(CashBookEntry.objects.exists())

    def test_source_choices_and_transaction_summary_api(self):
        advance = self.pay()
        self.pay(
            kind="RECEIVE",
            category="ADVANCE_RETURN",
            amount="25000.00",
            related_entry=str(advance.pk),
        )
        base = f"/ledger/staff-ledger/staff/{self.staff.pk}/"
        response = self.api.get(
            base + "choices/", {"mode": "retire", "currency": "NGN"}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["results"][0]["remaining"], "75000.00")
        self.assertEqual(response.data["results"][0]["staff_name"], self.staff.username)
        self.retirement(advance)
        self.assertEqual(
            self.api.get(base + "choices/", {"mode": "retire"}).data["count"], 0
        )
        response = self.api.get(base + "entries/", {"currency": "NGN"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["closing_receivable"], "75000.00")
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            self.api.post("/ledger/staff-ledger/", {}, format="json").status_code, 405
        )
