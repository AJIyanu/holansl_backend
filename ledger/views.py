"""Explicit staff-only ledger APIs; business state changes only through services."""

from decimal import Decimal
from collections.abc import Mapping
from django.db import IntegrityError
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import FileResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, NotFound
from rest_framework.response import Response
from rest_framework.permissions import BasePermission
from drf_spectacular.utils import extend_schema, OpenApiTypes
from holansladminservices.pagination import StandardPagination
from ledger import models as m, serializers as z, documents
from ledger.services import records, setup, business_posting, settlement, tax
from ledger.services.common import (
    require,
    full_scope,
    get_record,
    visible_records,
    primitive,
    operation,
    check,
    Conflict,
)
from ledger.integrations import procurement
from ledger.selectors.statements import generate as generate_statement


def plain(obj):
    hidden = {"external_file_id", "storage_path"}
    return primitive(
        {
            field.attname: getattr(obj, field.attname)
            for field in obj._meta.concrete_fields
            if field.attname not in hidden
        }
    )


def record_data(obj):
    data = plain(obj)
    data["currency"] = obj.currency.code
    data["content"] = records.snapshot(obj)
    data["events"] = [plain(e) for e in obj.events.all()]
    if obj.kind == "INVOICE":
        data["outstanding"] = str(settlement.invoice_due(obj.invoice))
        data["invoice_id"] = str(obj.invoice.pk)
    if hasattr(obj, "settlement_source"):
        data["settlement_source"] = str(obj.settlement_source.pk)
        data["unallocated"] = str(settlement.source_available(obj.settlement_source))
    if obj.kind == "ADVANCE":
        data["outstanding_advance"] = str(business_posting.advance_outstanding(obj))
    return data


class StaffPermission(BasePermission):
    def has_permission(self, request, view):
        require(request.user, "view_financialrecord")
        return True


class BaseView(viewsets.ViewSet):
    permission_classes = [StaffPermission]
    serializer_class = z.StrictSerializer
    pagination_class = StandardPagination
    lookup_value_regex = (
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method in {"POST", "PATCH", "PUT"}:
            check(isinstance(request.data, Mapping), "Expected an object payload.")

    def handle_exception(self, exc):
        if isinstance(exc, DjangoValidationError):
            exc = ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            )
        return super().handle_exception(exc)

    def paginate(self, qs, serialize=plain):
        pager = StandardPagination()
        page = pager.paginate_queryset(qs, self.request, view=self)
        return pager.get_paginated_response([serialize(obj) for obj in page])

    def mutate(self, serializer, callback, context=None):
        parsed = serializer(data=self.request.data, context=context or {})
        parsed.is_valid(raise_exception=True)
        data = parsed.validated_data
        payload = {
            k: (
                {
                    "filename": v.name,
                    "checksum": __import__(
                        "crm.document_services",
                        fromlist=["calculate_document_checksum"],
                    ).calculate_document_checksum(v),
                }
                if hasattr(v, "chunks")
                else v
            )
            for k, v in data.items()
        }
        try:
            body = operation(
                self.request.user,
                self.request.headers.get("Idempotency-Key"),
                self.request.method,
                self.request.path,
                payload,
                lambda: callback(data),
            )
        except DjangoValidationError as exc:
            raise ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            ) from None
        except IntegrityError:
            raise Conflict(
                "The operation conflicts with financial integrity constraints."
            ) from None
        return Response(body, status=201 if self.action == "create" else 200)


