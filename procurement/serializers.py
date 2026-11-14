from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import serializers

from crm.models import Party
from . import models as m


class ItemInput(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField()
    specification = serializers.CharField(required=False, allow_blank=True)
    brand = serializers.CharField(max_length=255, required=False, allow_blank=True)
    quantity = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001")
    )
    unit = serializers.CharField(max_length=80)
    urgency = serializers.ChoiceField(
        choices=m.RequestedItem.Urgency.choices, default="NORMAL"
    )
    deadline = serializers.DateField(required=False, allow_null=True)


class ItemRequestInput(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Party.objects.all())
    received_via = serializers.ChoiceField(
        choices=m.ItemRequest.ReceivedVia.choices, required=False, allow_blank=True
    )
    received_via_custom = serializers.CharField(
        max_length=100, required=False, allow_blank=True
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    received_at = serializers.DateTimeField(required=False)
    items = ItemInput(many=True, allow_empty=False)

    def validate(self, attrs):
        if (
            attrs.get("received_via") == "CUSTOM"
            and not attrs.get("received_via_custom", "").strip()
        ):
            raise serializers.ValidationError(
                {"received_via_custom": "Enter the custom request channel."}
            )
        return attrs


class ItemImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.RequestedItemImage
        fields = [
            "id",
            "filename",
            "mime_type",
            "size_bytes",
            "position",
            "caption",
            "created_at",
        ]


class ActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = m.ItemActivity
        fields = [
            "id",
            "category",
            "title_code",
            "title",
            "description",
            "occurred_at",
            "status_result",
            "source_type",
            "source_id",
            "metadata",
            "created_by_name",
            "created_at",
        ]


class RequestedItemSerializer(serializers.ModelSerializer):
    client = serializers.UUIDField(source="request.client_id", read_only=True)
    client_name = serializers.CharField(source="request.client_name", read_only=True)
    received_via = serializers.CharField(source="request.received_via", read_only=True)
    received_via_custom = serializers.CharField(
        source="request.received_via_custom", read_only=True
    )
    received_at = serializers.DateTimeField(
        source="request.received_at", read_only=True
    )
    images = ItemImageSerializer(many=True, read_only=True)
    latest_activity = serializers.SerializerMethodField()
    activity_count = serializers.IntegerField(read_only=True)
    latest_quotation_price = serializers.DecimalField(
        max_digits=18, decimal_places=2, read_only=True, allow_null=True
    )

    def get_latest_activity(self, obj):
        row = next(iter(obj.activities.all()), None)
        return ActivitySerializer(row).data if row else None

    class Meta:
        model = m.RequestedItem
        fields = [
            "id",
            "request",
            "code",
            "name",
            "description",
            "specification",
            "brand",
            "quantity",
            "unit",
            "urgency",
            "deadline",
            "status",
            "completed_at",
            "client",
            "client_name",
            "received_via",
            "received_via_custom",
            "received_at",
            "images",
            "latest_activity",
            "activity_count",
            "latest_quotation_price",
            "created_by_name",
            "created_at",
            "updated_at",
        ]


class ItemRequestSerializer(serializers.ModelSerializer):
    items = RequestedItemSerializer(many=True, read_only=True)

    class Meta:
        model = m.ItemRequest
        fields = [
            "id",
            "client",
            "client_name",
            "received_via",
            "received_via_custom",
            "notes",
            "received_at",
            "created_by_name",
            "created_at",
            "items",
        ]


class ActivityInput(serializers.Serializer):
    category = serializers.ChoiceField(
        choices=[("ACTIVITY", "Activity"), ("COMMENT", "Comment")]
    )
    title_code = serializers.CharField(max_length=50)
    custom_title = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    description = serializers.CharField(required=False, allow_blank=True)
    occurred_at = serializers.DateTimeField(required=False)


class StatusInput(serializers.Serializer):
    status = serializers.ChoiceField(choices=m.RequestedItem.Status.choices)
    reason = serializers.CharField()
    occurred_at = serializers.DateTimeField(required=False)


class ItemEditInput(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False)
    specification = serializers.CharField(required=False, allow_blank=True)
    brand = serializers.CharField(max_length=255, required=False, allow_blank=True)
    quantity = serializers.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        required=False,
    )
    unit = serializers.CharField(max_length=80, required=False)
    urgency = serializers.ChoiceField(
        choices=m.RequestedItem.Urgency.choices, required=False
    )
    deadline = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Submit at least one item field.")
        return attrs


