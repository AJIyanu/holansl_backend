"""Explicit write contracts; read contracts exclude private storage identifiers."""

from rest_framework import serializers
from . import models as m


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {key: "Unsupported field." for key in unknown}
            )
        return super().to_internal_value(data)


def pk(model, **kwargs):
    return serializers.PrimaryKeyRelatedField(queryset=model.objects.all(), **kwargs)


def amount(**kwargs):
    return serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=0, **kwargs
    )


def quantity(**kwargs):
    from decimal import Decimal

    return serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001"), **kwargs
    )


class ItemInput(StrictSerializer):
    description = serializers.CharField()
    specification = serializers.CharField(required=False, allow_blank=True)
    quantity = quantity()
    unit = serializers.CharField(max_length=40)
    required_date = serializers.DateField(required=False, allow_null=True)


class RequestInput(StrictSerializer):
    from crm.models import Party
    from django.contrib.auth import get_user_model
    from accounts.models import Department

    client = pk(Party)
    contact = pk(Party, required=False, allow_null=True)
    owner = pk(get_user_model(), required=False)
    department = pk(Department, required=False, allow_null=True)
    received_at = serializers.DateTimeField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    items = ItemInput(many=True, allow_empty=False)


class RequestEditInput(StrictSerializer):
    notes = serializers.CharField(required=False, allow_blank=True)
    expected_version = serializers.IntegerField(min_value=0)


class OfferLineInput(StrictSerializer):
    item = pk(m.RequestItem)
    attempt = pk(m.FulfilmentAttempt, required=False, allow_null=True)
    quantity = quantity()
    unit_price = amount()
    charges = amount(required=False)


class OfferInput(StrictSerializer):
    from crm.models import Party

    request = pk(m.ProcurementRequest)
    supplier = pk(Party)
    reference = serializers.CharField(max_length=150, required=False, allow_blank=True)
    currency = serializers.RegexField(r"^[A-Z]{3}$")
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    document = pk(m.ProcurementDocument, required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True)
    lines = OfferLineInput(many=True, allow_empty=False)


class QuoteLineInput(StrictSerializer):
    item = pk(m.RequestItem)
    supplier_offer = pk(m.SupplierQuotationLine)
    description = serializers.CharField()
    quantity = quantity()
    unit_price = amount()
    estimated_unit_cost = amount()
    discount = amount(required=False)
    tax = amount(required=False)
    logistics = amount(required=False)
    additional_charges = amount(required=False)


class QuoteInput(StrictSerializer):
    request = pk(m.ProcurementRequest)
    currency = serializers.RegexField(r"^[A-Z]{3}$")
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    document = pk(m.ProcurementDocument, required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True)
    lines = QuoteLineInput(many=True, allow_empty=False)


class IssueInput(StrictSerializer):
    from crm.models import Party

    recipient = pk(Party)
    method = serializers.CharField(max_length=40)
    evidence = serializers.CharField()
    occurred_at = serializers.DateTimeField(required=False)


class POLineInput(StrictSerializer):
    quote_line = pk(m.ClientQuotationLine)
    quantity = quantity()
    unit_price = amount()


class POInput(StrictSerializer):
    request = pk(m.ProcurementRequest)
    reference = serializers.CharField(max_length=150)
    verbal = serializers.BooleanField(default=False)
    evidence = serializers.CharField(required=False, allow_blank=True)
    document = pk(m.ProcurementDocument, required=False, allow_null=True)
    received_at = serializers.DateTimeField(required=False)
    lines = POLineInput(many=True, allow_empty=False)


class PurchaseLineInput(StrictSerializer):
    attempt = pk(m.FulfilmentAttempt)
    supplier_offer = pk(m.SupplierQuotationLine)
    quantity = quantity()
    unit_price = amount()


class PurchaseInput(StrictSerializer):
    from crm.models import Party

    request = pk(m.ProcurementRequest)
    supplier = pk(Party)
    currency = serializers.RegexField(r"^[A-Z]{3}$")
    reference = serializers.CharField(max_length=150, required=False, allow_blank=True)
    document = pk(m.ProcurementDocument, required=False, allow_null=True)
    lines = PurchaseLineInput(many=True, allow_empty=False)


class ReceiptLineInput(StrictSerializer):
    purchase_line = pk(m.SupplierPurchaseLine)
    quantity = quantity()


class ReceiptInput(StrictSerializer):
    purchase = pk(m.SupplierPurchase)
    received_at = serializers.DateTimeField(required=False)
    document = pk(m.ProcurementDocument, required=False, allow_null=True)
    lines = ReceiptLineInput(many=True, allow_empty=False)


class DeliveryLineInput(StrictSerializer):
    receipt_line = pk(m.GoodsReceiptLine)
    quantity = quantity()


class DeliveryInput(StrictSerializer):
    from crm.models import Party

    request = pk(m.ProcurementRequest)
    destination = serializers.CharField()
    recipient = pk(Party)
    logistics_details = serializers.CharField(required=False, allow_blank=True)
    waybill = pk(m.ProcurementDocument)
    lines = DeliveryLineInput(many=True, allow_empty=False)


