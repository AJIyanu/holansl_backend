from rest_framework import serializers
from .models import ClientRequest, SupplierQuote, PurchaseOrder, POTracker


class SupplierQuoteSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(
        source="supplier.name", read_only=True)
    client_request_details = serializers.SerializerMethodField()

    class Meta:
        model = SupplierQuote
        fields = [
            "id", "supplier", "supplier_name", "client_request",
            "price", "import_type", "currency", "quoted_price",
            "lead_time_days", "comments", "created_at",
            "client_request_details",
        ]

    def get_client_request_details(self, obj):
        if obj.client_request:
            return {
                "item_name": obj.client_request.item_name,
                "specification": obj.client_request.specification,
                "model": obj.client_request.model,
                "brand": obj.client_request.brand,
                "quantity": obj.client_request.quantity,
                "uom": obj.client_request.uom,
                "status": obj.client_request.status,
            }
        return None


class ClientRequestSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    supplier_quotes = SupplierQuoteSerializer(
        source="supplierquote_set", many=True, read_only=True)
    contact_person_name = serializers.SerializerMethodField()

    def get_contact_person_name(self, obj):
        if obj.contact_person:
            first = obj.contact_person.first_name or ''
            last = obj.contact_person.last_name or ''
            return f'{first} {last}'.strip()
        return ''

    class Meta:
        model = ClientRequest
        fields = "__all__"


class POTrackerSerializer(serializers.ModelSerializer):
    class Meta:
        model = POTracker
        fields = [
            "id",
            "status",
            "description",
            "updated_at",
            "purchase_order"]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    supplier_name = serializers.CharField(
        source="supplier_quote.supplier.name", read_only=True)
    item_name = serializers.CharField(
        source="supplier_quote.client_request.item_name",
        read_only=True)
    item_brand = serializers.CharField(
        source="supplier_quote.client_request.brand",
        read_only=True)
    request_id = serializers.CharField(
        source="supplier_quote.client_request.id",
        read_only=True)
    trackers = POTrackerSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = "__all__"
