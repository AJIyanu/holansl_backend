"""Explicit write contracts; never accept approval, totals, actors or posting flags."""

from collections.abc import Mapping
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer
from ledger import models as m
from crm.models import Party
from django.contrib.auth import get_user_model
from procurement import models as pm


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError("Expected a JSON object.")
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {name: "Unknown or read-only field." for name in unknown}
            )
        return super().to_internal_value(data)


def pk(model, **kwargs):
    return serializers.PrimaryKeyRelatedField(queryset=model.objects.all(), **kwargs)


def money(**kwargs):
    return serializers.DecimalField(max_digits=24, decimal_places=4, **kwargs)


class InvoiceLineInput(StrictSerializer):
    description = serializers.CharField()
    unit = serializers.CharField(max_length=40, default="unit")
    quantity = money(min_value=0)
    unit_price = money(min_value=0)
    discount = money(min_value=0, default=0)
    item = pk(pm.RequestItem, required=False)
    quote_line = pk(pm.ClientQuotationLine, required=False)
    po_line = pk(pm.ClientPurchaseOrderLine, required=False)
    tax_policy = pk(m.TaxPolicyVersion, required=False)


class CostLineInput(StrictSerializer):
    account = pk(m.LedgerAccount)
    amount = money(min_value=0)
    description = serializers.CharField()
    evidence = pk(m.LedgerDocument)
    procurement_cost = pk(pm.ProcurementCost, required=False)


class CreditLineInput(StrictSerializer):
    line = pk(m.InvoiceLine)
    quantity = money(min_value=0)


class ManualLineInput(StrictSerializer):
    account = pk(m.AccountCurrency)
    debit = money(min_value=0, default=0)
    credit = money(min_value=0, default=0)
    party = pk(Party, required=False)


class FinancialInput(StrictSerializer):
    party = pk(Party, required=False)
    currency = serializers.SlugRelatedField(
        slug_field="code", queryset=m.Currency.objects.all()
    )
    policy = pk(m.PostingPolicyVersion)
    date = serializers.DateField()
    description = serializers.CharField(required=False, allow_blank=True)
    reference = serializers.CharField(max_length=150, required=False, allow_blank=True)
    request = pk(pm.ProcurementRequest, required=False)
    evidence = pk(m.LedgerDocument, required=False)
    due_date = serializers.DateField(required=False)
    lines = InvoiceLineInput(many=True, required=False, allow_empty=False)
    amount = money(min_value=0, required=False)
    cash_account = pk(m.CashAccount, required=False)
    purpose = serializers.CharField(required=False)
    target = pk(m.FinancialRecord, required=False)
    cost_lines = CostLineInput(many=True, required=False, allow_empty=False)
    custodian = pk(get_user_model(), required=False)
    advance = pk(m.FinancialRecord, required=False)
    invoice = pk(m.Invoice, required=False)
    credit_lines = CreditLineInput(many=True, required=False, allow_empty=False)
    line = pk(m.InvoiceLine, required=False)
    authorization = pk(m.ClientWithholdingAuthorization, required=False)
    certificate_reference = serializers.CharField(required=False)
    tax_policy = pk(m.TaxPolicyVersion, required=False)
    period_from = serializers.DateField(required=False)
    period_to = serializers.DateField(required=False)
    journal_lines = ManualLineInput(many=True, required=False, allow_empty=False)

    def validate(self, data):
        kind = self.context["kind"]
        extras = {
            "INVOICE": {"lines", "due_date"},
            "RECEIPT": {"amount", "cash_account", "purpose", "target"},
            "PAYMENT": {"amount", "cash_account", "purpose", "target"},
            "EXPENSE": {"cost_lines"},
            "SUPPLIER_BILL": {"cost_lines"},
            "CLEARANCE": {"cost_lines", "advance"},
            "ADVANCE": {"amount", "custodian"},
            "CREDIT": {"invoice", "credit_lines"},
            "WITHHOLDING": {"line", "authorization", "certificate_reference", "amount"},
            "TAX_REMITTANCE": {"tax_policy", "period_from", "period_to", "amount"},
            "MANUAL": {"journal_lines"},
            "OPENING": {"journal_lines"},
        }[kind]
        common = {
            "party",
            "currency",
            "policy",
            "date",
            "description",
            "reference",
            "request",
            "evidence",
        }
        invalid = set(data) - common - extras
        if invalid:
            raise serializers.ValidationError(
                {k: "Field is not valid for this record type." for k in invalid}
            )
        required = extras - {"due_date", "target"}
        missing = required - set(data)
        if missing:
            raise serializers.ValidationError(
                {k: "This field is required." for k in missing}
            )
        return data


class VersionInput(StrictSerializer):
    expected_version = serializers.IntegerField(min_value=1)


class DecisionInput(VersionInput):
    reason = serializers.CharField()


class EditInput(VersionInput):
    description = serializers.CharField(required=False, allow_blank=True)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=150)


class DateReasonInput(StrictSerializer):
    date = serializers.DateField()
    reason = serializers.CharField()


class TaxAllocationInput(StrictSerializer):
    line = pk(m.InvoiceLine)
    amount = money(min_value=0)


