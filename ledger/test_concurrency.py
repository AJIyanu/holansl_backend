"""Real PostgreSQL transactions must serialize competing allocations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import TransactionTestCase
from rest_framework.exceptions import ValidationError

from ledger import models as m
from ledger.services import settlement
from ledger.tests import LedgerTests


class LedgerConcurrencyTests(TransactionTestCase):
    serialized_rollback = True
    create = LedgerTests.create
    post = LedgerTests.post
    invoice = LedgerTests.invoice
    receipt = LedgerTests.receipt

    def setUp(self):
        # No outbound notifications are exercised by the concurrency test.
        patcher = patch("ledger.integrations.workflow.schedule")
        patcher.start()
        self.addCleanup(patcher.stop)
        LedgerTests.setUpTestData.__func__(type(self))

    def _fixture_teardown(self):
        super()._fixture_teardown()
        for alias in self._databases_names(include_mirrors=False):
            conn = connections[alias]
            conn.creation.deserialize_db_from_string(conn._test_serialized_contents)

    def test_two_allocations_cannot_spend_one_receipt_twice(self):
        invoice = self.invoice(value="100")
        receipt = self.receipt("100")
        barrier = Barrier(2)

        def allocate():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.user.pk)
                source = m.SettlementSource.objects.get(record=receipt.pk)
                target = m.Invoice.objects.get(record=invoice.pk)
                barrier.wait(timeout=10)
                try:
                    settlement.allocate(
                        actor, source, target, Decimal(60), date(2026, 9, 1)
                    )
                    return "allocated"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: allocate(), range(2)))
        self.assertCountEqual(results, ["allocated", "rejected"])
        self.assertEqual(settlement.invoice_due(invoice.invoice), Decimal(40))
        self.assertEqual(
            settlement.source_available(receipt.settlement_source), Decimal(40)
        )
