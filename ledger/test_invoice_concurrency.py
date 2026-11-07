from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase
from crm.models import Party, PartyRole
from .models import PartyLedgerTransaction, InvoiceClientCode, CashBookEntry
from .invoices import save_invoice, invoice_action, today


class InvoiceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="invoice-concurrent",
            email="invoice-concurrent@example.test",
            password="test",
        )
        self.party = Party.objects.create(display_name="Concurrent Client")
        PartyRole.objects.create(party=self.party, role="CLIENT")

    def invoice(self):
        return save_invoice(
            actor=self.actor,
            party_id=self.party.pk,
            payload={
                "request_key": str(uuid4()),
                "date": str(today()),
                "currency": "NGN",
                "billed_to": "Concurrent Client",
                "items": [
                    {
                        "description": "Switch",
                        "quantity": "1",
                        "unit": "each",
                        "unit_price": "100",
                    }
                ],
            },
        )

    def parallel(self, jobs):
        barrier = Barrier(2)

        def worker(job):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrier.wait(timeout=15)
                result = invoice_action(actor=actor, **job)
                return result.reference, result.posted_transaction_id
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, jobs))

    def test_concurrent_confirmation_has_unique_daily_numbers_and_one_client_code(self):
        first, second = self.invoice(), self.invoice()
        results = self.parallel(
            [
                {
                    "invoice_id": row.pk,
                    "action": "confirm",
                    "payload": {"request_key": str(uuid4()), "version": 1},
                }
                for row in (first, second)
            ]
        )
        self.assertEqual(sorted(r[0].split("-")[-1] for r in results), ["001", "002"])
        self.assertEqual(InvoiceClientCode.objects.count(), 1)
        self.assertFalse(PartyLedgerTransaction.objects.exists())

    def test_concurrent_delivery_retry_posts_once(self):
        invoice = self.invoice()
        invoice_action(
            actor=self.actor,
            invoice_id=invoice.pk,
            action="confirm",
            payload={"request_key": str(uuid4()), "version": 1},
        )
        job = {
            "invoice_id": invoice.pk,
            "action": "deliver",
            "payload": {
                "request_key": str(uuid4()),
                "version": 1,
                "delivery_date": str(today()),
            },
        }
        results = self.parallel([job, job])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(PartyLedgerTransaction.objects.count(), 1)
        self.assertFalse(CashBookEntry.objects.exists())