class AllocationInput(StrictSerializer):
    source = pk(m.SettlementSource)
    invoice = pk(m.Invoice)
    amount = money(min_value=0)
    date = serializers.DateField()
    tax_lines = TaxAllocationInput(many=True, default=list)


class ReleaseInput(DateReasonInput):
    amount = money(min_value=0)


class StatementInput(StrictSerializer):
    client = pk(Party)
    currency = serializers.SlugRelatedField(
        slug_field="code", queryset=m.Currency.objects.all()
    )
    date_from = serializers.DateField()
    date_to = serializers.DateField()


class TaxReportInput(StrictSerializer):
    currency = serializers.SlugRelatedField(
        slug_field="code", queryset=m.Currency.objects.all()
    )
    date_from = serializers.DateField()
    date_to = serializers.DateField()


class AuthorizationInput(StrictSerializer):
    party = pk(Party)
    tax_type = pk(m.TaxType)
    effective_from = serializers.DateField()
    effective_to = serializers.DateField(required=False, allow_null=True)
    reason = serializers.CharField()
    evidence = pk(m.LedgerDocument)


class RecognizeInput(StrictSerializer):
    line = pk(m.InvoiceLine)
    quantity = money(min_value=0)
    date = serializers.DateField()


class RemittanceEvidenceInput(StrictSerializer):
    document = pk(m.LedgerDocument)
    reference = serializers.CharField(max_length=150)
    verified_on = serializers.DateField()


@extend_schema_serializer(component_name="LedgerClientCodeInput")
class ClientCodeInput(StrictSerializer):
    party = pk(Party)
    code = serializers.RegexField(r"^[A-Z]{3,4}$")


@extend_schema_serializer(component_name="LedgerDocumentInput")
class DocumentInput(StrictSerializer):
    file = serializers.FileField()
    record = pk(m.FinancialRecord, required=False)
    party = pk(Party, required=False)


class FormatInput(StrictSerializer):
    format = serializers.ChoiceField(choices=["PDF", "JPG"], default="PDF")


class YearInput(StrictSerializer):
    year = serializers.IntegerField(min_value=1900, max_value=2200)


class SetupEditInput(StrictSerializer):
    name = serializers.CharField(max_length=150, required=False)
    symbol = serializers.CharField(max_length=8, required=False)
    authority = serializers.CharField(max_length=150, required=False)
    active = serializers.BooleanField(required=False)
    reason = serializers.CharField()


class PeriodInput(StrictSerializer):
    expected_version = serializers.IntegerField(min_value=0)
    reason = serializers.CharField()


SETUP_FIELDS = {
    m.Currency: ["code", "name", "symbol", "minor_units", "active"],
    m.LedgerAccount: ["code", "name", "classification", "purpose", "parent", "active"],
    m.AccountCurrency: ["account", "currency"],
    m.CashAccount: ["name", "kind", "account", "active"],
    m.PostingPolicyVersion: [
        "name",
        "receivable",
        "payable",
        "deferred_revenue",
        "revenue",
        "advance",
        "opening_equity",
    ],
    m.TaxType: ["code", "name", "authority"],
    m.TaxPolicyVersion: [
        "tax_type",
        "effective_from",
        "effective_to",
        "rate",
        "liability_account",
        "withheld_account",
        "treatment",
        "verified_reference",
    ],
}


class StrictModelSerializer(serializers.ModelSerializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError("Expected a JSON object.")
        extra = set(data) - set(self.fields)
        if extra:
            raise serializers.ValidationError({k: "Unknown field." for k in extra})
        return super().to_internal_value(data)


SETUP_INPUT = {
    model: type(
        model.__name__ + "Input",
        (StrictModelSerializer,),
        {"Meta": type("Meta", (), {"model": model, "fields": fields})},
    )
    for model, fields in SETUP_FIELDS.items()
}


class CancelInput(VersionInput):
    date = serializers.DateField()
    reason = serializers.CharField()
    lines = CreditLineInput(many=True, allow_empty=False)


class RoutingInput(StrictSerializer):
    enabled = serializers.BooleanField()
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=["DASHBOARD", "EMAIL"]),
        default=["DASHBOARD"],
    )
    reason = serializers.CharField()


class LinkSubmissionInput(StrictSerializer):
    record = pk(m.FinancialRecord)
    reason = serializers.CharField()


class InvoiceIssueInput(StrictSerializer):
    recipient = pk(Party)
    method = serializers.CharField(max_length=40)
    occurred_at = serializers.DateTimeField()
    evidence = serializers.CharField()


class ReviseInput(FinancialInput):
    expected_version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField()

    def validate(self, data):
        extras = {key: data.pop(key) for key in ["expected_version", "reason"]}
        return {**super().validate(data), **extras}


class FinancialSummaryOutput(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    party_id = serializers.UUIDField(allow_null=True)
    party_name = serializers.CharField()
    currency = serializers.CharField()
    date = serializers.DateField()
    reference = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField()
    version = serializers.IntegerField()
    amount = money()
    net_amount = money()
    tax_amount = money()