class ImagesInput(serializers.Serializer):
    files = serializers.ListField(
        child=serializers.ImageField(), min_length=1, max_length=5
    )
    caption = serializers.CharField(max_length=255, required=False, allow_blank=True)


class ImageDeleteInput(serializers.Serializer):
    images = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=5
    )

    def validate_images(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Choose each picture only once.")
        return value


class SupplierQuoteItemInput(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(queryset=m.RequestedItem.objects.all())
    quantity = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001")
    )
    unit_price = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0")
    )
    tax_enabled = serializers.BooleanField(default=False)
    tax_rate = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        default=Decimal("7.5"),
    )
    note = serializers.CharField(required=False, allow_blank=True)


class SupplierQuoteInput(serializers.Serializer):
    supplier = serializers.PrimaryKeyRelatedField(queryset=Party.objects.all())
    supplier_reference = serializers.CharField(
        max_length=100, required=False, allow_blank=True
    )
    quotation_date = serializers.DateField(required=False)
    expires_on = serializers.DateField(required=False, allow_null=True)
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"], default="NGN")
    received_via = serializers.ChoiceField(choices=m.SupplierQuote.ReceivedVia.choices)
    received_via_custom = serializers.CharField(
        max_length=100, required=False, allow_blank=True
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    items = SupplierQuoteItemInput(many=True, allow_empty=False)

    def validate(self, attrs):
        if (
            attrs["received_via"] == "CUSTOM"
            and not attrs.get("received_via_custom", "").strip()
        ):
            raise serializers.ValidationError(
                {
                    "received_via_custom": "Describe where the supplier quotation was received."
                }
            )
        ids = [row["item"].pk for row in attrs["items"]]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError(
                {"items": "Choose each item only once per supplier quotation."}
            )
        if (
            attrs.get("expires_on")
            and attrs.get("quotation_date")
            and attrs["expires_on"] < attrs["quotation_date"]
        ):
            raise serializers.ValidationError(
                {"expires_on": "Expiry cannot be before the quotation date."}
            )
        return attrs


class SupplierQuoteItemSerializer(serializers.ModelSerializer):
    subtotal = serializers.SerializerMethodField()
    tax_amount = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()

    def get_subtotal(self, obj):
        return obj.quantity * obj.unit_price

    def get_tax_amount(self, obj):
        return (
            self.get_subtotal(obj) * obj.tax_rate / 100
            if obj.tax_enabled
            else Decimal("0")
        )

    def get_total(self, obj):
        return self.get_subtotal(obj) + self.get_tax_amount(obj)

    class Meta:
        model = m.SupplierQuoteItem
        fields = [
            "id",
            "item",
            "item_name",
            "quantity",
            "unit",
            "unit_price",
            "tax_enabled",
            "tax_rate",
            "note",
            "subtotal",
            "tax_amount",
            "total",
        ]


class SupplierQuoteSerializer(serializers.ModelSerializer):
    items = SupplierQuoteItemSerializer(many=True, read_only=True)
    has_attachment = serializers.SerializerMethodField()

    def get_has_attachment(self, obj):
        return bool(obj.attachment_external_file_id)

    class Meta:
        model = m.SupplierQuote
        fields = [
            "id",
            "supplier",
            "supplier_name",
            "supplier_reference",
            "quotation_date",
            "expires_on",
            "currency",
            "received_via",
            "received_via_custom",
            "notes",
            "attachment_filename",
            "has_attachment",
            "items",
            "created_by_name",
            "created_at",
            "updated_at",
        ]


class ClientQuotationItemInput(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(queryset=m.RequestedItem.objects.all())
    description = serializers.CharField(max_length=1000)
    quantity = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001")
    )
    unit_price = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0")
    )


