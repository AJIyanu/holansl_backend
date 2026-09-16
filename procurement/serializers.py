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

    client = pk(Party)
    contact = pk(Party, required=False, allow_null=True)
    received_at = serializers.DateTimeField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    items = ItemInput(many=True, allow_empty=False)


class SubmitRequestInput(StrictSerializer):
    expected_version = serializers.IntegerField(min_value=0, required=False)


class ItemEditInput(ItemInput):
    expected_version = serializers.IntegerField(min_value=0)


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
    expires_at = serializers.DateField(required=False, allow_null=True)
    document = pk(m.ProcurementDocument, required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True)
    lines = OfferLineInput(many=True, allow_empty=False)


class QuoteLineInput(StrictSerializer):
    item = pk(m.RequestItem)
    supplier_offer = pk(m.SupplierQuotationLine)
    description = serializers.CharField()
    quantity = quantity()
    unit_price = amount()
    discount_type = serializers.ChoiceField(
        choices=["AMOUNT", "PERCENT"], default="AMOUNT"
    )
    discount_value = amount(required=False, default=0)
    tax_rate = serializers.DecimalField(
        max_digits=7, decimal_places=4, min_value=0, max_value=100, default="7.5"
    )
    logistics = amount(required=False)
    additional_charges = amount(required=False)


class QuoteInput(StrictSerializer):
    request = pk(m.ProcurementRequest)
    currency = serializers.RegexField(r"^[A-Z]{3}$")
    expires_at = serializers.DateField(required=False, allow_null=True)
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
    quote_line = pk(m.ClientQuotationLine, required=False, allow_null=True)
    invoice_line = serializers.UUIDField(required=False)
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
    waybill = pk(m.ProcurementDocument, required=False, allow_null=True)
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


class FinanceCapabilitySerializer(serializers.Serializer):
    available = serializers.BooleanField()
    status = serializers.CharField()
    reason = serializers.CharField()


class CapabilitySerializer(serializers.Serializer):
    document_templates = serializers.DictField()
    ledger = FinanceCapabilitySerializer()
    replacement_attempts = serializers.BooleanField()
    private_documents = serializers.BooleanField()
    client_reorders = serializers.BooleanField()
    automatic_external_sending = serializers.BooleanField()
    numbering = serializers.CharField()


class MilestoneSerializer(serializers.Serializer):
    event_count = serializers.IntegerField()
    latest_event = serializers.DictField(allow_null=True)
    code = serializers.CharField()
    label = serializers.CharField()
    state = serializers.CharField()


class AttemptSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    po_line = serializers.UUIDField()
    replaces = serializers.UUIDField(allow_null=True)
    strategy = serializers.CharField()
    status = serializers.CharField()
    quantities = serializers.DictField(child=serializers.CharField())


class TrackerSerializer(serializers.Serializer):
    item = serializers.UUIDField()
    requested_quantity = serializers.CharField()
    ordered_quantity = serializers.CharField()
    accepted_quantity = serializers.CharField()
    closed_rejected_quantity = serializers.CharField()
    remaining_quantity = serializers.CharField()
    milestones = MilestoneSerializer(many=True)
    attempts = AttemptSummarySerializer(many=True)
    quotation_validity = serializers.ListField(child=serializers.DictField())
    events = READ[m.ItemTrackingEvent](many=True)
    event_count = serializers.IntegerField()
    history_url = serializers.CharField()
    previous_item = serializers.UUIDField(allow_null=True)
    reorders = serializers.ListField(child=serializers.UUIDField())
    finance = FinanceCapabilitySerializer()


class AvailableActionSerializer(serializers.Serializer):
    action = serializers.CharField()
    allowed = serializers.BooleanField()
    reason = serializers.CharField()


class QuotationLinePricingSerializer(READ[m.ClientQuotationLine]):
    """Expose client price arithmetic; supplier costs remain outside this contract."""

    pricing = serializers.SerializerMethodField()

    class Meta(READ[m.ClientQuotationLine].Meta):
        fields = [
            field
            for field in READ[m.ClientQuotationLine].Meta.fields
            if field != "estimated_unit_cost"
        ] + ["pricing"]

    def get_pricing(self, obj) -> dict:
        from decimal import localcontext

        with localcontext() as context:
            context.prec = 40
            subtotal = obj.quantity * obj.unit_price
            net_goods_value = subtotal - obj.discount
            return {
                "subtotal": str(subtotal),
                "net_goods_value": str(net_goods_value),
                "total": str(
                    net_goods_value + obj.tax + obj.logistics + obj.additional_charges
                ),
            }


