"""Procurement compatibility, cancellation, official approval and private exports."""

from datetime import date
import uuid
from decimal import Decimal as D
from unittest.mock import patch, Mock
from django.test import TestCase
from django.db import transaction, IntegrityError
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from accounts.models import Department, StaffProfile
from crm.models import Party, PartyRole
from crm.storage import StoredDocument, DocumentStorageError
from procurement import models as pm, services as ps, document_registry as registry
from ledger import models as m, documents
from ledger.tests import LedgerTests
from ledger.services import records
from ledger.services import business_posting, settlement, setup
from ledger.integrations import procurement as bridge, workflow
from ledger.selectors.statements import generate


class LedgerIntegrationTests(TestCase):
    setUpTestData = classmethod(LedgerTests.setUpTestData.__func__)
    create = LedgerTests.create
    post = LedgerTests.post
    invoice = LedgerTests.invoice
    receipt = LedgerTests.receipt
    setUp = LedgerTests.setUp

    def test_revision_requires_fresh_approval_and_preserves_original(self):
        original = self.create(
            "INVOICE",
            lines=[dict(description="Switch", quantity=D(1), unit_price=D(100))],
        )
        records.submit(self.user, original, 1)
        records.decide(self.user, original, 1, True, "Initial approval")
        response = self.api.post(
            f"/ledger/invoices/{original.pk}/revise/",
            dict(
                expected_version=1,
                reason="Correct agreed price",
                party=str(self.party.pk),
                currency="NGN",
                policy=str(self.policy.pk),
                date="2026-09-01",
                lines=[dict(description="Switch", quantity="1", unit_price="120")],
            ),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(response.status_code, 200, response.data)
        revised = m.FinancialRecord.objects.get(pk=response.data["id"])
        original.refresh_from_db()
        self.assertEqual(original.status, "SUPERSEDED")
        self.assertEqual(original.amount, D(100))
        self.assertEqual(revised.amount, D(120))
        self.assertEqual(revised.supersedes_id, original.pk)
        self.assertTrue(original.revisions.get().approval.decision.approved)
        with self.assertRaises(ValidationError):
            business_posting.post(self.user, revised, revised.version)
        self.post(revised)
        self.assertFalse(original.journals.exists())

    def test_allocation_api_validates_and_replays_without_receiving_twice(self):
        invoice = self.invoice(value="100")
        receipt = self.receipt("100", reference="BANK-VERIFIED")
        payload = dict(
            source=str(receipt.settlement_source.pk),
            invoice=str(invoice.invoice.pk),
            amount="100",
            date="2026-09-01",
        )
        key = str(uuid.uuid4())
        response = self.api.post(
            "/ledger/allocations/", payload, format="json", HTTP_IDEMPOTENCY_KEY=key
        )
        self.assertEqual(response.status_code, 201, response.data)
        replay = self.api.post(
            "/ledger/allocations/", payload, format="json", HTTP_IDEMPOTENCY_KEY=key
        )
        self.assertEqual(response.data, replay.data)
        self.assertEqual(settlement.invoice_due(invoice.invoice), 0)
        self.assertEqual(m.AllocationEvent.objects.count(), 1)
        self.assertEqual(receipt.receipt.payment_reference, "BANK-VERIFIED")
        bad = self.api.post(
            "/ledger/allocations/",
            {**payload, "source": "invalid"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(bad.status_code, 400)

    def test_final_partial_credit_uses_exact_remaining_minor_unit(self):
        invoice = self.post(
            self.create(
                "INVOICE",
                lines=[
                    dict(
                        description="Three units", quantity=D(3), unit_price=D("0.3333")
                    )
                ],
            )
        )
        line = invoice.invoice.lines.get()
        amounts = []
        for _ in range(3):
            credit = self.post(
                self.create(
                    "CREDIT",
                    invoice=invoice.invoice,
                    credit_lines=[dict(line=line, quantity=D(1))],
                )
            )
            amounts.append(credit.amount)
        self.assertEqual(amounts, [D("0.33"), D("0.33"), D("0.34")])
        self.assertEqual(settlement.invoice_due(invoice.invoice), 0)

    def test_currency_can_be_deactivated_without_rewriting_identity(self):
        setup.update_setup(
            self.user, self.usd, dict(active=False, reason="Temporarily unavailable")
        )
        self.usd.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.create(
                "INVOICE",
                currency=self.usd,
                lines=[dict(description="Switch", quantity=1, unit_price=10)],
            )
        with self.assertRaises(ValidationError):
            setup.update_setup(
                self.user,
                self.currency,
                dict(code="ABC", reason="Cannot relabel money"),
            )

    def test_tax_policy_setup_api_accepts_open_ended_policy(self):
        response = self.api.post(
            "/ledger/tax-policies/",
            dict(
                tax_type=str(self.tax_type.pk),
                effective_from="2026-09-01",
                rate="5",
                liability_account=str(self.accounts["TAX"].pk),
                treatment="CLIENT_REMITTANCE",
                verified_reference="Isolated test policy",
            ),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_nonobject_payload_is_validation_error(self):
        response = self.api.post(
            "/ledger/allocations/",
            [dict(amount="1")],
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(response.status_code, 400)

    def test_submitted_record_cannot_be_reopened_through_sql(self):
        original = self.create(
            "INVOICE",
            lines=[dict(description="Switch", quantity=D(1), unit_price=D(100))],
        )
        records.submit(self.user, original, 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            m.FinancialRecord.objects.filter(pk=original.pk).update(status="DRAFT")

    def request_and_invoice(self):
        Department.objects.get_or_create(name="Procurement", defaults={"code": "PROC"})
        request = ps.create_request(
            self.user,
            dict(
                client=self.party,
                contact=self.party,
                items=[dict(description="Switch", quantity=D(10), unit="unit")],
            ),
        )
        ps.submit_request(self.user, request)
        item = request.items.get()
        invoice = self.post(
            self.create(
                "INVOICE",
                request=request,
                lines=[
                    dict(
                        item=item,
                        description="Switch",
                        unit="unit",
                        quantity=D(10),
                        unit_price=D(100),
                    )
                ],
            )
        )
        return request, item, invoice

    def doc_for(self, request, category):
        return ps.make(
            pm.ProcurementDocument,
            self.user,
            request=request,
            category=category,
            filename="evidence.pdf",
            mime_type="application/pdf",
            size_bytes=1,
            checksum="f" * 64,
            storage_provider="supabase",
            external_file_id="private",
        )

    def test_direct_invoice_order_and_acceptance_gate_revenue_and_cancellation(self):
        request, item, invoice = self.request_and_invoice()
        line = invoice.invoice.lines.get()
        po = ps.create_po(
            self.user,
            dict(
                request=request,
                reference="DIRECT",
                verbal=True,
                evidence="Client approved these 10 switches",
                lines=[dict(invoice_line=line.pk, quantity=D(10), unit_price=D(100))],
            ),
        )
        self.assertIsNone(po.lines.get().quote_line_id)
        self.assertEqual(po.lines.get().item_id, item.pk)
        self.assertFalse(pm.ClientQuotation.objects.exists())
        supplier = Party.objects.create(display_name="Supplier")
        PartyRole.objects.create(party=supplier, role="SUPPLIER")
        offer = ps.create_offer(
            self.user,
            dict(
                request=request,
                supplier=supplier,
                currency="NGN",
                lines=[dict(item=item, quantity=D(10), unit_price=D(80))],
            ),
        ).lines.get()
        purchase = ps.create_purchase(
            self.user,
            dict(
                request=request,
                supplier=supplier,
                currency="NGN",
                lines=[
                    dict(
                        attempt=po.lines.get().attempts.get(),
                        supplier_offer=offer,
                        quantity=D(10),
                        unit_price=D(80),
                    )
                ],
            ),
        )
        receipt = ps.create_receipt(
            self.user,
            dict(
                purchase=purchase,
                lines=[dict(purchase_line=purchase.lines.get(), quantity=D(10))],
            ),
        )
        delivery = ps.create_delivery(
            self.user,
            dict(
                request=request,
                destination="Client site",
                recipient=self.party,
                waybill=self.doc_for(request, "WAYBILL"),
                lines=[dict(receipt_line=receipt.lines.get(), quantity=D(10))],
            ),
        )
        ps.dispatch(self.user, delivery)
        today = timezone.localdate()
        with self.assertRaises(ValidationError):
            bridge.recognize(self.user, line, D(1), today)
        ps.record_outcome(
            self.user,
            delivery,
            dict(
                receiver=self.party,
                signed_waybill=self.doc_for(request, "SIGNED_WAYBILL"),
                lines=[
                    dict(
                        delivery_line=delivery.lines.get(),
                        accepted=D(4),
                        rejected=D(6),
                        reason="Wrong switch specification",
                    )
                ],
            ),
        )
        recognition = bridge.recognize(self.user, line, D(4), today)
        self.assertEqual(recognition.amount, D(400))
        with self.assertRaises(ValidationError):
            bridge.recognize(self.user, line, D(1), today)
        credit = self.create(
            "CREDIT",
            invoice=invoice.invoice,
            date=today,
            description="Client rejected six switches",
            credit_lines=[dict(line=line, quantity=D(6))],
        )
        self.post(credit)
        forbidden = self.create(
            "CREDIT",
            invoice=invoice.invoice,
            date=today,
            description="Cannot cancel accepted goods",
            credit_lines=[dict(line=line, quantity=D(1))],
        )
        with self.assertRaises(ValidationError):
            self.post(forbidden)

    def test_quote_approval_is_real_and_revision_bound(self):
        request, item, invoice = self.request_and_invoice()
        supplier = Party.objects.create(display_name="Supplier")
        PartyRole.objects.create(party=supplier, role="SUPPLIER")
        offer = ps.create_offer(
            self.user,
            dict(
                request=request,
                supplier=supplier,
                currency="NGN",
                lines=[dict(item=item, quantity=D(10), unit_price=D(80))],
            ),
        ).lines.get()
        revision = ps.create_quote(
            self.user,
            dict(
                request=request,
                currency="NGN",
                lines=[
                    dict(
                        item=item,
                        supplier_offer=offer,
                        description="Switch",
                        quantity=D(10),
                        unit_price=D(100),
                    )
                ],
            ),
        )
        version = registry.prepare(
            self.user, dict(request=request, kind="QT", quotation_revision=revision)
        )
        registry.request_approval(self.user, version)
        with self.assertRaises(registry.IntegrationUnavailable):
            registry.require_ledger_approval(revision)
        approval = m.ApprovalRequest.objects.get(quotation_revision=revision)
        bridge.decide_quotation(self.user, approval, True, "Checked exact revision")
        registry.require_ledger_approval(revision)
        self.assertEqual(registry.approval_status(version), "APPROVED")
        self.assertEqual(invoice.journals.count(), 1)

    def test_financial_registry_not_exposed_through_procurement(self):
        request, item, invoice = self.request_and_invoice()
        register = invoice.registered_version.register
        self.assertEqual(
            self.api.get(f"/procurement/document-register/{register.pk}/").status_code,
            404,
        )
        self.assertEqual(
            self.api.get(f"/ledger/invoices/{invoice.pk}/").status_code, 200
        )

    def test_detail_sql_mutation_rejected_after_post(self):
        invoice = self.invoice()
        with self.assertRaises(IntegrityError), transaction.atomic():
            m.InvoiceLine.objects.filter(invoice=invoice.invoice).update(total=D(1))

    def test_statement_export_private_and_idempotent_without_live_provider(self):
        self.invoice()
        report = generate(
            self.user, self.party, self.currency, date(2026, 9, 1), date(2026, 9, 1)
        )
        provider = Mock()
        provider.upload.return_value = StoredDocument(
            provider="supabase",
            external_file_id="private",
            storage_path="private/report.pdf",
        )
        with patch("ledger.documents.get_document_storage", return_value=provider):
            first = documents.generate(self.user, statement=report)
            second = documents.generate(self.user, statement=report)
        self.assertEqual([d.pk for d in first], [d.pk for d in second])
        self.assertEqual(provider.upload.call_count, 1)
        self.assertTrue(first[0].filename.startswith("Reconciliation-and-Statement"))
        with patch(
            "ledger.documents.get_document_storage",
            side_effect=DocumentStorageError("private token"),
        ):
            with self.assertRaises(documents.StorageUnavailable) as raised:
                documents.generate(self.user, statement=report, format="JPG")
            self.assertNotIn("private token", str(raised.exception))

    def test_manager_authorized_system_tasks_complete_with_approval(self):
        StaffProfile.objects.create(
            user=self.user,
            department=self.department,
            job_title="Accounts",
            start_date=date(2020, 1, 1),
        )
        policy = workflow.authorize_routing(
            self.user,
            True,
            ["DASHBOARD"],
            "Standing authorization for Accounts reviews",
        )
        invoice = self.create(
            "INVOICE",
            lines=[dict(description="Switch", quantity=D(1), unit_price=D(100))],
        )
        records.submit(self.user, invoice, invoice.version)
        approval = m.ApprovalRequest.objects.get(revision__record=invoice)
        intent = m.IntegrationIntent.objects.get(approval=approval, event="SUBMITTED")
        workflow.process_one(intent.pk)
        approval.refresh_from_db()
        intent.refresh_from_db()
        self.assertIsNotNone(approval.task_batch_id, intent.failure)
        self.assertEqual(approval.task_batch.created_by_name, "System")
        self.assertIsNone(approval.task_batch.created_by_id)
        self.assertEqual(approval.task_batch.tasks.count(), 1)
        workflow.process_one(intent.pk)
        self.assertEqual(approval.task_batch.tasks.count(), 1)
        records.decide(self.user, invoice, invoice.version, True, "Reviewed")
        done = m.IntegrationIntent.objects.get(approval=approval, event="APPROVED")
        workflow.process_one(done.pk)
        self.assertEqual(approval.task_batch.tasks.get().status, "COMPLETED")
        self.assertEqual(policy.created_by_id, self.user.pk)
