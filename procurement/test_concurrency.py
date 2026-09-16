"""PostgreSQL concurrency: two purchases cannot consume the same capacity."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import TransactionTestCase
from rest_framework.exceptions import ValidationError

from . import models as m, services as s
from .tests import ProcurementTests


class ProcurementConcurrencyTests(TransactionTestCase):
    serialized_rollback = True

    def _fixture_teardown(self):
        # TransactionTestCase flushes migration-created CRM master data. Restore
        # the test runner's baseline so later --keepdb suites retain those seeds.
        super()._fixture_teardown()
        for alias in self._databases_names(include_mirrors=False):
            connection = connections[alias]
            connection.creation.deserialize_db_from_string(
                connection._test_serialized_contents
            )

    doc = ProcurementTests.doc
    offer = ProcurementTests.offer
    po = ProcurementTests.po

    def setUp(self):
        ProcurementTests.setUpTestData.__func__(type(self))
        ProcurementTests.setUp(self)

    def test_concurrent_purchases_cannot_exceed_ordered_quantity(self):
        attempt, offer = self.po()
        barrier = Barrier(2)
        user_id, request_id = self.user.id, self.request.id

        def purchase():
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=user_id)
                data = {
                    "request": m.ProcurementRequest.objects.get(pk=request_id),
                    "supplier": offer.quotation.supplier,
                    "currency": "NGN",
                    "lines": [
                        {
                            "attempt": m.FulfilmentAttempt.objects.get(pk=attempt.id),
                            "supplier_offer": m.SupplierQuotationLine.objects.get(
                                pk=offer.id
                            ),
                            "quantity": Decimal(6),
                            "unit_price": Decimal(100),
                        }
                    ],
                }
                barrier.wait(timeout=10)
                try:
                    s.create_purchase(user, data)
                    return "created"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: purchase(), range(2)))
        self.assertCountEqual(results, ["created", "rejected"])
        self.assertEqual(s.total(attempt.purchase_lines.all()), Decimal(6))