READ[m.ClientQuotationLine] = QuotationLinePricingSerializer
READ[m.ClientQuotationRevision]._declared_fields["lines"] = (
    QuotationLinePricingSerializer(many=True, read_only=True)
)


class ClientCodeInput(StrictSerializer):
    code = serializers.RegexField(r"^[A-Z]{3,4}$")


class PrepareDocumentInput(StrictSerializer):
    request = pk(m.ProcurementRequest)
    kind = serializers.ChoiceField(choices=[("QT", "Quotation"), ("WB", "Waybill")])
    quotation_revision = pk(m.ClientQuotationRevision, required=False, allow_null=True)
    delivery = pk(m.Delivery, required=False, allow_null=True)


class GenerateDocumentInput(StrictSerializer):
    show_logistics = serializers.BooleanField(default=False)
    show_additional_charges = serializers.BooleanField(default=False)
    format = serializers.ChoiceField(choices=["PDF", "JPG"])
    draft = serializers.BooleanField(default=True)


class DocumentAssetSerializer(serializers.ModelSerializer):
    filename = serializers.CharField(source="document.filename", read_only=True)

    class Meta:
        model = m.DocumentAsset
        fields = [
            "id",
            "document",
            "format",
            "page",
            "draft",
            "template_version",
            "filename",
            "created_at",
        ]
        read_only_fields = fields


class DocumentVersionSerializer(serializers.ModelSerializer):
    number = serializers.CharField(source="register.number", read_only=True)
    kind = serializers.CharField(source="register.kind", read_only=True)
    approval_status = serializers.SerializerMethodField()
    assets = DocumentAssetSerializer(many=True, read_only=True)

    def get_approval_status(self, obj) -> str:
        from .document_registry import approval_status

        return approval_status(obj)

    class Meta:
        model = m.DocumentVersion
        fields = [
            "id",
            "register",
            "number",
            "kind",
            "revision",
            "quotation_revision",
            "snapshot",
            "approval_status",
            "approval_requested_at",
            "assets",
            "created_at",
            "created_by_name",
        ]
        read_only_fields = fields


class DocumentRegisterSerializer(serializers.ModelSerializer):
    versions = DocumentVersionSerializer(many=True, read_only=True)

    class Meta:
        model = m.DocumentRegister
        fields = [
            "id",
            "request",
            "kind",
            "client_code",
            "year",
            "serial",
            "number",
            "quotation",
            "delivery",
            "versions",
            "created_at",
            "created_by_name",
        ]
        read_only_fields = fields


READ[m.DocumentRegister] = DocumentRegisterSerializer
READ[m.DocumentVersion] = DocumentVersionSerializer
READ[m.DocumentAsset] = DocumentAssetSerializer


class RequestWithCodeSerializer(READ[m.ProcurementRequest]):
    client_code = serializers.SerializerMethodField()

    def get_client_code(self, obj) -> str | None:
        try:
            return obj.client.document_code.code
        except m.ClientDocumentCode.DoesNotExist:
            return None

    class Meta(READ[m.ProcurementRequest].Meta):
        fields = READ[m.ProcurementRequest].Meta.fields + ["client_code"]


READ[m.ProcurementRequest] = RequestWithCodeSerializer

# Keep the request context when preparing a document from a quotation revision.
READ[m.ClientQuotationRevision]._declared_fields["request"] = serializers.UUIDField(
    source="quotation.request_id", read_only=True
)
READ[m.ClientQuotationRevision].Meta.fields = [
    *READ[m.ClientQuotationRevision].Meta.fields,
    "request",
]

# Human-readable operational references for selectors and item navigation.
for model, source in {
    m.SupplierQuotationLine: "item.description",
    m.ClientPurchaseOrderLine: "item_description",
    m.SupplierPurchaseLine: "attempt.item.description",
    m.GoodsReceiptLine: "purchase_line.attempt.item.description",
    m.DeliveryLine: "receipt_line.purchase_line.attempt.item.description",
    m.DeliveryOutcomeLine: "delivery_line.receipt_line.purchase_line.attempt.item.description",
    m.FulfilmentAttempt: "item.description",
}.items():
    READ[model]._declared_fields["description"] = serializers.CharField(
        source=source, read_only=True
    )
    READ[model].Meta.fields = [*READ[model].Meta.fields, "description"]