class FinancialView(BaseView):
    kind = None

    def obj(self, pk, act="view"):
        obj = get_record(self.request.user, pk, action=act)
        if obj.kind != self.kind:
            raise NotFound()
        return obj

    @extend_schema(responses=z.FinancialSummaryOutput(many=True))
    def list(self, request):
        require(request.user, "view_" + m.RESOURCE[self.kind])
        qs = (
            visible_records(request.user)
            .filter(kind=self.kind)
            .select_related("currency", "policy", "party")
        )
        for param, field in [
            ("client", "party_id"),
            ("status", "status"),
            ("request", "request_id"),
        ]:
            if request.query_params.get(param):
                qs = qs.filter(**{field: request.query_params[param]})
        qs = report_filter(
            qs, request, date_field="date", currency_field="currency__code"
        )
        if request.query_params.get("cash_account") and self.kind in {
            "RECEIPT",
            "PAYMENT",
        }:
            account = z.serializers.UUIDField().run_validation(
                request.query_params["cash_account"]
            )
            qs = qs.filter(**{self.kind.lower() + "__cash_account_id": account})
        return self.paginate(
            qs, lambda obj: {**plain(obj), "currency": obj.currency.code}
        )

    def retrieve(self, request, pk=None):
        return Response(record_data(self.obj(pk)))

    @extend_schema(request=z.FinancialInput, responses=OpenApiTypes.OBJECT)
    def create(self, request):
        require(request.user, "add_" + m.RESOURCE[self.kind])

        def callback(data):
            obj = records.create(request.user, self.kind, data)
            return record_data(obj), [obj]

        return self.mutate(z.FinancialInput, callback, {"kind": self.kind})

    @extend_schema(request=z.EditInput, responses=OpenApiTypes.OBJECT)
    def partial_update(self, request, pk=None):
        obj = self.obj(pk, "change")

        def callback(data):
            updated = records.edit(
                request.user, obj, data.pop("expected_version"), data
            )
            return record_data(updated), [updated]

        return self.mutate(z.EditInput, callback)

    @extend_schema(request=z.ReviseInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def revise(self, request, pk=None):
        obj = self.obj(pk, "change")
        require(request.user, "add_" + m.RESOURCE[self.kind])

        def callback(data):
            updated = records.revise(
                request.user,
                obj,
                data.pop("expected_version"),
                data.pop("reason"),
                data,
            )
            return record_data(updated), [obj, updated]

        return self.mutate(z.ReviseInput, callback, {"kind": self.kind})

    @extend_schema(request=z.VersionInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        obj = self.obj(pk, "change")
        return self.mutate(
            z.VersionInput,
            lambda d: self.result(
                records.submit(request.user, obj, d["expected_version"])
            ),
        )

    def result(self, obj):
        return record_data(obj), [obj]

    @extend_schema(request=z.DecisionInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        obj = self.obj(pk, "approve")
        return self.mutate(
            z.DecisionInput,
            lambda d: self.result(
                records.decide(
                    request.user, obj, d["expected_version"], True, d["reason"]
                )
            ),
        )

    @extend_schema(request=z.DecisionInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        obj = self.obj(pk, "approve")
        return self.mutate(
            z.DecisionInput,
            lambda d: self.result(
                records.decide(
                    request.user, obj, d["expected_version"], False, d["reason"]
                )
            ),
        )

    @extend_schema(request=z.VersionInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="post")
    def post_record(self, request, pk=None):
        obj = self.obj(pk, "post")
        return self.mutate(
            z.VersionInput,
            lambda d: self.result(
                business_posting.post(request.user, obj, d["expected_version"])
            ),
        )

    @extend_schema(request=z.FormatInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        obj = self.obj(pk)
        require(request.user, "generate_financialrecord")
        return self.mutate(
            z.FormatInput,
            lambda d: (
                {
                    "documents": [
                        plain(doc)
                        for doc in documents.generate(
                            request.user, record=obj, format=d["format"]
                        )
                    ]
                },
                [obj],
            ),
        )

    @extend_schema(request=z.DateReasonInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        obj = self.obj(pk)
        require(
            request.user,
            "reverse_journalentry"
            if obj.kind == "MANUAL"
            else "reverse_financialrecord",
        )
        from ledger.services.reversals import reverse

        return self.mutate(
            z.DateReasonInput,
            lambda d: self.result(reverse(request.user, obj, d["date"], d["reason"])),
        )

    @extend_schema(request=z.CancelInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        original = self.obj(pk)
        check(
            original.kind == "INVOICE" and original.status == "POSTED",
            "Select a posted invoice.",
        )
        require(request.user, "cancel_invoice")
        require(request.user, "add_creditnote")

        def callback(data):
            from ledger.services.common import version

            version(original, data["expected_version"])
            credit = records.create(
                request.user,
                "CREDIT",
                dict(
                    party=original.party,
                    currency=original.currency,
                    policy=original.policy,
                    date=data["date"],
                    description=data["reason"],
                    invoice=original.invoice,
                    credit_lines=data["lines"],
                ),
            )
            business_posting.guard_credit(request.user, credit.credit_note)
            return record_data(credit), [original, credit]

        return self.mutate(z.CancelInput, callback)

    @extend_schema(request=z.InvoiceIssueInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="record-sent")
    def record_sent(self, request, pk=None):
        obj = self.obj(pk, "change")
        check(
            obj.kind == "INVOICE" and obj.status == "POSTED",
            "Only posted invoices can be recorded as sent.",
        )

        def callback(data):
            from crm.integrations import validate_client_contact
            from django.utils import timezone
            from ledger.services.common import make, event

            validate_client_contact(client=obj.party, contact_party=data["recipient"])
            check(
                obj.date <= data["occurred_at"].date()
                and data["occurred_at"] <= timezone.now(),
                "Invalid sent time.",
            )
            issue = make(m.InvoiceIssue, request.user, invoice=obj.invoice, **data)
            event(request.user, "invoice_sent", obj, issue=issue.pk)
            procurement.tracking_event(
                request.user,
                obj,
                "INVOICE_SENT",
                f"Invoice {obj.reference} recorded as sent.",
            )
            return plain(issue), [obj]

        return self.mutate(z.InvoiceIssueInput, callback)

    @extend_schema(request=z.RemittanceEvidenceInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="remittance-evidence")
    def remittance_evidence(self, request, pk=None):
        obj = self.obj(pk, "change")
        require(request.user, "add_clientremittanceevidence")
        check(
            obj.kind == "WITHHOLDING" and obj.status == "POSTED",
            "Select a posted withholding record.",
        )

        def callback(data):
            from ledger.services.common import make, event, evidence, business_date

            evidence(request.user, data["document"], obj.party)
            check(
                business_date(data["verified_on"]) >= obj.date,
                "Verification cannot precede the deduction.",
            )
            proof = make(
                m.ClientRemittanceEvidence,
                request.user,
                withholding=obj.withholding,
                **data,
            )
            event(request.user, "client_remittance_evidence", obj, evidence=proof.pk)
            return plain(proof), [obj]

        return self.mutate(z.RemittanceEvidenceInput, callback)


class SetupView(BaseView):
    model = None

    def get_serializer_class(self):
        return z.SETUP_INPUT.get(self.model, z.StrictSerializer)

    def list(self, request):
        require(request.user, "view_" + self.model._meta.model_name)

        def output(obj):
            data = plain(obj)
            if self.model is m.AccountCurrency:
                data.update(account_name=obj.account.name, currency=obj.currency.code)
            if self.model is m.CashAccount:
                data.update(
                    currency=obj.account.currency.code,
                    custodian_name=(
                        obj.custodian.get_full_name() or obj.custodian.username
                    )
                    if obj.custodian
                    else "Unassigned",
                )
            return data

        return self.paginate(self.model.objects.all(), output)

    def retrieve(self, request, pk=None):
        require(request.user, "view_" + self.model._meta.model_name)
        try:
            obj = self.model.objects.get(pk=pk)
        except self.model.DoesNotExist:
            raise NotFound() from None
        return Response(plain(obj))

    def create(self, request):
        require(request.user, "add_" + self.model._meta.model_name)
        return self.mutate(
            z.SETUP_INPUT[self.model],
            lambda d: (plain(setup.create_setup(request.user, self.model, d)), []),
        )

    @extend_schema(request=z.SetupEditInput, responses=OpenApiTypes.OBJECT)
    def partial_update(self, request, pk=None):
        require(request.user, "change_" + self.model._meta.model_name)
        try:
            obj = self.model.objects.get(pk=pk)
        except self.model.DoesNotExist:
            raise NotFound() from None
        return self.mutate(
            z.SetupEditInput,
            lambda d: (plain(setup.update_setup(request.user, obj, d)), []),
        )


class YearView(SetupView):
    model = m.FinancialYear

    @extend_schema(request=z.YearInput, responses=OpenApiTypes.OBJECT)
    def create(self, request):
        require(request.user, "add_financialyear")
        return self.mutate(
            z.YearInput,
            lambda d: (plain(setup.create_year(request.user, d["year"])), []),
        )


class PeriodView(BaseView):
    def retrieve(self, request, pk=None):
        require(request.user, "view_accountingperiod")
        try:
            obj = m.AccountingPeriod.objects.get(pk=pk)
        except m.AccountingPeriod.DoesNotExist:
            raise NotFound() from None
        return Response(plain(obj))

    def list(self, request):
        require(request.user, "view_accountingperiod")
        return self.paginate(m.AccountingPeriod.objects.all())

    def period_action(self, request, pk, closed):
        require(
            request.user,
            "close_accountingperiod" if closed else "reopen_accountingperiod",
        )
        try:
            obj = m.AccountingPeriod.objects.get(pk=pk)
        except m.AccountingPeriod.DoesNotExist:
            raise NotFound() from None
        return self.mutate(
            z.PeriodInput,
            lambda d: (
                plain(
                    setup.period_action(
                        request.user, obj, d["expected_version"], closed, d["reason"]
                    )
                ),
                [],
            ),
        )

    @extend_schema(request=z.PeriodInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        return self.period_action(request, pk, True)

    @extend_schema(request=z.PeriodInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        return self.period_action(request, pk, False)


class AllocationView(BaseView):
    @extend_schema(request=z.AllocationInput, responses=OpenApiTypes.OBJECT)
    def create(self, request):
        # Authorize before an idempotent result can be replayed.
        require(
            request.user,
            "allocate_receipt"
            if request.data.get("source")
            and m.SettlementSource.objects.filter(
                pk=request.data["source"], record__kind="RECEIPT"
            ).exists()
            else "allocate_settlementsource",
        )

        def callback(d):
            obj = settlement.allocate(
                request.user,
                d["source"],
                d["invoice"],
                d["amount"],
                d["date"],
                d["tax_lines"],
            )
            return plain(obj), [obj.source.record, obj.invoice.record]

        return self.mutate(z.AllocationInput, callback)

    @extend_schema(request=z.ReleaseInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        try:
            obj = m.AllocationEvent.objects.get(pk=pk)
        except m.AllocationEvent.DoesNotExist:
            raise NotFound() from None
        get_record(request.user, obj.invoice.record_id)
        get_record(request.user, obj.source.record_id)
        require(
            request.user,
            "allocate_receipt"
            if obj.source.record.kind == "RECEIPT"
            else "allocate_settlementsource",
        )
        return self.mutate(
            z.ReleaseInput,
            lambda d: (
                plain(
                    settlement.release(
                        request.user, obj, d["amount"], d["date"], d["reason"]
                    )
                ),
                [obj.invoice.record, obj.source.record],
            ),
        )


class StatementView(BaseView):
    @extend_schema(request=z.StatementInput, responses=OpenApiTypes.OBJECT)
    def create(self, request):
        full_scope(request.user, "view_clientstatement")

        def callback(d):
            obj = generate_statement(
                request.user, d["client"], d["currency"], d["date_from"], d["date_to"]
            )
            return {
                "id": str(obj.pk),
                "generated_at": str(obj.created_at),
                "summary": {k: v for k, v in obj.data.items() if k != "rows"},
            }, []

        return self.mutate(z.StatementInput, callback)

    def retrieve(self, request, pk=None):
        full_scope(request.user, "view_clientstatement")
        try:
            obj = m.StatementSnapshot.objects.get(pk=pk)
        except m.StatementSnapshot.DoesNotExist:
            raise NotFound() from None
        pager = StandardPagination()
        page = pager.paginate_queryset(obj.data["rows"], request, view=self)
        response = pager.get_paginated_response(page)
        response.data["summary"] = {k: v for k, v in obj.data.items() if k != "rows"}
        start = pager.page.start_index() - 1
        response.data["page_opening_balance"] = (
            obj.data["rows"][start - 1]["running_balance"]
            if start
            else obj.data["opening_balance"]
        )
        return response

    @extend_schema(request=z.FormatInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def export(self, request, pk=None):
        full_scope(request.user, "export_clientstatement")
        require(request.user, "view_clientstatement")
        try:
            obj = m.StatementSnapshot.objects.get(pk=pk)
        except m.StatementSnapshot.DoesNotExist:
            raise NotFound() from None
        return self.mutate(
            z.FormatInput,
            lambda d: (
                {
                    "documents": [
                        plain(x)
                        for x in documents.generate(
                            request.user, statement=obj, format=d["format"]
                        )
                    ]
                },
                [],
            ),
        )


class AuthorizationView(BaseView):
    def retrieve(self, request, pk=None):
        full_scope(request.user, "view_clientwithholdingauthorization")
        try:
            obj = m.ClientWithholdingAuthorization.objects.get(pk=pk)
        except m.ClientWithholdingAuthorization.DoesNotExist:
            raise NotFound() from None
        return Response(plain(obj))

    def list(self, request):
        full_scope(request.user, "view_clientwithholdingauthorization")
        return self.paginate(
            m.ClientWithholdingAuthorization.objects.select_related("party"),
            lambda x: {**plain(x), "client_name": x.party.display_name},
        )

    @extend_schema(request=z.AuthorizationInput, responses=OpenApiTypes.OBJECT)
    def create(self, request):
        full_scope(request.user, "authorize_clientwithholdingauthorization")
        return self.mutate(
            z.AuthorizationInput, lambda d: (plain(tax.authorize(request.user, d)), [])
        )

    @extend_schema(request=z.DateReasonInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        full_scope(request.user, "authorize_clientwithholdingauthorization")
        try:
            obj = m.ClientWithholdingAuthorization.objects.get(pk=pk)
        except m.ClientWithholdingAuthorization.DoesNotExist:
            raise NotFound() from None
        return self.mutate(
            z.DateReasonInput,
            lambda d: (
                plain(tax.revoke(request.user, obj, d["date"], d["reason"])),
                [],
            ),
        )


class ApprovalView(BaseView):
    def list(self, request):
        full_scope(request.user, "view_approvalrequest")
        from django.db.models import Q

        kinds = [
            kind
            for kind, resource in m.RESOURCE.items()
            if request.user.has_perm("ledger.view_" + resource)
            or request.user.has_perm("ledger.approve_" + resource)
        ]
        scope = Q(revision__record__kind__in=kinds)
        if request.user.has_perm("ledger.approve_quotation"):
            scope |= Q(quotation_revision__isnull=False)
        qs = m.ApprovalRequest.objects.filter(scope).select_related(
            "revision__record", "quotation_revision__quotation"
        )
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])

        def output(obj):
            record = obj.revision.record if obj.revision_id else None
            return {
                **plain(obj),
                "record": record_data(record) if record else None,
                "quotation_revision": str(obj.quotation_revision_id)
                if obj.quotation_revision_id
                else None,
            }

        return self.paginate(qs, output)

    def decision(self, request, pk, approved):
        full_scope(request.user, "view_approvalrequest")
        try:
            obj = m.ApprovalRequest.objects.get(pk=pk)
        except m.ApprovalRequest.DoesNotExist:
            raise NotFound() from None
        if obj.quotation_revision_id:
            require(request.user, "approve_quotation")

            class Input(z.StrictSerializer):
                reason = z.serializers.CharField()

            return self.mutate(
                Input,
                lambda d: (
                    plain(
                        procurement.decide_quotation(
                            request.user, obj, approved, d["reason"]
                        )
                    ),
                    [],
                ),
            )
        record = get_record(request.user, obj.revision.record_id, action="approve")
        return self.mutate(
            z.DecisionInput,
            lambda d: (
                record_data(
                    records.decide(
                        request.user,
                        record,
                        d["expected_version"],
                        approved,
                        d["reason"],
                    )
                ),
                [record],
            ),
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self.decision(request, pk, True)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self.decision(request, pk, False)


class SubmissionView(BaseView):
    def retrieve(self, request, pk=None):
        full_scope(request.user, "view_financesubmission")
        try:
            obj = m.FinanceSubmission.objects.select_related("source").get(pk=pk)
        except m.FinanceSubmission.DoesNotExist:
            raise NotFound() from None
        return Response({**plain(obj), "source": plain(obj.source)})

    def list(self, request):
        full_scope(request.user, "view_financesubmission")
        return self.paginate(
            m.FinanceSubmission.objects.select_related("source"),
            lambda x: {**plain(x), "source": plain(x.source)},
        )

    @extend_schema(request=z.LinkSubmissionInput, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def prepare(self, request, pk=None):
        full_scope(request.user, "change_financesubmission")

        def callback(data):
            try:
                obj = m.FinanceSubmission.objects.select_for_update().get(pk=pk)
            except m.FinanceSubmission.DoesNotExist:
                raise NotFound() from None
            record = get_record(request.user, data["record"].pk, action="change")
            expected = {
                "INVOICE": "INVOICE",
                "EXPENSE": "EXPENSE",
                "ADVANCE": "ADVANCE",
                "ADVANCE_BREAKDOWN": "CLEARANCE",
                "SUPPLIER_REFUND": "RECEIPT",
            }
            check(not obj.record_id, "Submission already has a ledger record.")
            check(
                record.status == "DRAFT"
                and record.kind == expected[obj.source.kind]
                and record.request_id == obj.source.request_id
                and record.currency.code == obj.source.currency,
                "Ledger record does not match the finance submission.",
            )
            obj.record = record
            obj.save(update_fields=["record"])
            from ledger.services.common import event

            event(
                request.user,
                "finance_submission_prepared",
                record,
                data["reason"],
                submission=obj.pk,
            )
            return plain(obj), [record]

        return self.mutate(z.LinkSubmissionInput, callback)


def document_queryset(user):
    require(user, "view_ledgerdocument")
    from django.db.models import Q

    kinds = [k for k, v in m.RESOURCE.items() if user.has_perm("ledger.view_" + v)]
    visible = visible_records(user).filter(kind__in=kinds)
    scope = Q(record__in=visible) | Q(record__isnull=True, created_by=user)
    if user.has_perm("ledger.viewall_financialrecord"):
        scope |= Q(record__isnull=True)
    qs = m.LedgerDocument.objects.filter(scope)
    if not (
        user.has_perm("ledger.viewall_financialrecord")
        and user.has_perm("ledger.view_clientstatement")
    ):
        qs = qs.filter(statement__isnull=True)
    return qs


class DocumentView(BaseView):
    def list(self, request):
        return self.paginate(document_queryset(request.user))

    def retrieve(self, request, pk=None):
        try:
            obj = document_queryset(request.user).get(pk=pk)
        except m.LedgerDocument.DoesNotExist:
            raise NotFound() from None
        return Response(plain(obj))

    @extend_schema(request=z.DocumentInput, responses=OpenApiTypes.OBJECT)
    def create(self, request):
        require(request.user, "add_ledgerdocument")
        return self.mutate(
            z.DocumentInput,
            lambda d: (
                plain(documents.upload(request.user, d)),
                [d["record"]] if d.get("record") else [],
            ),
        )

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        try:
            doc = m.LedgerDocument.objects.get(pk=pk)
        except m.LedgerDocument.DoesNotExist:
            raise NotFound() from None
        file = documents.download(request.user, doc)
        response = FileResponse(
            file.file_object,
            as_attachment=True,
            filename=file.filename,
            content_type=file.mime_type,
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


def report_filter(qs, request, date_field="date", currency_field="currency__code"):
    fields = {
        "date_from": z.serializers.DateField(required=False),
        "date_to": z.serializers.DateField(required=False),
        "currency": z.serializers.CharField(required=False),
    }
    parsed = type("ReportFilter", (z.StrictSerializer,), dict(fields))(
        data={k: v for k, v in request.query_params.items() if k in fields}
    )
    parsed.is_valid(raise_exception=True)
    d = parsed.validated_data
    check(
        not d.get("date_from")
        or not d.get("date_to")
        or d["date_from"] <= d["date_to"],
        "Invalid date range.",
    )
    for key, suffix in [("date_from", "gte"), ("date_to", "lte")]:
        if d.get(key):
            qs = qs.filter(**{date_field + "__" + suffix: d[key]})
    if d.get("currency"):
        qs = qs.filter(**{currency_field: d["currency"]})
    return qs


def journal_data(obj):
    return {
        **plain(obj),
        "currency": obj.currency.code,
        "reference": obj.source.reference,
        "description": obj.source.description,
        "source_kind": obj.source.kind,
        "lines": [
            {
                **plain(x),
                "account_name": x.account.account.name,
                "account_code": x.account.account.code,
            }
            for x in obj.lines.all()
        ],
    }


class JournalView(BaseView):
    def list(self, request):
        full_scope(request.user, "view_journalentry")
        qs = report_filter(
            m.JournalEntry.objects.filter(posted_at__isnull=False), request
        )
        account = request.query_params.get("account")
        if account:
            parsed = z.serializers.UUIDField().run_validation(account)
            qs = qs.filter(lines__account_id=parsed).distinct()
        if request.query_params.get("kind"):
            qs = qs.filter(source__kind=request.query_params["kind"])
        if request.query_params.get("search"):
            qs = qs.filter(source__reference__icontains=request.query_params["search"])
        return self.paginate(
            qs.select_related("currency", "source").prefetch_related(
                "lines__account__account"
            ),
            journal_data,
        )

    def retrieve(self, request, pk=None):
        full_scope(request.user, "view_journalentry")
        try:
            obj = m.JournalEntry.objects.get(pk=pk, posted_at__isnull=False)
        except m.JournalEntry.DoesNotExist:
            raise NotFound() from None
        return Response(journal_data(obj))


class UtilityView(BaseView):
    @action(detail=False, methods=["get"])
    def capabilities(self, request):
        return Response(
            {
                "ledger": True,
                "primary_currency": "NGN",
                "default_wht_rate": "2.5",
                "currencies": list(
                    m.Currency.objects.filter(active=True).values_list(
                        "code", flat=True
                    )
                ),
                "revenue_recognition": "CLIENT_ACCEPTANCE",
                "self_approval": "PERMISSION_WITH_AUDIT",
                "statement_title": "Reconciliation and Statement",
                "fx_conversion": False,
                "approval_routing_configured": m.ApprovalRoutingPolicy.objects.filter(
                    enabled=True
                ).exists(),
                "tax_policies_configured": m.TaxPolicyVersion.objects.exists(),
                "policy_configured": m.PostingPolicyVersion.objects.exists(),
                "financial_year_configured": m.FinancialYear.objects.exists(),
            }
        )

    @extend_schema(request=z.ClientCodeInput, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"], url_path="client-codes")
    def client_codes(self, request):
        full_scope(request.user, "generate_financialrecord")
        return self.mutate(
            z.ClientCodeInput,
            lambda d: (
                plain(procurement.set_client_code(request.user, d["party"], d["code"])),
                [],
            ),
        )

    @extend_schema(request=z.RecognizeInput, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"], url_path="recognize-revenue")
    def recognize_revenue(self, request):
        require(request.user, "post_invoice")
        return self.mutate(
            z.RecognizeInput,
            lambda d: (
                plain(
                    procurement.recognize(
                        request.user, d["line"], d["quantity"], d["date"]
                    )
                ),
                [d["line"].invoice.record],
            ),
        )

    @action(detail=False, methods=["get"], url_path="tax-reports")
    def tax_reports(self, request):
        parsed = z.TaxReportInput(data=request.query_params)
        parsed.is_valid(raise_exception=True)
        return Response(tax.report(request.user, **parsed.validated_data))

    @extend_schema(request=z.RoutingInput, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"], url_path="approval-routing")
    def approval_routing(self, request):
        full_scope(request.user, "add_approvalroutingpolicy")
        from ledger.integrations.workflow import authorize_routing

        return self.mutate(
            z.RoutingInput, lambda d: (plain(authorize_routing(request.user, **d)), [])
        )

    @action(detail=False, methods=["get"], url_path="trial-balance")
    def trial_balance(self, request):
        full_scope(request.user, "view_journalentry")
        parsed = z.TaxReportInput(data=request.query_params)
        parsed.is_valid(raise_exception=True)
        d = parsed.validated_data
        check(d["date_from"] <= d["date_to"], "Invalid date range.")
        from django.db.models import Sum

        qs = m.JournalLine.objects.filter(
            entry__posted_at__isnull=False,
            entry__currency=d["currency"],
            entry__date__range=(d["date_from"], d["date_to"]),
        )
        rows = list(
            qs.values("account__account__code", "account__account__name")
            .annotate(debit=Sum("debit"), credit=Sum("credit"))
            .order_by("account__account__code")
        )
        return Response(
            primitive(
                {
                    "currency": d["currency"].code,
                    "results": rows,
                    "debit": sum((r["debit"] for r in rows), Decimal(0)),
                    "credit": sum((r["credit"] for r in rows), Decimal(0)),
                }
            )
        )

    @action(detail=False, methods=["get"])
    def analytics(self, request):
        full_scope(request.user, "view_journalentry")
        parsed = z.TaxReportInput(data=request.query_params)
        parsed.is_valid(raise_exception=True)
        d = parsed.validated_data
        check(d["date_from"] <= d["date_to"], "Invalid date range.")
        from django.db.models import Sum
        from django.db.models.functions import TruncMonth

        all_lines = m.JournalLine.objects.filter(
            entry__posted_at__isnull=False,
            entry__currency=d["currency"],
            entry__date__lte=d["date_to"],
        )
        lines = all_lines.filter(entry__date__gte=d["date_from"])
        balances = list(
            all_lines.values(
                "account_id",
                "account__account__name",
                "account__account__classification",
            ).annotate(debit=Sum("debit"), credit=Sum("credit"))
        )
        for row in balances:
            value = row["debit"] - row["credit"]
            row["balance"] = (
                value
                if row["account__account__classification"] in {"ASSET", "EXPENSE"}
                else -value
            )
        monthly = list(
            lines.annotate(month=TruncMonth("entry__date"))
            .values("month", "account__account__classification")
            .annotate(debit=Sum("debit"), credit=Sum("credit"))
            .order_by("month")
        )
        return Response(
            primitive(
                {
                    "currency": d["currency"].code,
                    "date_from": d["date_from"],
                    "date_to": d["date_to"],
                    "balances": balances,
                    "monthly": monthly,
                }
            )
        )

    @action(detail=False, methods=["get"])
    def lookups(self, request):
        kind = request.query_params.get("kind", "")
        from crm.models import Party
        from django.contrib.auth import get_user_model
        from rest_framework.exceptions import PermissionDenied
        from django.db.models import Q

        if kind == "parties":
            if not request.user.has_perm("crm.view_party"):
                raise PermissionDenied("Client directory permission required.")
            qs = Party.objects.all()
            if request.query_params.get("search"):
                qs = qs.filter(display_name__icontains=request.query_params["search"])
            return self.paginate(
                qs, lambda x: {"id": str(x.pk), "label": x.display_name}
            )
        if kind == "requests":
            if not request.user.has_perm("procurement.view_procurementrequest"):
                raise PermissionDenied("Procurement request permission required.")
            from procurement.services import visible_requests

            qs = visible_requests(request.user)
            if request.query_params.get("search"):
                qs = qs.filter(
                    Q(number__icontains=request.query_params["search"])
                    | Q(client_name__icontains=request.query_params["search"])
                )
            return self.paginate(
                qs,
                lambda x: {"id": str(x.pk), "label": x.number + " — " + x.client_name},
            )
        if kind in {"request-items", "order-lines"}:
            from procurement import models as pm
            from procurement.services import visible_requests

            permission = (
                "procurement.view_requestitem"
                if kind == "request-items"
                else "procurement.view_clientpurchaseorder"
            )
            if not request.user.has_perm(permission):
                raise PermissionDenied("Procurement document permission required.")
            qs = (
                pm.RequestItem.objects.filter(
                    request__in=visible_requests(request.user)
                )
                if kind == "request-items"
                else pm.ClientPurchaseOrderLine.objects.filter(
                    order__request__in=visible_requests(request.user)
                )
            )
            if request.query_params.get("request"):
                qs = qs.filter(
                    **{
                        "request_id"
                        if kind == "request-items"
                        else "order__request_id": request.query_params["request"]
                    }
                )
            if request.query_params.get("search"):
                qs = qs.filter(
                    **{
                        "description__icontains"
                        if kind == "request-items"
                        else "item_description__icontains": request.query_params[
                            "search"
                        ]
                    }
                )
            return self.paginate(
                qs,
                lambda x: {
                    "id": str(x.pk),
                    "label": x.description
                    if kind == "request-items"
                    else x.item_description or str(x.pk),
                },
            )
        if kind == "staff":
            if not request.user.has_perm("accounts.view_user"):
                raise PermissionDenied("Staff directory permission required.")
            qs = get_user_model().objects.filter(is_active=True, is_staff=True)
            if request.query_params.get("search"):
                qs = qs.filter(username__icontains=request.query_params["search"])
            return self.paginate(
                qs,
                lambda x: {"id": str(x.pk), "label": x.get_full_name() or x.username},
            )
        models = {
            "currencies": m.Currency,
            "currency-ids": m.Currency,
            "accounts": m.LedgerAccount,
            "account-currencies": m.AccountCurrency,
            "cash-accounts": m.CashAccount,
            "posting-policies": m.PostingPolicyVersion,
            "tax-types": m.TaxType,
            "tax-policies": m.TaxPolicyVersion,
            "documents": m.LedgerDocument,
            "withholding-authorizations": m.ClientWithholdingAuthorization,
        }
        if kind in models:
            model = models[kind]
            require(request.user, "view_" + model._meta.model_name)
            qs = (
                document_queryset(request.user)
                if kind == "documents"
                else model.objects.all()
            )
            if kind == "withholding-authorizations":
                full_scope(request.user, "view_" + model._meta.model_name)
            names = {f.name for f in model._meta.fields}
            query = request.query_params.get("search")
            if query:
                filters = Q()
                for name in ["name", "code", "filename", "reason"]:
                    if name in names:
                        filters |= Q(**{name + "__icontains": query})
                qs = qs.filter(filters)
            if "active" in names:
                qs = qs.filter(active=True)

            def output(x):
                label = (
                    getattr(x, "name", None)
                    or getattr(x, "filename", None)
                    or str(x.pk)
                )
                if kind == "currencies":
                    return {"id": x.code, "label": x.code + " — " + x.name}
                if kind == "account-currencies":
                    label = (
                        x.account.code
                        + " — "
                        + x.account.name
                        + " ("
                        + x.currency.code
                        + ")"
                    )
                if kind == "cash-accounts":
                    label = x.name + " (" + x.account.currency.code + ")"
                if kind == "tax-policies":
                    label = x.tax_type.name + " — " + str(x.rate) + "%"
                if kind == "withholding-authorizations":
                    label = (
                        x.party.display_name
                        + " — "
                        + x.treatment
                        + " "
                        + str(x.rate)
                        + "%"
                    )
                return {"id": str(x.pk), "label": label}

            return self.paginate(qs, output)
        if kind == "financial-records":
            kinds = [
                k
                for k, v in m.RESOURCE.items()
                if request.user.has_perm("ledger.view_" + v)
            ]
            qs = (
                visible_records(request.user)
                .filter(kind__in=kinds)
                .select_related("currency")
            )
            if request.query_params.get("search"):
                qs = qs.filter(
                    Q(reference__icontains=request.query_params["search"])
                    | Q(party_name__icontains=request.query_params["search"])
                )
            return self.paginate(
                qs,
                lambda x: {
                    "id": str(x.pk),
                    "label": x.kind
                    + " — "
                    + (x.reference or x.description)
                    + " ("
                    + x.currency.code
                    + ")",
                },
            )
        if kind == "invoices-for-allocation":
            require(request.user, "view_invoice")
            qs = m.Invoice.objects.filter(
                record__in=visible_records(request.user).filter(
                    kind="INVOICE", status="POSTED"
                )
            ).select_related("record")
            if request.query_params.get("search"):
                qs = qs.filter(
                    record__reference__icontains=request.query_params["search"]
                )
            return self.paginate(
                qs,
                lambda x: {
                    "id": str(x.pk),
                    "label": x.record.reference + " — " + x.record.party_name,
                },
            )
        if kind == "invoice-lines":
            require(request.user, "view_invoice")
            qs = m.InvoiceLine.objects.filter(
                invoice__record__in=visible_records(request.user).filter(
                    kind="INVOICE", status="POSTED"
                )
            ).select_related("invoice__record")
            if request.query_params.get("request"):
                qs = qs.filter(
                    invoice__record__request_id=request.query_params["request"]
                )
            if request.query_params.get("search"):
                qs = qs.filter(description__icontains=request.query_params["search"])
            return self.paginate(
                qs,
                lambda x: {
                    "id": str(x.pk),
                    "label": x.invoice.record.reference + " — " + x.description,
                    "net": str(x.net),
                },
            )
        if kind in {
            "invoices",
            "receipts",
            "payments",
            "expenses",
            "advances",
            "tax-remittances",
            "supplier-bills",
            "advance-clearances",
        }:
            kinds = {
                "invoices": "INVOICE",
                "receipts": "RECEIPT",
                "payments": "PAYMENT",
                "expenses": "EXPENSE",
                "advances": "ADVANCE",
                "tax-remittances": "TAX_REMITTANCE",
                "supplier-bills": "SUPPLIER_BILL",
                "advance-clearances": "CLEARANCE",
            }
            k = kinds[kind]
            require(request.user, "view_" + m.RESOURCE[k])
            qs = visible_records(request.user).filter(kind=k).select_related("currency")
            if request.query_params.get("search"):
                qs = qs.filter(
                    Q(reference__icontains=request.query_params["search"])
                    | Q(party_name__icontains=request.query_params["search"])
                )
            return self.paginate(
                qs,
                lambda x: {
                    "id": str(x.pk),
                    "label": (x.reference or x.description or x.kind)
                    + " — "
                    + x.party_name
                    + " ("
                    + x.currency.code
                    + ")",
                },
            )
        raise ValidationError("Unknown lookup.")

    @action(detail=False, methods=["get"])
    def account_book(self, request):
        full_scope(request.user, "view_journalentry")

        class Input(z.TaxReportInput):
            account = z.pk(m.AccountCurrency)
            page = z.serializers.IntegerField(required=False, min_value=1)

        parsed = Input(
            data={
                k: v
                for k, v in request.query_params.items()
                if k in {"account", "currency", "date_from", "date_to", "page"}
            }
        )
        parsed.is_valid(raise_exception=True)
        d = parsed.validated_data
        check(d["date_from"] <= d["date_to"], "Invalid date range.")
        check(
            d["account"].currency_id == d["currency"].pk,
            "Account belongs to a different currency.",
        )
        from django.db.models import Sum, F, Window

        qs = m.JournalLine.objects.filter(
            account=d["account"], entry__posted_at__isnull=False
        )
        prior = qs.filter(entry__date__lt=d["date_from"]).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        opening = (prior["debit"] or Decimal(0)) - (prior["credit"] or Decimal(0))
        period = qs.filter(entry__date__range=(d["date_from"], d["date_to"]))
        totals = period.aggregate(debit=Sum("debit"), credit=Sum("credit"))
        debit = totals["debit"] or Decimal(0)
        credit = totals["credit"] or Decimal(0)
        ordered = (
            period.annotate(
                running=Window(
                    Sum(F("debit") - F("credit")),
                    order_by=[
                        F("entry__date").asc(),
                        F("created_at").asc(),
                        F("id").asc(),
                    ],
                )
            )
            .order_by("entry__date", "created_at", "id")
            .select_related("entry__source")
        )
        response = self.paginate(
            ordered,
            lambda x: {
                "id": str(x.pk),
                "date": str(x.entry.date),
                "reference": x.entry.source.reference,
                "description": x.entry.source.description,
                "debit": str(x.debit),
                "credit": str(x.credit),
                "running_balance": str(opening + x.running),
                "journal": str(x.entry_id),
            },
        )
        response.data["summary"] = {
            "currency": d["currency"].code,
            "account": d["account"].account.name,
            "opening_balance": str(opening),
            "debit": str(debit),
            "credit": str(credit),
            "closing_balance": str(opening + debit - credit),
            "balance_convention": "Debit positive; credit negative",
        }
        return response
