from django.contrib import admin

from .models import (
    Notification,
    NotificationDelivery,
    NotificationPreference,
    NotificationRecipient,
    NotificationTemplate,
)


class NotificationRecipientInline(admin.TabularInline):
    model = NotificationRecipient
    extra = 0

    fields = [
        "recipient",
        "action_url",
        "seen_at",
        "read_at",
        "archived_at",
        "created_at",
    ]

    readonly_fields = ["created_at"]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "notification_type",
        "category",
        "severity",
        "actor",
        "is_mandatory",
        "scheduled_at",
        "created_at",
    ]

    list_filter = [
        "category",
        "severity",
        "is_mandatory",
        "created_at",
    ]

    search_fields = [
        "title",
        "message",
        "notification_type",
        "deduplication_key",
    ]

    readonly_fields = [
        "created_at",
        "updated_at",
    ]

    inlines = [NotificationRecipientInline]


@admin.register(NotificationRecipient)
class NotificationRecipientAdmin(admin.ModelAdmin):
    list_display = [
        "notification",
        "recipient",
        "seen_at",
        "read_at",
        "archived_at",
        "created_at",
    ]

    list_filter = [
        "seen_at",
        "read_at",
        "archived_at",
        "created_at",
    ]

    search_fields = [
        "notification__title",
        "recipient__username",
        "recipient__email",
    ]

    readonly_fields = ["created_at"]


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = [
        "notification_recipient",
        "channel",
        "status",
        "destination",
        "provider",
        "attempt_count",
        "next_attempt_at",
        "created_at",
    ]

    list_filter = [
        "channel",
        "status",
        "provider",
        "created_at",
    ]

    search_fields = [
        "notification_recipient__notification__title",
        "notification_recipient__recipient__username",
        "destination",
        "provider_message_id",
        "error_message",
    ]

    readonly_fields = [
        "attempt_count",
        "last_attempt_at",
        "sent_at",
        "delivered_at",
        "read_at",
        "failed_at",
        "created_at",
        "updated_at",
    ]


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "category",
        "notification_type",
        "dashboard_enabled",
        "email_enabled",
        "whatsapp_enabled",
    ]

    list_filter = [
        "dashboard_enabled",
        "email_enabled",
        "whatsapp_enabled",
    ]

    search_fields = [
        "user__username",
        "user__email",
        "category",
        "notification_type",
    ]


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = [
        "key",
        "channel",
        "language",
        "version",
        "is_active",
    ]

    list_filter = [
        "channel",
        "language",
        "is_active",
    ]

    search_fields = [
        "key",
        "name",
        "subject_template",
        "title_template",
    ]
