from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from django.test import TransactionTestCase
from django.db import close_old_connections
from django.contrib.auth import get_user_model
from rest_framework.exceptions import ValidationError
from . import test_tax_ledger as fixtures
from .tax_operations import decide, balances
from .models import TaxDecision


class TaxConcurrencyTests(TransactionTestCase):
    setUp = fixtures.TaxLedgerTests.setUp
    data = fixtures.TaxLedgerTests.data
    receipt = fixtures.TaxLedgerTests.receipt
    request = fixtures.TaxLedgerTests.request

    def parallel(self, jobs):
        barrier = Barrier(2)

        def worker(job):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrier.wait(timeout=20)
                try:
                    return job(actor)
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, jobs))

    def test_same_approval_posts_once(self):
        self.receipt()
        request = self.request("REMIT", "7500")
        payload = {"request_key": str(uuid4()), "approved": True, "reason": "Reviewed"}

        def job(actor):
            return decide(actor=actor, pk=request.pk, payload=payload).pk

        result = self.parallel([job, job])
        self.assertEqual(result[0], result[1])
        self.assertEqual(TaxDecision.objects.count(), 1)
        self.assertEqual(balances("NGN")["vat_remitted"], 7500)

    def test_competing_remittances_cannot_overpay(self):
        self.receipt()
        requests = [self.request("REMIT", "5000"), self.request("REMIT", "5000")]

        def job(request):
            def approve(actor):
                decide(
                    actor=actor,
                    pk=request.pk,
                    payload={
                        "request_key": str(uuid4()),
                        "approved": True,
                        "reason": "Reviewed",
                    },
                )
                return "saved"

            return approve

        self.assertCountEqual(
            self.parallel([job(r) for r in requests]), ["saved", "rejected"]
        )
        self.assertEqual(balances("NGN")["vat_remitted"], 5000)
