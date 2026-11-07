import uuid
from decimal import Decimal
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError, transaction, close_old_connections
from django.test import TestCase, TransactionTestCase
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.test import APIClient

from crm.models import Party, PartyRole
from .models import PartyLedgerTransaction, CashBookEntry, CashBookJournal
from .party_posting import post_party_transaction
from .services import revise_entry


class PostingTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="poster", email="poster@example.test", password="test"
        )
        self.party = Party.objects.create(display_name="Test party")
        for role in ["CLIENT", "SUPPLIER"]:
            PartyRole.objects.create(party=self.party, role=role)

    def payload(self, **changes):
        return {
            "request_key": str(uuid.uuid4()),
            "party_id": str(self.party.pk),
            "ledger_kind": "sales",
            "transaction_type": "INVOICE",
            "currency": "NGN",
            "date": "2026-01-02",
            "amount": "100.00",
            "description": "Test posting",
            **changes,
        }

    def post(self, **changes):
        return post_party_transaction(actor=self.actor, payload=self.payload(**changes))

    def test_posting_pairs_and_cash_link(self):
        for kind, event, debit, credit in [
            ("sales", "INVOICE", "RECEIVABLE", "DEFERRED_REVENUE"),
            ("purchase", "BILL", "PURCHASE_CLEARING", "PAYABLE"),
            ("sales", "PAYMENT", "CASH", "RECEIVABLE"),
            ("purchase", "PAYMENT", "PAYABLE", "CASH"),
        ]:
            record = self.post(ledger_kind=kind, transaction_type=event)
            self.assertEqual(
                (record.journal.debit_account, record.journal.credit_account),
                (debit, credit),
            )
            self.assertEqual(record.journal.amount, record.amount)
            self.assertEqual(record.journal.entry_id, record.cash_entry_id)
            self.assertEqual(bool(record.cash_entry_id), event == "PAYMENT")
        self.assertEqual(CashBookEntry.objects.count(), 2)
        self.assertEqual(CashBookJournal.objects.count(), 4)

    def test_idempotency_and_conflict(self):
        payload = self.payload(transaction_type="PAYMENT")
        first = post_party_transaction(actor=self.actor, payload=payload)
        again = post_party_transaction(actor=self.actor, payload=payload)
        self.assertEqual(first.pk, again.pk)
        payload["amount"] = "101.00"
        with self.assertRaises(ValidationError):
            post_party_transaction(actor=self.actor, payload=payload)
        self.assertEqual(CashBookEntry.objects.count(), 1)
        self.assertEqual(CashBookJournal.objects.count(), 1)

    def test_failure_rolls_back_all_records(self):
        with patch(
            "ledger.party_posting.CashBookJournal.objects.create",
            side_effect=RuntimeError("failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.post(transaction_type="PAYMENT")
        self.assertFalse(PartyLedgerTransaction.objects.exists())
        self.assertFalse(CashBookEntry.objects.exists())
        self.assertFalse(CashBookJournal.objects.exists())

    def test_invalid_inputs(self):
        for changes in [
            {"amount": "0"},
            {"amount": "1.001"},
            {"amount": "NaN"},
            {"currency": "EUR"},
            {"transaction_type": "BILL"},
            {"description": " "},
        ]:
            with self.assertRaises(ValidationError):
                self.post(**changes)
        self.party.is_archived = True
        self.party.save()
        with self.assertRaises(ValidationError):
            self.post()
        self.assertFalse(PartyLedgerTransaction.objects.exists())

    def test_permissions(self):
        user = get_user_model().objects.create_user(
            username="reader", email="reader@example.test", is_staff=True
        )
        with self.assertRaises(PermissionDenied):
            post_party_transaction(actor=user, payload=self.payload())

    def test_payment_requires_cash_permission_and_history_is_read_only(self):
        user = get_user_model().objects.create_user(
            username="limited", email="limited@example.test", is_staff=True
        )
        user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="ledger",
                codename__in=["view_salesledger", "add_salestransaction"],
            )
        )
        post_party_transaction(actor=user, payload=self.payload())
        with self.assertRaises(PermissionDenied):
            post_party_transaction(
                actor=user, payload=self.payload(transaction_type="PAYMENT")
            )
        client = APIClient()
        client.force_authenticate(user)
        self.assertEqual(
            client.get(
                f"/ledger/party-ledgers/sales/parties/{self.party.pk}/transactions/"
            ).status_code,
            200,
        )
        self.assertEqual(
            client.get(
                f"/ledger/party-ledgers/purchase/parties/{self.party.pk}/transactions/"
            ).status_code,
            403,
        )

    def test_immutable_and_linked_cash_guard(self):
        record = self.post(transaction_type="PAYMENT")
        with self.assertRaises(ValidationError):
            revise_entry(
                record.cash_entry_id,
                {"version": 1, "reason": "Change", "amount": Decimal("1")},
                self.actor,
            )
        with self.assertRaises(DatabaseError), transaction.atomic():
            CashBookEntry.objects.filter(pk=record.cash_entry_id).update(
                amount=Decimal("1")
            )
        with self.assertRaises(DatabaseError), transaction.atomic():
            PartyLedgerTransaction.objects.filter(pk=record.pk).update(
                amount=Decimal("1")
            )

    def test_scoped_history_running_balances_and_dates(self):
        self.post(date="2025-12-31", amount="50.00")
        self.post()
        self.post(transaction_type="PAYMENT", amount="30.00")
        self.post(ledger_kind="purchase", transaction_type="BILL", amount="999.00")
        client = APIClient()
        client.force_authenticate(self.actor)
        path = f"/ledger/party-ledgers/sales/parties/{self.party.pk}/transactions/"
        response = client.get(path + "?year=2026&page_size=1&page=2")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.data["summary"]["opening_balance"], "50.00")
        self.assertEqual(response.data["summary"]["closing_balance"], "120.00")
        self.assertEqual(response.data["results"][0]["running_balance"], "120.00")
        self.assertEqual(client.post(path, {}, format="json").status_code, 400)
        self.assertEqual(client.get(path + "?currency=GBP").status_code, 200)
        self.party.is_archived = True
        self.party.save()
        self.assertEqual(client.get(path).status_code, 200)
        lookup = client.get("/ledger/party-ledgers/sales/parties/")
        self.assertIn(str(self.party.pk), [row["id"] for row in lookup.data["results"]])
        client.force_authenticate(None)
        self.assertIn(client.get(path).status_code, [401, 403])


class ConcurrentPostingTests(TransactionTestCase):
    def test_simultaneous_retries_post_once(self):
        from .models import LedgerAccount, PRIMARY_CASH_ID

        LedgerAccount.objects.get_or_create(
            pk=PRIMARY_CASH_ID,
            defaults={"name": "Primary", "kind": "CASH", "currency": "NGN"},
        )
        user = get_user_model().objects.create_superuser(
            username="concurrent", email="concurrent@example.test", password="test"
        )
        party = Party.objects.create(display_name="Concurrent party")
        PartyRole.objects.create(party=party, role="CLIENT")
        payload = {
            "request_key": str(uuid.uuid4()),
            "party_id": str(party.pk),
            "ledger_kind": "sales",
            "transaction_type": "PAYMENT",
            "date": "2026-01-01",
            "amount": "1.00",
            "description": "Retry test",
        }
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=user.pk)
                barrier.wait(timeout=10)
                return post_party_transaction(actor=actor, payload=payload).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            ids = [future.result(timeout=20) for future in futures]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(PartyLedgerTransaction.objects.count(), 1)
        self.assertEqual(CashBookEntry.objects.count(), 1)
        self.assertEqual(CashBookJournal.objects.count(), 1)
