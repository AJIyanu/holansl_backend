from decimal import Decimal

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
    requester = serializers.PrimaryKeyRelatedField(
        queryset=Party.objects.all(), required=False, allow_null=True
    )
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
    client_name = serializers.CharField(source="request.client_name", read_only=True)
    requester_name = serializers.CharField(
        source="request.requester_name", read_only=True
    )
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
            "client_name",
            "requester_name",
            "received_via",
            "received_via_custom",
            "received_at",
            "images",
            "latest_activity",
            "activity_count",
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
            "requester",
            "requester_name",
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


class ImageInput(serializers.Serializer):
    file = serializers.ImageField()
    caption = serializers.CharField(max_length=255, required=False, allow_blank=True)
