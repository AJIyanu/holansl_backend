from django.contrib.auth import get_user_model
from django.utils import timezone

from rest_framework import serializers

from .constants import (
    NotificationChannel,
    NotificationEventMode,
    NotificationSeverity,
)
from .data import RecipientSpec
from .models import (
    NotificationDelivery,
    NotificationPreference,
    NotificationRecipient,
    NotificationTemplate,
)
from .services import notify


User = get_user_model()


class NotificationActorSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = User

        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "display_name",
        ]

    def get_display_name(self, obj):
        return obj.get_full_name() or obj.username


class NotificationInboxSerializer(serializers.ModelSerializer):
    notification_id = serializers.UUIDField(
        source="notification.id",
        read_only=True,
    )

    notification_type = serializers.CharField(
        source="notification.notification_type",
        read_only=True,
    )

    category = serializers.CharField(
        source="notification.category",
        read_only=True,
    )

    severity = serializers.CharField(
        source="notification.severity",
        read_only=True,
    )

    title = serializers.CharField(
        source="notification.title",
        read_only=True,
    )

    message = serializers.CharField(
        source="notification.message",
        read_only=True,
    )

    actor = NotificationActorSerializer(
        source="notification.actor",
        read_only=True,
    )

    notification_metadata = serializers.JSONField(
        source="notification.metadata",
        read_only=True,
    )

    is_mandatory = serializers.BooleanField(
        source="notification.is_mandatory",
        read_only=True,
    )

    scheduled_at = serializers.DateTimeField(
        source="notification.scheduled_at",
        read_only=True,
    )

    expires_at = serializers.DateTimeField(
        source="notification.expires_at",
        read_only=True,
    )

    source = serializers.SerializerMethodField()
    is_seen = serializers.SerializerMethodField()
    is_read = serializers.SerializerMethodField()
    is_archived = serializers.SerializerMethodField()
    channels = serializers.SerializerMethodField()

    class Meta:
        model = NotificationRecipient

        fields = [
            "id",
            "notification_id",
            "notification_type",
            "category",
            "severity",
            "title",
            "message",
            "actor",
            "action_url",
            "action_label",
            "notification_metadata",
            "metadata",
            "is_mandatory",
            "source",
            "channels",
            "is_seen",
            "is_read",
            "is_archived",
            "seen_at",
            "read_at",
            "archived_at",
            "dismissed_at",
            "scheduled_at",
            "expires_at",
            "created_at",
        ]

    def get_source(self, obj):
        notification = obj.notification

        if not notification.source_content_type_id or not notification.source_object_id:
            return None

        return {
            "app_label": (notification.source_content_type.app_label),
            "model": (notification.source_content_type.model),
            "object_id": notification.source_object_id,
        }

    def get_is_seen(self, obj):
        return obj.seen_at is not None

    def get_is_read(self, obj):
        return obj.read_at is not None

    def get_is_archived(self, obj):
        return obj.archived_at is not None

    def get_channels(self, obj):
        return [
            {
                "channel": delivery.channel,
                "status": delivery.status,
            }
            for delivery in obj.deliveries.all()
        ]


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference

        fields = [
            "id",
            "category",
            "notification_type",
            "dashboard_enabled",
            "email_enabled",
            "whatsapp_enabled",
            "quiet_hours_start",
            "quiet_hours_end",
            "timezone_name",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        start = attrs.get(
            "quiet_hours_start",
            getattr(
                self.instance,
                "quiet_hours_start",
                None,
            ),
        )

        end = attrs.get(
            "quiet_hours_end",
            getattr(
                self.instance,
                "quiet_hours_end",
                None,
            ),
        )

        if bool(start) != bool(end):
            raise serializers.ValidationError(
                "Both quiet_hours_start and quiet_hours_end are required."
            )

        request = self.context.get("request")

        if request and request.user.is_authenticated:
            category = attrs.get(
                "category",
                getattr(
                    self.instance,
                    "category",
                    "",
                ),
            )

            notification_type = attrs.get(
                "notification_type",
                getattr(
                    self.instance,
                    "notification_type",
                    "",
                ),
            )

            duplicate = NotificationPreference.objects.filter(
                user=request.user,
                category=category,
                notification_type=notification_type,
            )

            if self.instance:
                duplicate = duplicate.exclude(pk=self.instance.pk)

            if duplicate.exists():
                raise serializers.ValidationError(
                    "A preference already exists for this notification scope."
                )

        return attrs


class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = "__all__"

        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        instance = self.instance

        channel = attrs.get(
            "channel",
            getattr(
                instance,
                "channel",
                None,
            ),
        )

        subject = attrs.get(
            "subject_template",
            getattr(
                instance,
                "subject_template",
                "",
            ),
        )

        title = attrs.get(
            "title_template",
            getattr(
                instance,
                "title_template",
                "",
            ),
        )

        body_text = attrs.get(
            "body_text_template",
            getattr(
                instance,
                "body_text_template",
                "",
            ),
        )

        body_html = attrs.get(
            "body_html_template",
            getattr(
                instance,
                "body_html_template",
                "",
            ),
        )

        if channel == NotificationChannel.EMAIL and not subject:
            raise serializers.ValidationError(
                {"subject_template": "Email templates require a subject."}
            )

        if channel == NotificationChannel.DASHBOARD and not title:
            raise serializers.ValidationError(
                {"title_template": "Dashboard templates require a title."}
            )

        if not body_text and not body_html:
            raise serializers.ValidationError("At least one body template is required.")

        return attrs


class NotificationDeliveryAdminSerializer(serializers.ModelSerializer):
    notification_id = serializers.UUIDField(
        source=("notification_recipient.notification_id"),
        read_only=True,
    )

    notification_type = serializers.CharField(
        source=("notification_recipient.notification.notification_type"),
        read_only=True,
    )

    recipient_id = serializers.UUIDField(
        source=("notification_recipient.recipient_id"),
        read_only=True,
    )

    recipient_name = serializers.SerializerMethodField()

    class Meta:
        model = NotificationDelivery

        fields = [
            "id",
            "notification_id",
            "notification_type",
            "recipient_id",
            "recipient_name",
            "channel",
            "status",
            "destination",
            "provider",
            "provider_message_id",
            "subject",
            "title",
            "attempt_count",
            "max_attempts",
            "scheduled_at",
            "next_attempt_at",
            "last_attempt_at",
            "sent_at",
            "delivered_at",
            "read_at",
            "failed_at",
            "error_code",
            "error_message",
            "response_metadata",
            "created_at",
            "updated_at",
        ]

    def get_recipient_name(self, obj):
        user = obj.notification_recipient.recipient

        return user.get_full_name() or user.username


class NotificationDispatchSerializer(serializers.Serializer):
    recipient_ids = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True),
        many=True,
        allow_empty=False,
    )

    recipient_overrides = serializers.DictField(
        child=serializers.DictField(),
        required=False,
        default=dict,
    )

    notification_type = serializers.CharField(max_length=120)

    category = serializers.SlugField(max_length=60)

    severity = serializers.ChoiceField(
        choices=NotificationSeverity.choices,
        default=NotificationSeverity.INFO,
    )

    title = serializers.CharField(max_length=255)
    message = serializers.CharField()

    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=NotificationChannel.choices),
        allow_empty=False,
        default=[NotificationChannel.DASHBOARD],
    )

    event_mode = serializers.ChoiceField(
        choices=NotificationEventMode.choices,
        default=NotificationEventMode.SHARED,
    )

    template_key = serializers.SlugField(
        required=False,
        allow_blank=True,
        default="",
    )

    language = serializers.CharField(
        required=False,
        max_length=12,
        default="en",
    )

    action_url = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )

    action_label = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )

    metadata = serializers.JSONField(
        required=False,
        default=dict,
    )

    template_context = serializers.JSONField(
        required=False,
        default=dict,
    )

    deduplication_key = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=255,
        default=None,
    )

    is_mandatory = serializers.BooleanField(default=False)

    scheduled_at = serializers.DateTimeField(required=False)

    expires_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
    )

    def validate_recipient_ids(self, users):
        unique_users = []
        seen_ids = set()

        for user in users:
            if user.pk in seen_ids:
                continue

            seen_ids.add(user.pk)
            unique_users.append(user)

        return unique_users

    def validate(self, attrs):
        scheduled_at = attrs.get("scheduled_at") or timezone.now()

        expires_at = attrs.get("expires_at")

        if expires_at and expires_at <= scheduled_at:
            raise serializers.ValidationError(
                {"expires_at": "Must be later than scheduled_at."}
            )

        return attrs

    def create(self, validated_data):
        request = self.context["request"]

        overrides = validated_data.pop(
            "recipient_overrides",
            {},
        )

        recipient_users = validated_data.pop("recipient_ids")

        default_action_url = validated_data.pop(
            "action_url",
            "",
        )

        default_action_label = validated_data.pop(
            "action_label",
            "",
        )

        specs = []

        for user in recipient_users:
            override = overrides.get(
                str(user.pk),
                {},
            )

            specs.append(
                RecipientSpec(
                    user=user,
                    action_url=override.get(
                        "action_url",
                        default_action_url,
                    ),
                    action_label=override.get(
                        "action_label",
                        default_action_label,
                    ),
                    metadata=override.get(
                        "metadata",
                        {},
                    ),
                    template_context=override.get(
                        "template_context",
                        {},
                    ),
                )
            )

        return notify(
            recipients=specs,
            actor=request.user,
            **validated_data,
        )

    def to_representation(self, instance):
        return instance.as_dict()
