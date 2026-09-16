"""Accounting, settlement, access and historical-report regressions on local PostgreSQL."""

import uuid
from datetime import date
from decimal import Decimal as D
from django.test import TestCase
from django.db import connection, transaction, IntegrityError
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError, PermissionDenied
from accounts.models import Department
from crm.models import Party, PartyRole
from ledger import models as m
from ledger.services import records, setup, business_posting as bp, settlement, tax
from ledger.services.common import make, Conflict
from ledger.selectors.statements import generate
from ledger.integrations.procurement import set_client_code


class LedgerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="ledger-test", email="ledger@example.invalid", password="test-only"
        )
        cls.department, _ = Department.objects.get_or_create(
            name="Accounts", defaults={"code": "ACCT"}
        )
        cls.party = Party.objects.create(
            display_name="Ledger client", entity_kind="INDIVIDUAL"
        )
        PartyRole.objects.create(party=cls.party, role="CLIENT")
        cls.other = Party.objects.create(
            display_name="Other client", entity_kind="INDIVIDUAL"
        )
        PartyRole.objects.create(party=cls.other, role="CLIENT")
        cls.currency = m.Currency.objects.get(code="NGN")
        cls.usd = m.Currency.objects.get(code="USD")
        cls.accounts = {}
        for code, kind in [
            ("AR", "ASSET"),
            ("AP", "LIABILITY"),
            ("DEFERRED", "LIABILITY"),
            ("SALES", "INCOME"),
            ("ADVANCE", "ASSET"),
            ("EQUITY", "EQUITY"),
            ("BANK", "ASSET"),
            ("DELIVERY", "EXPENSE"),
            ("TAX", "LIABILITY"),
        ]:
            cls.accounts[code] = make(
                m.LedgerAccount, cls.user, code=code, name=code, classification=kind
            )
        cls.policy = setup.create_setup(
            cls.user,
            m.PostingPolicyVersion,
            dict(
                name="Test policy",
                receivable=cls.accounts["AR"],
                payable=cls.accounts["AP"],
                deferred_revenue=cls.accounts["DEFERRED"],
                revenue=cls.accounts["SALES"],
                advance=cls.accounts["ADVANCE"],
                opening_equity=cls.accounts["EQUITY"],
            ),
        )
        cls.bank = make(
            m.CashAccount,
            cls.user,
            name="NGN bank",
            kind="BANK",
            account=make(
                m.AccountCurrency,
                cls.user,
                account=cls.accounts["BANK"],
                currency=cls.currency,
            ),
        )
        setup.create_year(cls.user, 2026)
        set_client_code(cls.user, cls.party, "LGR")
        set_client_code(cls.user, cls.other, "OTH")
        cls.doc = make(
            m.LedgerDocument,
            cls.user,
            party=cls.party,
            filename="proof.pdf",
            mime_type="application/pdf",
            checksum="0" * 64,
            size_bytes=10,
            storage_provider="supabase",
            external_file_id="private-test",
        )
        cls.tax_type = make(
            m.TaxType,
            cls.user,
            code="TEST",
            name="Test invoice tax",
            authority="Test authority",
        )
        cls.tax_policy = make(
            m.TaxPolicyVersion,
            cls.user,
            tax_type=cls.tax_type,
            effective_from=date(2026, 1, 1),
            rate=D(5),
            liability_account=cls.accounts["TAX"],
            treatment="CLIENT_REMITTANCE",
            verified_reference="Isolated illustrative policy; not statutory",
        )

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def create(self, kind, **kwargs):
        data = dict(
            party=self.party,
            currency=self.currency,
            policy=self.policy,
            date=date(2026, 9, 1),
            description="Test record",
            evidence=self.doc,
        )
        data.update(kwargs)
        return records.create(self.user, kind, data)

    def post(self, record):
        record = records.submit(self.user, record, record.version)
        record = records.decide(
            self.user, record, record.version, True, "Checked supporting evidence"
        )
        return bp.post(self.user, record, record.version)

    def invoice(self, tax_policy=None, value="100000", **kwargs):
        line = dict(
            description="Smart switch", unit="unit", quantity=D(1), unit_price=D(value)
        )
        if tax_policy:
            line["tax_policy"] = tax_policy
        return self.post(self.create("INVOICE", lines=[line], **kwargs))

    def receipt(self, value="98000", **kwargs):
        return self.post(
            self.create(
                "RECEIPT",
                amount=D(value),
                cash_account=self.bank,
                purpose="CLIENT",
                **kwargs,
            )
        )

    def test_currency_year_and_no_legacy_models(self):
        self.assertEqual(
            set(m.Currency.objects.values_list("code", flat=True)),
            {"NGN", "USD", "GBP"},
        )
        self.assertEqual(m.AccountingPeriod.objects.count(), 12)
        self.assertFalse(hasattr(m, "Expectation"))

    def test_invoice_double_entry_and_approval_audit(self):
        invoice = self.invoice()
        journal = invoice.journals.get()
        self.assertEqual(sum(x.debit for x in journal.lines.all()), D("100000"))
        self.assertEqual(sum(x.credit for x in journal.lines.all()), D("100000"))
        self.assertEqual(
            journal.lines.get(account__account=self.accounts["DEFERRED"]).credit,
            D("100000"),
        )
        self.assertFalse(
            journal.lines.filter(account__account=self.accounts["SALES"]).exists()
        )
        decision = invoice.revisions.get().approval.decision
        self.assertEqual(decision.created_by_id, self.user.pk)
        self.assertTrue(decision.reason)
        with self.assertRaises(ValidationError):
            bp.post(self.user, invoice, invoice.version)

    def test_approved_content_and_posted_lines_are_immutable(self):
        invoice = self.invoice()
        with self.assertRaises(IntegrityError), transaction.atomic():
            m.FinancialRecord.objects.filter(pk=invoice.pk).update(amount=D("1"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            m.JournalLine.objects.filter(entry__source=invoice).update(debit=D("2"))

    def test_database_rejects_unbalanced_journal_even_without_service(self):
        period = m.AccountingPeriod.objects.get(start=date(2026, 9, 1))
        from django.utils import timezone

        with self.assertRaises(IntegrityError), transaction.atomic():
            entry = make(
                m.JournalEntry,
                self.user,
                date=date(2026, 9, 1),
                period=period,
                currency=self.currency,
                event="INVALID",
                description="invalid",
            )
            account = make(
                m.AccountCurrency,
                self.user,
                account=self.accounts["AR"],
                currency=self.currency,
            )
            make(
                m.JournalLine,
                self.user,
                entry=entry,
                account=account,
                debit=D("10"),
                credit=0,
            )
            entry.posted_at = timezone.now()
            entry.save(update_fields=["posted_at"])
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    def test_receipt_partial_bulk_and_release_preserve_cash(self):
        first = self.invoice(value="60000")
        second = self.invoice(value="40000")
        receipt = self.receipt("100000")
        source = receipt.settlement_source
        a = settlement.allocate(
            self.user, source, first.invoice, D("60000"), date(2026, 9, 1)
        )
        settlement.allocate(
            self.user, source, second.invoice, D("40000"), date(2026, 9, 1)
        )
        self.assertEqual(settlement.invoice_due(first.invoice), 0)
        with self.assertRaises(ValidationError):
            settlement.allocate(
                self.user, source, second.invoice, D(1), date(2026, 9, 1)
            )
        settlement.release(self.user, a, D("10000"), date(2026, 9, 2), "Reallocate")
        self.assertEqual(settlement.source_available(source), D("10000"))
        self.assertEqual(m.ClientMovement.objects.filter(kind="RECEIPT").count(), 1)

    def test_statement_opening_period_and_future_allocation_history(self):
        prior = self.invoice(value="20000", date=date(2026, 8, 31))
        current = self.invoice(value="100000")
        receipt = self.receipt("98000")
        application = settlement.allocate(
            self.user,
            receipt.settlement_source,
            current.invoice,
            D("98000"),
            date(2026, 9, 1),
        )
        before = generate(
            self.user, self.party, self.currency, date(2026, 9, 1), date(2026, 9, 1)
        )
        self.assertEqual(D(before.data["opening_balance"]), D("20000"))
        self.assertEqual(D(before.data["closing_balance"]), D("22000"))
        self.receipt("30000", date=date(2026, 9, 2))
        settlement.release(
            self.user, application, D("98000"), date(2026, 9, 2), "Move to older debt"
        )
        after = generate(
            self.user, self.party, self.currency, date(2026, 9, 1), date(2026, 9, 1)
        )
        self.assertEqual(before.data["closing_balance"], after.data["closing_balance"])
        self.assertEqual(before.data["outstanding"], after.data["outstanding"])
        self.assertTrue(prior)

    def test_withholding_settles_debt_but_not_cash_and_remittance_only_collected(self):
        # Net 100000 + test tax 5000; cash 103000 plus documented withholding 2000.
        invoice = self.invoice(tax_policy=self.tax_policy)
        line = invoice.invoice.lines.get()
        auth = tax.authorize(
            self.user,
            dict(
                party=self.party,
                tax_type=self.tax_type,
                effective_from=date(2026, 1, 1),
                reason="Approved eligibility",
                evidence=self.doc,
            ),
        )
        withholding = self.post(
            self.create(
                "WITHHOLDING",
                line=line,
                authorization=auth,
                certificate_reference="WHT-TEST",
                amount=D("2000"),
            )
        )
        receipt = self.receipt("103000")
        settlement.allocate(
            self.user,
            receipt.settlement_source,
            invoice.invoice,
            D("103000"),
            date(2026, 9, 1),
            [dict(line=line, amount=D("3000"))],
        )
        self.assertEqual(settlement.invoice_due(invoice.invoice), 0)
        statement = generate(
            self.user, self.party, self.currency, date(2026, 9, 1), date(2026, 9, 1)
        )
        self.assertEqual(D(statement.data["closing_balance"]), 0)
        self.assertEqual(D(statement.data["cash_received_total"]), D("103000"))
        self.assertEqual(D(statement.data["withheld_total"]), D("2000"))
        self.assertEqual(tax.remittable(self.tax_policy, self.currency), D("3000"))
        self.assertFalse(
            withholding.journals.get().lines.filter(account=self.bank.account).exists()
        )

    def test_unauthorized_and_excess_withholding_rejected(self):
        invoice = self.invoice(tax_policy=self.tax_policy)
        line = invoice.invoice.lines.get()
        auth = tax.authorize(
            self.user,
            dict(
                party=self.party,
                tax_type=self.tax_type,
                effective_from=date(2026, 9, 2),
                reason="Later eligibility",
                evidence=self.doc,
            ),
        )
        record = self.create(
            "WITHHOLDING",
            line=line,
            authorization=auth,
            certificate_reference="TEST",
            amount=D("2000"),
        )
        with self.assertRaises(ValidationError):
            self.post(record)
        self.assertEqual(m.ClientMovement.objects.filter(kind="WITHHOLDING").count(), 0)

    def test_currency_mismatch_and_cross_client_allocation(self):
        invoice = self.invoice()
        with self.assertRaises(ValidationError):
            self.create(
                "RECEIPT",
                currency=self.usd,
                amount=D("10"),
                cash_account=self.bank,
                purpose="CLIENT",
            )
        other_doc = make(
            m.LedgerDocument,
            self.user,
            party=self.other,
            filename="other.pdf",
            mime_type="application/pdf",
            checksum="1" * 64,
            size_bytes=1,
            storage_provider="supabase",
            external_file_id="test",
        )
        receipt = self.receipt("10", party=self.other, evidence=other_doc)
        with self.assertRaises(ValidationError):
            settlement.allocate(
                self.user,
                receipt.settlement_source,
                invoice.invoice,
                D(10),
                date(2026, 9, 1),
            )

    def test_closed_period_and_stale_version(self):
        record = self.create(
            "INVOICE",
            lines=[
                dict(description="Switch", unit="each", quantity=D(1), unit_price=D(1))
            ],
        )
        with self.assertRaises(Conflict):
            records.submit(self.user, record, 42)
        period = m.AccountingPeriod.objects.get(start=date(2026, 9, 1))
        setup.period_action(self.user, period, 0, True, "Month closed")
        with self.assertRaises(Conflict):
            self.post(record)

    def test_reverse_receipt_keeps_historical_statement_and_evidence(self):
        from ledger.services.reversals import reverse

        invoice = self.invoice()
        receipt = self.receipt("100000")
        settlement.allocate(
            self.user,
            receipt.settlement_source,
            invoice.invoice,
            D("100000"),
            date(2026, 9, 1),
        )
        reverse(
            self.user, receipt, date(2026, 9, 2), "Bank confirmed receipt was reversed"
        )
        statement = generate(
            self.user, self.party, self.currency, date(2026, 9, 1), date(2026, 9, 1)
        )
        self.assertEqual(D(statement.data["closing_balance"]), 0)
        current = generate(
            self.user, self.party, self.currency, date(2026, 9, 1), date(2026, 9, 2)
        )
        self.assertEqual(D(current.data["closing_balance"]), D("100000"))
        self.assertEqual(m.Receipt.objects.count(), 1)

    def test_advance_clearance_counts_cost_once(self):
        advance = self.post(
            self.create("ADVANCE", amount=D("50000"), custodian=self.user)
        )
        payment = self.post(
            self.create(
                "PAYMENT",
                amount=D("50000"),
                cash_account=self.bank,
                purpose="ADVANCE",
                target=advance,
            )
        )
        self.post(
            self.create(
                "CLEARANCE",
                advance=advance,
                cost_lines=[
                    dict(
                        account=self.accounts["DELIVERY"],
                        amount=D("42000"),
                        description="Actual logistics",
                        evidence=self.doc,
                    )
                ],
            )
        )
        self.post(
            self.create(
                "RECEIPT",
                amount=D("8000"),
                cash_account=self.bank,
                purpose="ADVANCE_RETURN",
                target=advance,
            )
        )
        self.assertEqual(bp.advance_outstanding(advance), 0)
        expense = m.JournalLine.objects.filter(
            account__account=self.accounts["DELIVERY"]
        )
        self.assertEqual(sum(x.debit for x in expense), D("42000"))
        self.assertFalse(m.ClientMovement.objects.exists())
        self.assertEqual(payment.journals.count(), 1)

    def test_api_idempotency_and_permissions(self):
        payload = dict(
            party=str(self.party.pk),
            currency="NGN",
            policy=str(self.policy.pk),
            date="2026-09-01",
            lines=[
                dict(description="Switch", unit="each", quantity="1", unit_price="100")
            ],
        )
        key = str(uuid.uuid4())
        first = self.api.post(
            "/ledger/invoices/", payload, format="json", HTTP_IDEMPOTENCY_KEY=key
        )
        self.assertEqual(first.status_code, 201, first.data)
        second = self.api.post(
            "/ledger/invoices/", payload, format="json", HTTP_IDEMPOTENCY_KEY=key
        )
        self.assertEqual(first.data, second.data)
        payload["description"] = "Different"
        self.assertEqual(
            self.api.post(
                "/ledger/invoices/", payload, format="json", HTTP_IDEMPOTENCY_KEY=key
            ).status_code,
            409,
        )
        staff = get_user_model().objects.create_user(
            username="restricted", password="test", is_staff=True
        )
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="ledger", codename="view_financialrecord"
            ),
            Permission.objects.get(
                content_type__app_label="ledger", codename="view_invoice"
            ),
        )
        self.api.force_authenticate(staff)
        self.assertEqual(self.api.get("/ledger/invoices/").data["count"], 0)
        self.assertEqual(
            self.api.post(
                "/ledger/invoices/",
                payload,
                format="json",
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            ).status_code,
            403,
        )
        with self.assertRaises(PermissionDenied):
            generate(
                staff, self.party, self.currency, date(2026, 9, 1), date(2026, 9, 1)
            )

    def test_manual_cannot_bypass_invoice_control_account(self):
        ar = make(
            m.AccountCurrency,
            self.user,
            account=self.accounts["AR"],
            currency=self.currency,
        )
        record = self.create(
            "MANUAL",
            journal_lines=[
                dict(account=self.bank.account, debit=D(10), credit=0),
                dict(account=ar, debit=0, credit=D(10)),
            ],
        )
        with self.assertRaises(ValidationError):
            self.post(record)
