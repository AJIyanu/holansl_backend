import uuid
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission, Group
from django.db import DatabaseError, transaction
from django.test import TestCase, SimpleTestCase
from django.urls import resolve, Resolver404
from rest_framework.test import APIClient

from accounts.serializers import format_permission
from .models import CashBookEntry, CashBookJournal


class RetiredLedgerTests(SimpleTestCase):
    def test_old_endpoints_remain_removed(self):
        for path in [
            "/ledger/invoices/",
            "/ledger/capabilities/",
            "/ledger/withholdings/",
        ]:
            with self.assertRaises(Resolver404):
                resolve(path)


class CashBookTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="cash-test", email="cash@example.test", is_staff=True
        )
        self.client = APIClient()
        self.grant(
            "view_cashbookentry",
            "add_cashbookentry",
            "change_cashbookentry",
            "delete_cashbookentry",
            "view_cashbookjournal",
        )

    def grant(self, *codes):
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="ledger", codename__in=codes
            )
        )
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_authenticate(self.user)

    def payload(self, **changes):
        return {
            "date": "2026-01-10",
            "kind": "RECEIPT",
            "amount": "100.00",
            "description": "Cash receipt",
            "request_key": str(uuid.uuid4()),
            **changes,
        }

    def create(self, **changes):
        response = self.client.post(
            "/ledger/cash-book/", self.payload(**changes), format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_zero_opening_calendar_year(self):
        response = self.client.get("/ledger/cash-book/?year=2026")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["summary"],
            {
                "currency": "NGN",
                "year": 2026,
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
                "opening_balance": "0.00",
                "debit": "0.00",
                "credit": "0.00",
                "closing_balance": "0.00",
            },
        )

    def test_income_expense_and_capital_post_balanced_pairs(self):
        for kind, debit, credit in [
            ("RECEIPT", "CASH", "INCOME"),
            ("EXPENSE", "EXPENSE", "CASH"),
            ("CAPITAL", "CASH", "CAPITAL"),
        ]:
            entry = self.create(kind=kind)
            journal = CashBookJournal.objects.get(entry_id=entry["id"])
            self.assertEqual(
                (journal.debit_account, journal.credit_account, journal.amount),
                (debit, credit, Decimal("100")),
            )
            self.assertEqual(journal.actor, self.user)

    def test_permission_matrix_and_format(self):
        entry = self.create()
        url = f"/ledger/cash-book/{entry['id']}/"
        self.grant()
        for method, path, data in [
            ("get", "/ledger/cash-book/", {}),
            ("get", url, {}),
            ("get", "/ledger/cash-book-journal/", {}),
            ("post", "/ledger/cash-book/", self.payload()),
            ("patch", url, {"version": 1, "reason": "Fix", "amount": "25.00"}),
            ("delete", url, {"version": 1, "reason": "Duplicate"}),
        ]:
            response = getattr(self.client, method)(path, data, format="json")
            self.assertEqual(response.status_code, 403, (method, response.data))
        self.grant("view_cashbookentry")
        self.assertEqual(self.client.get(url).status_code, 200)
        for method in ["post", "patch", "delete"]:
            self.assertEqual(
                getattr(self.client, method)(
                    url if method != "post" else "/ledger/cash-book/", {}, format="json"
                ).status_code,
                403,
            )
        self.assertEqual(self.client.get("/ledger/cash-book-journal/").status_code, 403)
        permission = Permission.objects.get(
            content_type__app_label="ledger", codename="change_cashbookentry"
        )
        self.assertEqual(format_permission(permission), "ledger.cashbookentry.edit")

    def test_mutation_permission_alone_denied(self):
        self.grant("add_cashbookentry")
        self.assertEqual(
            self.client.post(
                "/ledger/cash-book/", self.payload(), format="json"
            ).status_code,
            403,
        )

    def test_role_permissions_are_used(self):
        self.grant()
        role, _ = Group.objects.get_or_create(name="Cash book clerk")
        role.permissions.set(
            Permission.objects.filter(
                content_type__app_label="ledger", codename="view_cashbookentry"
            )
        )
        self.user.groups.add(role)
        self.client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
        self.assertEqual(self.client.get("/ledger/cash-book/").status_code, 200)
        role.permissions.clear()
        self.client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
        self.assertEqual(self.client.get("/ledger/cash-book/").status_code, 403)

    def test_nonstaff_anonymous_and_superuser(self):
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.get("/ledger/cash-book/").status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get("/ledger/cash-book/").status_code, [401, 403])
        self.user.is_superuser = True
        self.user.save()
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/ledger/cash-book/").status_code, 200)

    def test_invalid_input_does_not_post(self):
        for changes in [
            {"amount": "0"},
            {"amount": "-1"},
            {"amount": "0.001"},
            {"amount": "NaN"},
            {"kind": "OTHER"},
            {"description": " "},
            {"date": "2026-02-30"},
        ]:
            response = self.client.post(
                "/ledger/cash-book/", self.payload(**changes), format="json"
            )
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(CashBookEntry.objects.exists())
        self.assertFalse(CashBookJournal.objects.exists())

    def test_retry_idempotency(self):
        payload = self.payload()
        first = self.client.post("/ledger/cash-book/", payload, format="json")
        second = self.client.post("/ledger/cash-book/", payload, format="json")
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(CashBookJournal.objects.count(), 1)
        payload["amount"] = "999.00"
        self.assertEqual(
            self.client.post("/ledger/cash-book/", payload, format="json").status_code,
            400,
        )

    def test_edit_void_history_and_stale_write(self):
        entry = self.create()
        url = f"/ledger/cash-book/{entry['id']}/"
        response = self.client.patch(
            url,
            {"amount": "60.00", "version": 1, "reason": "Correct receipt"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(CashBookJournal.objects.count(), 3)
        original = CashBookJournal.objects.filter(reverses__isnull=True).earliest(
            "created_at"
        )
        self.assertEqual(original.amount, Decimal("100"))
        self.assertEqual(original.reversal.credit_account, "CASH")
        self.assertEqual(original.reversal.reason, "Correct receipt")
        self.assertEqual(
            self.client.patch(
                url, {"amount": "90.00", "version": 1, "reason": "Stale"}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.delete(
                url, {"version": 2, "reason": "Duplicate receipt"}, format="json"
            ).status_code,
            204,
        )
        self.assertEqual(CashBookJournal.objects.count(), 4)
        self.assertTrue(CashBookEntry.objects.get(pk=entry["id"]).voided_at)
        self.assertEqual(
            self.client.get("/ledger/cash-book/?year=2026").data["summary"][
                "closing_balance"
            ],
            "0.00",
        )
        self.assertEqual(
            self.client.delete(
                url, {"version": 3, "reason": "Again"}, format="json"
            ).status_code,
            400,
        )

    def test_reason_and_version_required(self):
        entry = self.create()
        url = f"/ledger/cash-book/{entry['id']}/"
        for data in [{"version": 1}, {"reason": "Fix"}, {"version": 1, "reason": " "}]:
            self.assertEqual(
                self.client.patch(url, data, format="json").status_code, 400
            )
            self.assertEqual(
                self.client.delete(url, data, format="json").status_code, 400
            )
        self.assertEqual(CashBookJournal.objects.count(), 1)

    def test_failure_rolls_back(self):
        with patch("ledger.services.post", side_effect=RuntimeError("Test failure")):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertFalse(CashBookEntry.objects.exists())

    def test_database_journal_immutable(self):
        self.create()
        for operation in [
            lambda: CashBookJournal.objects.update(amount=Decimal("2")),
            lambda: CashBookJournal.objects.all().delete(),
        ]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                operation()

    def test_range_carry_forward_pagination(self):
        self.create(date="2025-12-31", amount="150.00")
        self.create(date="2026-01-01", amount="20.00")
        self.create(date="2026-01-02", kind="EXPENSE", amount="45.00")
        self.create(date="2027-01-01", amount="99.00")
        response = self.client.get("/ledger/cash-book/?year=2026&page_size=1&page=2")
        self.assertEqual(response.data["summary"]["opening_balance"], "150.00")
        self.assertEqual(response.data["summary"]["closing_balance"], "125.00")
        self.assertEqual(response.data["results"][0]["running_balance"], "125.00")
        response = self.client.get("/ledger/cash-book/?year=2026&date_from=2026-01-02")
        self.assertEqual(response.data["summary"]["opening_balance"], "170.00")
        self.assertEqual(
            len(
                self.client.get("/ledger/cash-book-journal/?year=2026").data["results"]
            ),
            2,
        )

    def test_invalid_ranges(self):
        for query in [
            "year=no",
            "year=0",
            "year=2026&date_from=2027-01-01",
            "year=2026&date_from=2026-10-01&date_to=2026-01-01",
        ]:
            self.assertEqual(
                self.client.get(f"/ledger/cash-book/?{query}").status_code, 400
            )

    def test_journal_read_only(self):
        entry = self.create()
        journal = CashBookJournal.objects.get(entry_id=entry["id"])
        self.user.is_superuser = True
        self.user.save()
        self.assertEqual(
            self.client.patch(
                f"/ledger/cash-book-journal/{journal.pk}/", {}, format="json"
            ).status_code,
            405,
        )

    def test_failed_correction_rolls_back_reversal(self):
        entry = self.create()
        with patch("ledger.services.post", side_effect=RuntimeError("Test failure")):
            with self.assertRaises(RuntimeError):
                self.client.patch(
                    f"/ledger/cash-book/{entry['id']}/",
                    {"amount": "70.00", "version": 1, "reason": "Correction"},
                    format="json",
                )
        saved = CashBookEntry.objects.get(pk=entry["id"])
        self.assertEqual(saved.amount, Decimal("100"))
        self.assertEqual(saved.version, 1)
        self.assertEqual(CashBookJournal.objects.count(), 1)

    def test_date_kind_correction_keeps_journal_and_cash_balances_equal(self):
        entry = self.create(date="2025-12-31")
        response = self.client.patch(
            f"/ledger/cash-book/{entry['id']}/",
            {
                "date": "2026-01-01",
                "kind": "EXPENSE",
                "amount": "25.00",
                "version": 1,
                "reason": "Correct date and type",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get("/ledger/cash-book/?year=2025").data["summary"][
                "closing_balance"
            ],
            "0.00",
        )
        self.assertEqual(
            self.client.get("/ledger/cash-book/?year=2026").data["summary"][
                "closing_balance"
            ],
            "-25.00",
        )
        journals = CashBookJournal.objects.all()
        balance = sum(
            row.amount if row.debit_account == "CASH" else -row.amount
            for row in journals
        )
        self.assertEqual(balance, Decimal("-25"))