class ClientQuotationInput(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Party.objects.all())
    quotation_date = serializers.DateField(required=False)
    expires_on = serializers.DateField()
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"], default="NGN")
    tax_enabled = serializers.BooleanField(default=False)
    tax_rate = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        default=Decimal("7.5"),
    )
    discount_type = serializers.ChoiceField(
        choices=["NONE", "PERCENT", "AMOUNT"], default="NONE"
    )
    discount_value = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0"), default=Decimal("0")
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    terms = serializers.CharField(required=False, allow_blank=True)
    recipient_email = serializers.EmailField(required=False, allow_blank=True)
    items = ClientQuotationItemInput(many=True, allow_empty=False)

    def validate(self, attrs):
        ids = [row["item"].pk for row in attrs["items"]]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError({"items": "Choose each item only once."})
        if any(
            row["item"].status not in {"OPEN", "SUSPENDED"} for row in attrs["items"]
        ):
            raise serializers.ValidationError(
                {"items": "Only open or suspended items can be quoted."}
            )
        if (
            attrs.get("quotation_date")
            and attrs["expires_on"] < attrs["quotation_date"]
        ):
            raise serializers.ValidationError(
                {"expires_on": "Expiry cannot be before the quotation date."}
            )
        if attrs["discount_type"] == "PERCENT" and attrs["discount_value"] > 100:
            raise serializers.ValidationError(
                {"discount_value": "Percentage discount cannot exceed 100%."}
            )
        if attrs["discount_type"] == "NONE":
            attrs["discount_value"] = Decimal("0")
        return attrs


class ClientQuotationItemSerializer(serializers.ModelSerializer):
    amount = serializers.SerializerMethodField()

    def get_amount(self, obj):
        return obj.quantity * obj.unit_price

    class Meta:
        model = m.ClientQuotationItem
        fields = [
            "id",
            "item",
            "item_name",
            "description",
            "quantity",
            "unit",
            "unit_price",
            "amount",
        ]


class CommunicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.ClientQuotationCommunication
        fields = [
            "id",
            "method",
            "destination",
            "note",
            "created_by_name",
            "created_at",
        ]


class ClientQuotationRevisionSerializer(serializers.ModelSerializer):
    document_available = serializers.SerializerMethodField()

    def get_document_available(self, obj):
        return bool(obj.document_external_file_id) and obj.removed_at is None

    class Meta:
        model = m.ClientQuotationRevision
        fields = [
            "id",
            "version",
            "issued_at",
            "issued_by_name",
            "document_filename",
            "document_size_bytes",
            "document_checksum",
            "document_available",
            "removed_at",
            "removed_by_name",
        ]


class ClientQuotationSerializer(serializers.ModelSerializer):
    items = ClientQuotationItemSerializer(many=True, read_only=True)
    communications = CommunicationSerializer(many=True, read_only=True)
    totals = serializers.SerializerMethodField()
    document_available = serializers.SerializerMethodField()
    revisions = ClientQuotationRevisionSerializer(many=True, read_only=True)

    def get_totals(self, obj):
        subtotal = sum(
            (row.quantity * row.unit_price for row in obj.items.all()), Decimal("0")
        )
        if obj.discount_type == "PERCENT":
            discount = subtotal * obj.discount_value / 100
        elif obj.discount_type == "AMOUNT":
            discount = obj.discount_value
        else:
            discount = Decimal("0")
        net = max(Decimal("0"), subtotal - discount)
        tax = net * obj.tax_rate / 100 if obj.tax_enabled else Decimal("0")
        return {
            "subtotal": subtotal,
            "discount": discount,
            "net": net,
            "tax": tax,
            "total": net + tax,
        }

    def get_document_available(self, obj):
        return bool(obj.document_external_file_id)

    class Meta:
        model = m.ClientQuotation
        fields = [
            "id",
            "client",
            "client_name",
            "reference",
            "version",
            "quotation_date",
            "expires_on",
            "currency",
            "tax_enabled",
            "tax_rate",
            "discount_type",
            "discount_value",
            "notes",
            "terms",
            "recipient_email",
            "status",
            "issued_at",
            "document_available",
            "items",
            "communications",
            "revisions",
            "totals",
            "created_by_name",
            "created_at",
            "updated_at",
        ]


class CommunicationInput(serializers.Serializer):
    method = serializers.ChoiceField(
        choices=m.ClientQuotationCommunication.Method.choices
    )
    destination = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    note = serializers.CharField(required=False, allow_blank=True)


class QuotationEmailInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    destination = serializers.EmailField()
    subject = serializers.CharField(max_length=250, required=False, allow_blank=True)
    message = serializers.CharField(max_length=10000, required=False, allow_blank=True)

    def validate_subject(self, value):
        if "\r" in value or "\n" in value:
            raise serializers.ValidationError("Email subject must be one line.")
        return value


class PurchaseOrderItemInput(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(queryset=m.RequestedItem.objects.all())
    quantity = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001")
    )
    unit_price = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        allow_null=True,
    )