class OutcomeLineInput(StrictSerializer):
    delivery_line = pk(m.DeliveryLine)
    accepted = amount(default=0)
    rejected = amount(default=0)
    reason = serializers.CharField(required=False, allow_blank=True)


class OutcomeInput(StrictSerializer):
    from crm.models import Party

    receiver = pk(Party)
    occurred_at = serializers.DateTimeField(required=False)
    signed_waybill = pk(m.ProcurementDocument, required=False, allow_null=True)
    lines = OutcomeLineInput(many=True, allow_empty=False)


class RejectionInput(StrictSerializer):
    kind = serializers.ChoiceField(choices=m.RejectionAction.Kind.choices)
    quantity = quantity()
    reason = serializers.CharField()
    document = pk(m.ProcurementDocument, required=False, allow_null=True)
    refund_amount = amount(required=False, allow_null=True)
    currency = serializers.RegexField(r"^[A-Z]{3}$", required=False)


class ReorderInput(StrictSerializer):
    quantity = quantity()
    reason = serializers.CharField()
    required_date = serializers.DateField(required=False, allow_null=True)


class CostInput(StrictSerializer):
    item = pk(m.RequestItem)
    kind = serializers.ChoiceField(
        choices=m.ProcurementCost._meta.get_field("kind").choices
    )
    amount = amount()
    currency = serializers.RegexField(r"^[A-Z]{3}$")
    description = serializers.CharField()
    document = pk(m.ProcurementDocument)


class FinanceLineInput(StrictSerializer):
    item = pk(m.RequestItem)
    po_line = pk(m.ClientPurchaseOrderLine, required=False, allow_null=True)
    delivery_line = pk(m.DeliveryLine, required=False, allow_null=True)
    amount = amount()
    quantity = quantity()
    document = pk(m.ProcurementDocument, required=False, allow_null=True)


class FinanceInput(StrictSerializer):
    request = pk(m.ProcurementRequest)
    kind = serializers.ChoiceField(
        choices=m.FinanceRequest._meta.get_field("kind").choices
    )
    currency = serializers.RegexField(r"^[A-Z]{3}$")
    notes = serializers.CharField(required=False, allow_blank=True)
    lines = FinanceLineInput(many=True, allow_empty=False)


class DocumentInput(StrictSerializer):
    request = pk(m.ProcurementRequest)
    category = serializers.ChoiceField(choices=m.ProcurementDocument.Category.choices)
    file = serializers.FileField()


class TaskLinkInput(StrictSerializer):
    from tasks.models import Task

    item = pk(m.RequestItem)
    task = pk(Task)


class EmptyInput(StrictSerializer):
    pass


# Every read model has its own schema component and only exposes stored business fields.
READ = {}
for model in [
    m.RequestItem,
    m.ProcurementDocument,
    m.SupplierQuotationLine,
    m.ClientQuotationLine,
    m.QuotationIssue,
    m.ClientPurchaseOrderLine,
    m.SupplierPurchaseLine,
    m.GoodsReceiptLine,
    m.DeliveryLine,
    m.DeliveryOutcomeLine,
    m.RejectionAction,
    m.ItemTrackingEvent,
    m.FulfilmentAttempt,
    m.ProcurementCost,
    m.FinanceRequestLine,
    m.ProcurementTaskLink,
]:
    fields = [
        f.name
        for f in model._meta.fields
        if f.name not in {"storage_provider", "external_file_id", "storage_path"}
    ]
    meta = type(
        "Meta", (), {"model": model, "fields": fields, "read_only_fields": fields}
    )
    READ[model] = type(
        model.__name__ + "Serializer", (serializers.ModelSerializer,), {"Meta": meta}
    )

for model, children in [
    (m.ProcurementRequest, {"items": m.RequestItem}),
    (m.SupplierQuotation, {"lines": m.SupplierQuotationLine}),
    (
        m.ClientQuotationRevision,
        {"lines": m.ClientQuotationLine, "issues": m.QuotationIssue},
    ),
    (m.ClientQuotation, {"revisions": m.ClientQuotationRevision}),
    (m.ClientPurchaseOrder, {"lines": m.ClientPurchaseOrderLine}),
    (m.SupplierPurchase, {"lines": m.SupplierPurchaseLine}),
    (m.GoodsReceipt, {"lines": m.GoodsReceiptLine}),
    (m.Delivery, {"lines": m.DeliveryLine}),
    (m.DeliveryOutcome, {"lines": m.DeliveryOutcomeLine}),
    (m.FinanceRequest, {"lines": m.FinanceRequestLine}),
]:
    fields = [f.name for f in model._meta.fields] + list(children)
    attrs = {
        "Meta": type(
            "Meta", (), {"model": model, "fields": fields, "read_only_fields": fields}
        )
    }
    attrs.update(
        {
            name: READ[child](many=True, read_only=True)
            for name, child in children.items()
        }
    )
    READ[model] = type(
        model.__name__ + "Serializer", (serializers.ModelSerializer,), attrs
    )
