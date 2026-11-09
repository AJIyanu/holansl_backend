import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from crm.models import Party, PartyRole
from .models import (
    CashBookEntry,
    CashBookJournal,
    PartyLedgerTransaction,
    PartyPaymentAllocation,
)
from .party_posting import post_party_transaction


class PartyEntryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="entry-test", email="entry@example.test", password="test"
        )
        self.party = Party.objects.create(display_name="Shared-table client")
        for role in ["CLIENT", "SUPPLIER"]:
            PartyRole.objects.create(party=self.party, role=role)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.path = f"/ledger/party-ledgers/sales/parties/{self.party.pk}/transactions/"

    def data(self, **changes):
        return {
            "request_key": str(uuid.uuid4()),
            "transaction_type": "INVOICE",
            "currency": "NGN",
            "date": "2026-01-10",
            "amount": "100.00",
            "description": "Document",
            "reference": str(uuid.uuid4()),
            **changes,
        }

    def post(self, **changes):
        if changes.get("transaction_type", "INVOICE") == "INVOICE":
            # Existing delivered-invoice fixture; public invoice creation now uses documents.
            from .party_ledgers import PartyTransactionSerializer

            row = post_party_transaction(
                actor=self.user,
                payload={
                    **self.data(**changes),
                    "party_id": str(self.party.pk),
                    "ledger_kind": "sales",
                },
            )
            return PartyTransactionSerializer(row).data
        response = self.client.post(self.path, self.data(**changes), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_document_required_reference_and_duplicate_reference(self):
        self.assertEqual(
            self.client.post(
                self.path, self.data(reference=""), format="json"
            ).status_code,
            400,
        )
        self.post(reference="INV-01")
        self.assertEqual(
            self.client.post(
                self.path, self.data(reference="inv-01"), format="json"
            ).status_code,
            400,
        )
        self.assertEqual(PartyLedgerTransaction.objects.count(), 1)
        self.assertEqual(CashBookEntry.objects.count(), 0)

    def test_partial_multi_document_payment_and_retry(self):
        first = self.post()
        second = self.post(amount="50.00")
        payload = self.data(
            transaction_type="PAYMENT",
            amount="120.00",
            allocations=[
                {"document_id": first["id"], "amount": "100.00"},
                {"document_id": second["id"], "amount": "20.00"},
            ],
        )
        posted = self.client.post(self.path, payload, format="json")
        self.assertEqual(posted.status_code, 201, posted.data)
        payload["allocations"].reverse()
        repeated = self.client.post(self.path, payload, format="json")
        self.assertEqual(posted.data["id"], repeated.data["id"])
        self.assertEqual(CashBookEntry.objects.count(), 1)
        self.assertEqual(CashBookJournal.objects.count(), 3)
        self.assertEqual(PartyPaymentAllocation.objects.count(), 2)
        outstanding = self.client.get(
            self.path.replace("transactions/", "outstanding/")
        )
        self.assertEqual(outstanding.data["count"], 1)
        self.assertEqual(outstanding.data["results"][0]["outstanding"], "30.00")
        history = self.client.get(self.path + "?year=2026")
        self.assertEqual(history.data["summary"]["closing_balance"], "30.00")
        self.assertEqual(history.data["results"][-1]["running_balance"], "30.00")
        self.assertEqual(history.data["results"][-1]["remaining_amount"], "0.00")
        payload["allocations"][0]["amount"] = "10.00"
        self.assertEqual(
            self.client.post(self.path, payload, format="json").status_code, 400
        )

    def test_unallocated_advance_and_payment_remainder(self):
        advance = self.post(transaction_type="PAYMENT", reference="", amount="25.00")
        self.assertEqual(advance["remaining_amount"], "25.00")
        self.assertEqual(
            self.client.get(self.path + "?year=2026").data["summary"][
                "closing_balance"
            ],
            "-25.00",
        )
        document = self.post(amount="10.00")
        payment = self.post(
            transaction_type="PAYMENT",
            amount="15.00",
            allocations=[{"document_id": document["id"], "amount": "10.00"}],
        )
        self.assertEqual(payment["remaining_amount"], "5.00")

    def test_overallocation_wrong_party_type_and_date_rejected(self):
        document = self.post()
        other = Party.objects.create(display_name="Other party")
        PartyRole.objects.create(party=other, role="CLIENT")
        foreign = post_party_transaction(
            actor=self.user,
            payload={**self.data(), "party_id": str(other.pk), "ledger_kind": "sales"},
        )
        for changes in [
            {
                "amount": "10.00",
                "allocations": [{"document_id": document["id"], "amount": "11.00"}],
            },
            {
                "amount": "200.00",
                "allocations": [{"document_id": document["id"], "amount": "101.00"}],
            },
            {"allocations": [{"document_id": str(foreign.pk), "amount": "1.00"}]},
            {
                "date": "2026-01-09",
                "allocations": [{"document_id": document["id"], "amount": "1.00"}],
            },
            {"allocations": [{"document_id": document["id"], "amount": "1.00"}] * 2},
        ]:
            self.assertEqual(
                self.client.post(
                    self.path,
                    self.data(transaction_type="PAYMENT", **changes),
                    format="json",
                ).status_code,
                400,
            )
        self.assertFalse(CashBookEntry.objects.exists())
        self.assertFalse(PartyPaymentAllocation.objects.exists())

    def test_allocations_failure_rolls_back_cash_journal_and_payment(self):
        document = self.post()
        with patch(
            "ledger.party_posting.PartyPaymentAllocation.objects.bulk_create",
            side_effect=RuntimeError("failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    self.path,
                    self.data(
                        transaction_type="PAYMENT",
                        allocations=[{"document_id": document["id"], "amount": "1.00"}],
                    ),
                    format="json",
                )
        self.assertEqual(PartyLedgerTransaction.objects.count(), 1)
        self.assertEqual(CashBookJournal.objects.count(), 1)
        self.assertFalse(CashBookEntry.objects.exists())

    def test_read_only_staff_cannot_post_and_payment_requires_cash_permission(self):
        staff = get_user_model().objects.create_user(
            username="read-entry", email="read-entry@example.test", is_staff=True
        )
        staff.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="ledger", codename="view_salesledger"
            )
        )
        self.client.force_authenticate(staff)
        self.assertEqual(
            self.client.post(self.path, self.data(), format="json").status_code, 403
        )
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="ledger", codename="add_salestransaction"
            )
        )
        self.client.force_authenticate(get_user_model().objects.get(pk=staff.pk))
        self.assertEqual(
            self.client.post(self.path, self.data(), format="json").status_code, 400
        )
        self.assertEqual(
            self.client.post(
                self.path, self.data(transaction_type="PAYMENT"), format="json"
            ).status_code,
            403,
        )

    def test_purchase_and_route_scope(self):
        self.path = self.path.replace("/sales/", "/purchase/")
        self.assertEqual(
            self.client.post(
                self.path, self.data(transaction_type="BILL"), format="json"
            ).status_code,
            400,
        )
        response = self.client.post(
            f"/ledger/purchase-bills/parties/{self.party.pk}/",
            {
                **self.data(),
                "ledger_kind": "sales",
                "party_id": str(uuid.uuid4()),
                "delivered": True,
                "items": [
                    {
                        "description": "Supplier goods",
                        "quantity": "1",
                        "unit": "pcs",
                        "unit_price": "100.00",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        from .party_ledgers import PartyTransactionSerializer

        bill = PartyTransactionSerializer(
            PartyLedgerTransaction.objects.get(pk=response.data["transaction"])
        ).data
        self.assertEqual(bill["party"], self.party.pk)
        self.assertEqual(bill["ledger_kind"], "purchase")
        payment = self.post(
            transaction_type="PAYMENT",
            allocations=[{"document_id": bill["id"], "amount": "40.00"}],
        )
        self.assertEqual(payment["debit_account"], "PAYABLE")
        self.assertEqual(payment["credit_account"], "CASH")

    def test_database_allocation_guards(self):
        document = self.post()
        payment = self.post(transaction_type="PAYMENT", amount="10.00")
        with self.assertRaises(DatabaseError), transaction.atomic():
            PartyPaymentAllocation.objects.create(
                payment_id=payment["id"],
                document_id=document["id"],
                amount="11.00",
                effective_date=payment["date"],
            )
        allocation = PartyPaymentAllocation.objects.create(
            payment_id=payment["id"],
            document_id=document["id"],
            amount="5.00",
            effective_date=payment["date"],
        )
        with self.assertRaises(DatabaseError), transaction.atomic():
            PartyPaymentAllocation.objects.filter(pk=allocation.pk).update(
                amount="6.00"
            )


class ConcurrentAllocationTests(TransactionTestCase):
    def test_two_payments_cannot_overpay_same_document(self):
        from .models import LedgerAccount, PRIMARY_CASH_ID

        LedgerAccount.objects.get_or_create(
            pk=PRIMARY_CASH_ID,
            defaults={"name": "Primary", "kind": "CASH", "currency": "NGN"},
        )
        user = get_user_model().objects.create_superuser(
            username="parallel-allocate",
            email="parallel-allocate@example.test",
            password="test",
        )
        party = Party.objects.create(display_name="Concurrent allocation party")
        PartyRole.objects.create(party=party, role="CLIENT")
        base = {
            "party_id": str(party.pk),
            "ledger_kind": "sales",
            "date": "2026-01-01",
            "amount": "100.00",
            "description": "Test",
        }
        document = post_party_transaction(
            actor=user,
            payload={
                **base,
                "request_key": str(uuid.uuid4()),
                "transaction_type": "INVOICE",
            },
        )
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=user.pk)
                barrier.wait(timeout=10)
                try:
                    post_party_transaction(
                        actor=actor,
                        payload={
                            **base,
                            "amount": "70.00",
                            "request_key": str(uuid.uuid4()),
                            "transaction_type": "PAYMENT",
                            "allocations": [
                                {"document_id": str(document.pk), "amount": "70.00"}
                            ],
                        },
                    )
                    return True
                except ValidationError:
                    return False
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(worker) for _ in range(2)]
            self.assertEqual(
                sorted(future.result(timeout=30) for future in results), [False, True]
            )
        self.assertEqual(PartyPaymentAllocation.objects.count(), 1)
        self.assertEqual(CashBookEntry.objects.count(), 1)
