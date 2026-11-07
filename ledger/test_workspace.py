import uuid
from decimal import Decimal
from datetime import date
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import DatabaseError, transaction
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient
from crm.models import Party, PartyRole
from .models import (
    CashBookEntry,
    CashBookJournal,
    PartyLedgerTransaction,
    LedgerAccount,
    Reconciliation,
)
from .party_posting import post_party_transaction
from .operations import operate, recognized
from .reconciliation import snapshot, review
from .balances import remaining, remaining_balances


class WorkspaceTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="workspace", email="workspace@example.test", password="test"
        )
        self.party = Party.objects.create(display_name="Workspace party")
        for role in ["CLIENT", "SUPPLIER"]:
            PartyRole.objects.create(party=self.party, role=role)

    def post(self, **changes):
        return post_party_transaction(
            actor=self.actor,
            payload={
                "request_key": str(uuid.uuid4()),
                "party_id": str(self.party.pk),
                "ledger_kind": "sales",
                "transaction_type": "INVOICE",
                "currency": "NGN",
                "date": "2026-01-01",
                "amount": "100.00",
                "description": "Goods",
                **changes,
            },
        )

    def op(self, action, source=None, **changes):
        return operate(
            actor=self.actor,
            party_id=self.party.pk,
            kind="sales",
            payload={
                "request_key": str(uuid.uuid4()),
                "action": action,
                "date": "2026-01-10",
                "currency": "NGN",
                "reason": "Authorized correction",
                **({"transaction_id": str(source.pk)} if source else {}),
                **changes,
            },
        )

    def statement(self, **changes):
        return snapshot(
            actor=self.actor,
            party_id=self.party.pk,
            kind="sales",
            payload={
                "request_key": str(uuid.uuid4()),
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
                "currency": "NGN",
                **changes,
            },
        )

    def test_advance_reallocation_and_retry_do_not_duplicate_cash(self):
        payment = self.post(transaction_type="PAYMENT")
        first = self.post(date="2026-01-02")
        second = self.post(date="2026-01-03")
        key = str(uuid.uuid4())
        payload = {
            "allocations": [
                {"document_id": str(first.pk), "amount": "60.00"},
                {"document_id": str(second.pk), "amount": "40.00"},
            ],
            "request_key": key,
        }
        self.op("ALLOCATE", payment, **payload)
        self.op("ALLOCATE", payment, **payload)
        self.assertEqual(CashBookEntry.objects.count(), 1)
        self.assertEqual(CashBookJournal.objects.count(), 3)
        self.assertEqual(remaining(first), Decimal("40"))
        self.op(
            "ALLOCATE",
            payment,
            date="2026-01-11",
            allocations=[{"document_id": str(second.pk), "amount": "100.00"}],
        )
        self.assertEqual(remaining(first), Decimal("100"))
        self.assertEqual(remaining(second), Decimal("0"))
        self.assertEqual(remaining(first, date(2026, 1, 10)), Decimal("40"))
        with self.assertNumQueries(3):
            balances = remaining_balances([first, second, payment], date(2026, 1, 10))
        self.assertEqual(balances[first.pk], Decimal("40"))
        self.assertEqual(balances[second.pk], Decimal("60"))
        self.assertEqual(balances[payment.pk], Decimal("0"))
        with self.assertRaises(ValidationError):
            self.op("ALLOCATE", payment, date="2026-01-09", allocations=[])

    def test_failed_allocation_replacement_rolls_back_release(self):
        doc = self.post()
        payment = self.post(
            transaction_type="PAYMENT",
            allocations=[{"document_id": str(doc.pk), "amount": "100"}],
        )
        with self.assertRaises(ValidationError):
            self.op(
                "ALLOCATE",
                payment,
                allocations=[{"document_id": str(doc.pk), "amount": "101"}],
            )
        self.assertEqual(remaining(doc), 0)
        self.assertFalse(
            payment.allocations.first().release
            if hasattr(payment.allocations.first(), "release")
            else False
        )

    def test_credit_refund_and_revenue(self):
        doc = self.post()
        self.op("ACCEPTED", doc, amount="100")
        self.assertEqual(recognized(doc), 100)
        with self.assertRaises(ValidationError):
            self.op("CREDIT", doc, amount="20", date="2026-01-09")
        with self.assertRaises(ValidationError):
            self.op("REVERSE", doc)
        self.op("CREDIT", doc, amount="20")
        self.assertEqual(recognized(doc), 80)
        self.assertEqual(remaining(doc), 80)
        payment = self.post(transaction_type="PAYMENT", amount="100")
        self.op("REFUND", payment, amount="20")
        self.assertEqual(remaining(payment), 80)
        self.assertEqual(CashBookEntry.objects.filter(kind="EXPENSE").get().amount, 20)
        self.assertEqual(self.statement().snapshot["closing_balance"], "0.00")
        content = self.statement().snapshot
        self.assertEqual(content["invoices"], "100.00")
        self.assertEqual(content["payments"], "100.00")
        self.assertEqual(content["refunds"], "20.00")

    def test_reversal_preserves_original_and_cancellation_requires_rejection(self):
        doc = self.post()
        self.op("DISPATCHED", doc, amount="100")
        with self.assertRaises(ValidationError):
            self.op("REVERSE", doc)
        self.op("REJECTED", doc, amount="100")
        result = self.op("REVERSE", doc)
        reverse = PartyLedgerTransaction.objects.get(pk=result["id"])
        self.assertEqual(reverse.reverses_id, doc.pk)
        self.assertEqual(reverse.journal.debit_account, "DEFERRED_REVENUE")
        self.assertEqual(self.statement().snapshot["closing_balance"], "0.00")
        with self.assertRaises(DatabaseError), transaction.atomic():
            PartyLedgerTransaction.objects.filter(pk=doc.pk).delete()

    def test_payment_reversal_releases_settlement_and_reverses_cash(self):
        doc = self.post()
        payment = self.post(
            transaction_type="PAYMENT",
            allocations=[{"document_id": str(doc.pk), "amount": "100"}],
        )
        self.op("REVERSE", payment)
        self.assertEqual(remaining(doc), 100)
        self.assertEqual(remaining(payment), 0)
        self.assertEqual(CashBookEntry.objects.count(), 2)
        self.assertEqual(self.statement().snapshot["closing_balance"], "100.00")

    def test_snapshot_full_history_prior_balance_and_immutability(self):
        self.post(date="2025-12-31")
        for _ in range(26):
            self.post(amount="1")
        statement = self.statement()
        self.assertEqual(len(statement.snapshot["transactions"]), 26)
        self.assertEqual(statement.snapshot["opening_balance"], "100.00")
        self.assertEqual(statement.snapshot["closing_balance"], "126.00")
        self.post(amount="10")
        statement.refresh_from_db()
        self.assertEqual(statement.snapshot["closing_balance"], "126.00")
        with self.assertRaises(DatabaseError), transaction.atomic():
            Reconciliation.objects.filter(pk=statement.pk).update(snapshot={})
        payload = {
            "request_key": str(uuid.uuid4()),
            "status": "DISPUTED",
            "notes": "One delivery under review.",
        }
        first = review(actor=self.actor, statement=statement, payload=payload)
        self.assertEqual(
            review(actor=self.actor, statement=statement, payload=payload).pk, first.pk
        )

    def test_currency_separation_and_matching_accounts(self):
        account = LedgerAccount.objects.get(currency="USD", kind="BANK")
        self.post(currency="USD", amount="50")
        self.post(
            currency="USD",
            amount="20",
            transaction_type="PAYMENT",
            account_id=str(account.pk),
        )
        self.assertEqual(
            self.statement(currency="USD").snapshot["closing_balance"], "30.00"
        )
        self.assertEqual(self.statement().snapshot["closing_balance"], "0.00")
        with self.assertRaises(ValidationError):
            self.post(transaction_type="PAYMENT", account_id=str(account.pk))
        api = APIClient()
        api.force_authenticate(self.actor)
        data = api.get("/ledger/cash-book/?currency=USD&year=2026").data
        self.assertEqual(data["summary"]["closing_balance"], "20.00")
        self.assertEqual(data["results"][0]["currency"], "USD")

    def test_permissions_enforced_by_services(self):
        self.actor = get_user_model().objects.create_user(
            username="noaccess", is_staff=True
        )
        with self.assertRaises(PermissionDenied):
            self.statement()
        with self.assertRaises(PermissionDenied):
            self.op("ADJUST_UP", amount="10")

    def test_signed_adjustments(self):
        self.op("ADJUST_UP", amount="15")
        self.op("ADJUST_DOWN", amount="5")
        self.assertEqual(self.statement().snapshot["closing_balance"], "10.00")
