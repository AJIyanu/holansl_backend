from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase
from crm.models import Party, PartyRole
from .models import SalesReceipt, LedgerAccount, CashBookEntry, CashBookJournal
from .invoices import today, save_invoice, invoice_action
from .receipts import create_receipt


class ReceiptConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="receipt-race", email="receipt-race@example.test", password="test"
        )
        self.party = Party.objects.create(display_name="Concurrent Client")
        PartyRole.objects.create(party=self.party, role="CLIENT")
        self.account = LedgerAccount.objects.create(
            name="Receipt cash", kind="CASH", currency="NGN"
        )
        self.payload = {
            "request_key": str(uuid4()),
            "date": str(today()),
            "currency": "NGN",
            "billed_to": "Concurrent Client",
            "account_id": str(self.account.pk),
            "payment_method": "CASH",
            "vat_paid": True,
            "withholding_applied": True,
            "items": [
                {
                    "description": "Switch",
                    "quantity": "1",
                    "unit": "pcs",
                    "unit_price": "100000",
                }
            ],
        }

    def parallel(self, jobs):
        barrier = Barrier(2)

        def worker(job):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrier.wait(timeout=15)
                return job(actor)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, jobs))

    def test_duplicate_receipt_submission_posts_once(self):
        def submit(actor):
            return create_receipt(
                actor=actor, party_id=self.party.pk, payload=self.payload
            ).pk

        ids = self.parallel([submit, submit])
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(SalesReceipt.objects.count(), 1)
        self.assertEqual(CashBookEntry.objects.count(), 1)
        self.assertEqual(CashBookJournal.objects.count(), 3)

    def test_invoice_and_receipt_share_safe_daily_sequence(self):
        invoice = save_invoice(
            actor=self.actor, party_id=self.party.pk, payload=self.payload
        )

        def confirm(actor):
            return invoice_action(
                actor=actor,
                invoice_id=invoice.pk,
                action="confirm",
                payload={"request_key": str(uuid4()), "version": 1},
            ).reference

        def receive(actor):
            return create_receipt(
                actor=actor, party_id=self.party.pk, payload=self.payload
            ).reference

        references = self.parallel([confirm, receive])
        self.assertEqual(len(set(references)), 2)
        self.assertEqual(sorted(r.split("-")[-1] for r in references), ["001", "002"])
