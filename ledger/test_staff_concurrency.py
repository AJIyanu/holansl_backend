from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from rest_framework.exceptions import ValidationError
from .models import LedgerAccount, StaffLedgerEntry, CashBookEntry, CashBookJournal
from .staff_ledger import post_money, submit_retirement, decide_retirement, balance
from .invoices import today


class StaffConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="staff-parallel",
            email="staff-parallel@example.test",
            password="test",
        )
        self.staff = get_user_model().objects.create_user(
            username="staff-target", is_staff=True
        )
        self.account = LedgerAccount.objects.create(
            name="Staff cash", kind="CASH", currency="NGN"
        )
        self.payload = dict(
            request_key=str(uuid4()),
            date=str(today()),
            kind="PAY",
            category="ADVANCE",
            currency="NGN",
            amount="100000.00",
            account_id=str(self.account.pk),
            description="Site advance",
        )

    def parallel(self, job):
        barrier = Barrier(2)

        def worker(_):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrier.wait(timeout=20)
                return job(actor)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, range(2)))

    def test_same_cash_submission_posts_once(self):
        ids = self.parallel(
            lambda actor: (
                post_money(actor=actor, staff_id=self.staff.pk, payload=self.payload).pk
            )
        )
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(StaffLedgerEntry.objects.count(), 1)
        self.assertEqual(CashBookEntry.objects.count(), 1)
        self.assertEqual(CashBookJournal.objects.count(), 1)

    def test_competing_returns_cannot_over_settle_advance(self):
        advance = post_money(
            actor=self.actor, staff_id=self.staff.pk, payload=self.payload
        )

        def pay(actor):
            try:
                post_money(
                    actor=actor,
                    staff_id=self.staff.pk,
                    payload={
                        **self.payload,
                        "request_key": str(uuid4()),
                        "kind": "RECEIVE",
                        "category": "ADVANCE_RETURN",
                        "related_entry": str(advance.pk),
                        "amount": "75000.00",
                    },
                )
                return "saved"
            except ValidationError:
                return "rejected"

        self.assertCountEqual(self.parallel(pay), ["saved", "rejected"])
        self.assertEqual(str(balance(advance)[0]), "25000.00")
        self.assertEqual(CashBookEntry.objects.count(), 2)

    def test_same_approval_posts_once_without_moving_cash(self):
        advance = post_money(
            actor=self.actor, staff_id=self.staff.pk, payload=self.payload
        )
        retirement = submit_retirement(
            actor=self.actor,
            staff_id=self.staff.pk,
            payload=dict(
                request_key=str(uuid4()),
                advance_id=str(advance.pk),
                date=str(today()),
                description="Expenses",
                items=[
                    dict(
                        date=str(today()),
                        category="TRANSPORT",
                        description="Travel",
                        amount="75000.00",
                    )
                ],
            ),
        )
        payload = dict(
            request_key=str(uuid4()),
            approved=True,
            date=str(today()),
            reason="Verified",
            expected_remaining="100000.00",
        )
        ids = self.parallel(
            lambda actor: (
                decide_retirement(actor=actor, pk=retirement.pk, payload=payload).pk
            )
        )
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(StaffLedgerEntry.objects.count(), 2)
        self.assertEqual(CashBookEntry.objects.count(), 1)
        self.assertEqual(CashBookJournal.objects.count(), 2)
