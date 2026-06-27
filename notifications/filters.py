import django_filters

from .models import (
    NotificationDelivery,
    NotificationRecipient,
)


class NotificationInboxFilter(django_filters.FilterSet):
    category = django_filters.CharFilter(field_name="notification__category")

    notification_type = django_filters.CharFilter(
        field_name=("notification__notification_type")
    )

    severity = django_filters.CharFilter(field_name="notification__severity")

    read = django_filters.BooleanFilter(method="filter_read")

    seen = django_filters.BooleanFilter(method="filter_seen")

    archived = django_filters.BooleanFilter(method="filter_archived")

    created_after = django_filters.IsoDateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
    )

    created_before = django_filters.IsoDateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
    )

    class Meta:
        model = NotificationRecipient

        fields = [
            "category",
            "notification_type",
            "severity",
            "read",
            "seen",
            "archived",
        ]

    def filter_read(
        self,
        queryset,
        _name,
        value,
    ):
        return queryset.filter(read_at__isnull=not value)

    def filter_seen(
        self,
        queryset,
        _name,
        value,
    ):
        return queryset.filter(seen_at__isnull=not value)

    def filter_archived(
        self,
        queryset,
        _name,
        value,
    ):
        return queryset.filter(archived_at__isnull=not value)


class NotificationDeliveryFilter(django_filters.FilterSet):
    created_after = django_filters.IsoDateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
    )

    created_before = django_filters.IsoDateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
    )

    notification_type = django_filters.CharFilter(
        field_name=("notification_recipient__notification__notification_type")
    )

    category = django_filters.CharFilter(
        field_name=("notification_recipient__notification__category")
    )

    recipient = django_filters.UUIDFilter(
        field_name=("notification_recipient__recipient_id")
    )

    class Meta:
        model = NotificationDelivery

        fields = [
            "channel",
            "status",
            "provider",
            "notification_type",
            "category",
            "recipient",
        ]