for name, source in {
    "item": "delivery_line.receipt_line.purchase_line.attempt.item_id",
    "request": "outcome.delivery.request_id",
}.items():
    READ[m.DeliveryOutcomeLine]._declared_fields[name] = serializers.UUIDField(
        source=source, read_only=True
    )
    READ[m.DeliveryOutcomeLine].Meta.fields = [
        *READ[m.DeliveryOutcomeLine].Meta.fields,
        name,
    ]


class TrackingEventDetailsSerializer(READ[m.ItemTrackingEvent]):
    details = serializers.SerializerMethodField()

    def get_details(self, obj) -> dict:
        details = {"item": obj.item.description, "unit": obj.item.unit}
        if obj.supplier_offer_id:
            details["supplier"] = obj.supplier_offer.quotation.supplier_name
            details["supplier_reference"] = obj.supplier_offer.quotation.reference
        if obj.quote_revision_id:
            details["quotation"] = obj.quote_revision.quotation.number
            details["revision"] = obj.quote_revision.number
        if obj.po_line_id:
            details["purchase_order"] = obj.po_line.order.reference
        if obj.purchase_line_id:
            details["supplier"] = obj.purchase_line.purchase.supplier_name
            details["purchase_reference"] = obj.purchase_line.purchase.reference
        if obj.delivery_line_id:
            delivery = obj.delivery_line.delivery
            details.update(
                delivery=delivery.number,
                destination=delivery.destination,
                recipient=delivery.recipient_name,
                logistics=delivery.logistics_details,
            )
        if obj.outcome_line_id:
            details["received_by"] = obj.outcome_line.outcome.receiver_name
        if obj.document_id:
            details["document"] = obj.document.filename
        return details

    class Meta(READ[m.ItemTrackingEvent].Meta):
        fields = [*READ[m.ItemTrackingEvent].Meta.fields, "details"]
        read_only_fields = fields


READ[m.ItemTrackingEvent] = TrackingEventDetailsSerializer
TrackerSerializer._declared_fields["events"] = TrackingEventDetailsSerializer(many=True)


def human_display_label(obj) -> str:
    short = str(obj.pk).replace("-", "")[:6].upper()
    if isinstance(obj, m.ProcurementRequest):
        return f"{obj.client_name} · Request #{short}"
    if isinstance(obj, m.ClientQuotation):
        return f"Quotation for {obj.request.client_name} · #{short}"
    if isinstance(obj, m.ClientQuotationRevision):
        return f"{human_display_label(obj.quotation)} · Revision {obj.number}"
    for field in (
        "description",
        "supplier_name",
        "recipient_name",
        "receiver_name",
        "filename",
        "reference",
        "number",
        "reason",
    ):
        value = getattr(obj, field, None)
        if value:
            if (
                field == "number"
                and len(str(value)) > 12
                and not str(value).startswith("Hol-")
            ):
                continue
            return str(value)
    return f"{obj._meta.verbose_name.title()} #{short}"


def get_display_label(self, obj) -> str:
    return human_display_label(obj)


def get_display_reference(self, obj) -> str:
    return str(obj.pk).replace("-", "")[:6].upper()


def get_related_labels(self, obj) -> dict:
    result = {}
    for field in obj._meta.fields:
        if field.is_relation and field.name in obj._state.fields_cache:
            related = obj._state.fields_cache[field.name]
            if related is not None:
                result[field.name] = (
                    human_display_label(related)
                    if related._meta.app_label == "procurement"
                    else str(related)
                )
    return result


for serializer_class in set(READ.values()):
    serializer_class._declared_fields["display_label"] = (
        serializers.SerializerMethodField(method_name="get_display_label")
    )
    serializer_class._declared_fields["display_reference"] = (
        serializers.SerializerMethodField(method_name="get_display_reference")
    )
    serializer_class._declared_fields["related_labels"] = (
        serializers.SerializerMethodField(method_name="get_related_labels")
    )
    serializer_class.get_display_label = get_display_label
    serializer_class.get_display_reference = get_display_reference
    serializer_class.get_related_labels = get_related_labels
    serializer_class.Meta.fields = [
        *serializer_class.Meta.fields,
        "display_label",
        "display_reference",
        "related_labels",
    ]
    serializer_class.Meta.read_only_fields = serializer_class.Meta.fields