class PurchaseOrderInput(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Party.objects.all())
    client_reference = serializers.CharField(max_length=120)
    document_date = serializers.DateField(required=False, allow_null=True)
    received_at = serializers.DateTimeField(required=False)
    received_via = serializers.CharField(
        max_length=30, required=False, allow_blank=True
    )
    urgency = serializers.ChoiceField(
        choices=m.RequestedItem.Urgency.choices, default="NORMAL"
    )
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"], default="NGN")
    notes = serializers.CharField(required=False, allow_blank=True)
    items = PurchaseOrderItemInput(many=True, allow_empty=False)

    def validate_items(self, rows):
        ids = [row["item"].pk for row in rows]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Choose each tracked item only once.")
        return rows


class PurchaseOrderStatusInput(serializers.Serializer):
    status = serializers.ChoiceField(choices=m.PurchaseOrder.Status.choices)
    reason = serializers.CharField(required=False, allow_blank=True)


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.PurchaseOrderItem
        fields = ["id", "item", "item_name", "quantity", "unit", "unit_price"]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseOrderItemSerializer(many=True, read_only=True)
    has_attachment = serializers.SerializerMethodField()

    def get_has_attachment(self, obj):
        return bool(obj.attachment_external_file_id)

    class Meta:
        model = m.PurchaseOrder
        fields = [
            "id",
            "reference",
            "client",
            "client_name",
            "client_reference",
            "document_date",
            "received_at",
            "received_via",
            "urgency",
            "status",
            "status_reason",
            "currency",
            "notes",
            "attachment_filename",
            "has_attachment",
            "items",
            "created_by_name",
            "created_at",
            "updated_at",
        ]


class PurchaseItemInput(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(queryset=m.RequestedItem.objects.all())
    quantity = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001")
    )
    unit_price = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0")
    )


class PurchaseInput(serializers.Serializer):
    purchased_from = serializers.PrimaryKeyRelatedField(queryset=Party.objects.all())
    purchase_order = serializers.PrimaryKeyRelatedField(
        queryset=m.PurchaseOrder.objects.exclude(
            status=m.PurchaseOrder.Status.FULFILLED
        ),
        required=False,
        allow_null=True,
    )
    purchased_at = serializers.DateTimeField(required=False)
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"], default="NGN")
    supplier_document_reference = serializers.CharField(
        max_length=120, required=False, allow_blank=True
    )
    logistics_method = serializers.ChoiceField(
        choices=m.Purchase.LogisticsMethod.choices, required=False, allow_blank=True
    )
    logistics_method_custom = serializers.CharField(
        max_length=120, required=False, allow_blank=True
    )
    logistics_cost = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        allow_null=True,
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    items = PurchaseItemInput(many=True, allow_empty=False)

    def validate(self, attrs):
        if (
            attrs.get("logistics_method") == "CUSTOM"
            and not attrs.get("logistics_method_custom", "").strip()
        ):
            raise serializers.ValidationError(
                {"logistics_method_custom": "Describe the custom logistics method."}
            )
        ids = [row["item"].pk for row in attrs["items"]]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError(
                {"items": "Choose each tracked item only once."}
            )
        return attrs


class PurchaseItemSerializer(serializers.ModelSerializer):
    total = serializers.SerializerMethodField()

    def get_total(self, obj):
        return obj.quantity * obj.unit_price

    class Meta:
        model = m.PurchaseItem
        fields = [
            "id",
            "item",
            "item_name",
            "quantity",
            "unit",
            "unit_price",
            "total",
            "status",
            "status_reason",
            "status_changed_at",
        ]


class PurchaseItemStatusInput(serializers.Serializer):
    status = serializers.ChoiceField(choices=m.PurchaseItem.Status.choices)
    reason = serializers.CharField(required=False, allow_blank=True)


class PurchaseSerializer(serializers.ModelSerializer):
    items = PurchaseItemSerializer(many=True, read_only=True)
    purchase_order_reference = serializers.CharField(
        source="purchase_order.reference", read_only=True
    )

    class Meta:
        model = m.Purchase
        fields = [
            "id",
            "purchased_from",
            "purchased_from_name",
            "purchase_order",
            "purchase_order_reference",
            "purchased_at",
            "currency",
            "supplier_document_reference",
            "logistics_method",
            "logistics_method_custom",
            "logistics_cost",
            "notes",
            "items",
            "created_by_name",
            "created_at",
        ]


