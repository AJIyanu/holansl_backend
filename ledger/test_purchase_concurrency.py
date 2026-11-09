from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from crm.models import Party, PartyRole
from .models import (
    LedgerAccount,
    PurchasePayment,
    CashBookEntry,
    CashBookJournal,
    PartyLedgerTransaction,
)
from .purchases import create_payment, save_bill, deliver_bill
from .invoices import today


class PurchaseConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-concurrent",
            email="purchase-concurrent@example.test",
            password="test",
        )
        self.party = Party.objects.create(display_name="Concurrent supplier")
        PartyRole.objects.create(party=self.party, role="SUPPLIER")
        self.account = LedgerAccount.objects.create(
            name="Purchase cash", kind="CASH", currency="NGN"
        )
        self.payload = {
            "request_key": str(uuid4()),
            "date": str(today()),
            "currency": "NGN",
            "reference": "SUP-1",
            "items": [
                {
                    "description": "Switch",
                    "quantity": "2",
                    "unit": "pcs",
                    "unit_price": "50000",
                }
            ],
            "account_id": str(self.account.pk),
            "payment_method": "CASH",
            "tax_enabled": True,
            "withholding_enabled": True,
        }

    def parallel(self, job):
        barrier = Barrier(2)

        def worker(_):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrier.wait(timeout=15)
                return job(actor)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, range(2)))

    def test_same_payment_posts_once(self):
        ids = self.parallel(
            lambda actor: (
                create_payment(
                    actor=actor, party_id=self.party.pk, payload=self.payload
                ).pk
            )
        )
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(PurchasePayment.objects.count(), 1)
        self.assertEqual(CashBookEntry.objects.count(), 1)
        self.assertEqual(CashBookJournal.objects.count(), 3)

    def test_same_delivery_posts_once(self):
        bill = save_bill(actor=self.actor, party_id=self.party.pk, payload=self.payload)
        d = {"request_key": str(uuid4()), "version": 1, "delivery_date": str(today())}
        ids = self.parallel(
            lambda actor: (
                deliver_bill(actor=actor, pk=bill.pk, payload=d).transaction_id
            )
        )
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(PartyLedgerTransaction.objects.count(), 1)
        self.assertEqual(CashBookJournal.objects.count(), 2)
        self.assertFalse(CashBookEntry.objects.exists())