class WaybillItemInput(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(queryset=m.RequestedItem.objects.all())
    description = serializers.CharField(
        max_length=1000, required=False, allow_blank=True
    )
    quantity = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001")
    )


class WaybillInput(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Party.objects.all())
    purchase_order = serializers.PrimaryKeyRelatedField(
        queryset=m.PurchaseOrder.objects.exclude(
            status=m.PurchaseOrder.Status.FULFILLED
        ),
        required=False,
        allow_null=True,
    )
    waybill_date = serializers.DateField(required=False)
    dispatch_at = serializers.DateTimeField(required=False, allow_null=True)
    delivery_personnel = serializers.CharField(max_length=255)
    delivered_to = serializers.CharField(max_length=255)
    delivery_address = serializers.CharField()
    logistics_method = serializers.CharField(
        max_length=120, required=False, allow_blank=True
    )
    driver_name = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    vehicle_details = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    signatory_name = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    received_at = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    items = WaybillItemInput(many=True, allow_empty=False)

    def validate_items(self, rows):
        ids = [row["item"].pk for row in rows]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Choose each tracked item only once.")
        return rows


class WaybillItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.WaybillItem
        fields = ["id", "item", "item_name", "description", "quantity", "unit"]


class WaybillSerializer(serializers.ModelSerializer):
    items = WaybillItemSerializer(many=True, read_only=True)
    document_available = serializers.SerializerMethodField()
    purchase_order_status = serializers.CharField(
        source="purchase_order.status", read_only=True, allow_null=True
    )

    def get_document_available(self, obj):
        return bool(obj.document_external_file_id)

    class Meta:
        model = m.Waybill
        fields = [
            "id",
            "reference",
            "client",
            "client_name",
            "purchase_order",
            "purchase_order_status",
            "waybill_date",
            "dispatch_at",
            "delivery_personnel",
            "delivered_to",
            "delivery_address",
            "logistics_method",
            "driver_name",
            "vehicle_details",
            "signatory_name",
            "received_at",
            "notes",
            "document_available",
            "items",
            "created_by_name",
            "created_at",
            "updated_at",
        ]


class WaybillEmailInput(QuotationEmailInput):
    pass


class ItemVerdictInput(serializers.Serializer):
    waybill_item = serializers.PrimaryKeyRelatedField(
        queryset=m.WaybillItem.objects.select_related("item", "waybill").exclude(
            waybill__purchase_order__status=m.PurchaseOrder.Status.FULFILLED
        )
    )
    verdict = serializers.ChoiceField(choices=m.ItemVerdict.Verdict.choices)
    quantity = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001")
    )
    occurred_at = serializers.DateTimeField(required=False)
    client_recipient = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    reason = serializers.CharField(required=False, allow_blank=True)
    supplier = serializers.PrimaryKeyRelatedField(
        queryset=Party.objects.all(), required=False, allow_null=True
    )
    disposal_method = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    authorised_by = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.all(), required=False, allow_null=True
    )

    def validate(self, attrs):
        verdict = attrs["verdict"]
        if (
            verdict in {"REJECTED", "RETURNED", "DISPOSED"}
            and not attrs.get("reason", "").strip()
        ):
            raise serializers.ValidationError(
                {"reason": "A reason is required for this verdict."}
            )
        if verdict == "RETURNED" and not attrs.get("supplier"):
            raise serializers.ValidationError(
                {"supplier": "Choose the supplier receiving the return."}
            )
        if verdict == "DISPOSED" and (
            not attrs.get("disposal_method", "").strip()
            or not attrs.get("authorised_by")
        ):
            raise serializers.ValidationError(
                {"disposal_method": "Enter the disposal method and authoriser."}
            )
        return attrs


class ItemVerdictSerializer(serializers.ModelSerializer):
    waybill_reference = serializers.CharField(
        source="waybill_item.waybill.reference", read_only=True
    )
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = m.ItemVerdict
        fields = [
            "id",
            "waybill_item",
            "waybill_reference",
            "item",
            "item_name",
            "verdict",
            "quantity",
            "unit",
            "occurred_at",
            "client_recipient",
            "reason",
            "supplier",
            "supplier_name",
            "disposal_method",
            "authorised_by",
            "authorised_by_name",
            "created_by_name",
            "created_at",
        ]
