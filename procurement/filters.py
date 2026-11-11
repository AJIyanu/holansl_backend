import django_filters
from django.db.models import Q

from .models import ItemRequest, RequestedItem


class ItemRequestFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")
    client = django_filters.UUIDFilter()
    received_via = django_filters.CharFilter()
    registered_from = django_filters.DateFilter(
        field_name="received_at__date", lookup_expr="gte"
    )
    registered_to = django_filters.DateFilter(
        field_name="received_at__date", lookup_expr="lte"
    )

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(client_name__icontains=value)
            | Q(requester_name__icontains=value)
            | Q(items__name__icontains=value)
            | Q(items__code__icontains=value)
        ).distinct()

    class Meta:
        model = ItemRequest
        fields = ["client", "received_via"]


class RequestedItemFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")
    client = django_filters.UUIDFilter(field_name="request__client_id")
    requester = django_filters.UUIDFilter(field_name="request__requester_id")
    received_via = django_filters.CharFilter(field_name="request__received_via")
    urgency = django_filters.CharFilter()
    status = django_filters.CharFilter()
    deadline_from = django_filters.DateFilter(field_name="deadline", lookup_expr="gte")
    deadline_to = django_filters.DateFilter(field_name="deadline", lookup_expr="lte")
    registered_from = django_filters.DateFilter(
        field_name="created_at__date", lookup_expr="gte"
    )
    registered_to = django_filters.DateFilter(
        field_name="created_at__date", lookup_expr="lte"
    )
    created_by = django_filters.UUIDFilter(field_name="created_by_id")
    has_images = django_filters.BooleanFilter(method="filter_images")
    completed = django_filters.BooleanFilter(method="filter_completed")
    overdue = django_filters.BooleanFilter(method="filter_overdue")

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value)
            | Q(code__icontains=value)
            | Q(description__icontains=value)
            | Q(brand__icontains=value)
            | Q(request__client_name__icontains=value)
            | Q(request__requester_name__icontains=value)
        )

    def filter_images(self, queryset, name, value):
        return queryset.filter(images__isnull=not value).distinct()

    def filter_completed(self, queryset, name, value):
        statuses = ["DELIVERED", "CLOSED"]
        return (
            queryset.filter(status__in=statuses)
            if value
            else queryset.exclude(status__in=statuses)
        )

    def filter_overdue(self, queryset, name, value):
        from django.utils import timezone

        lookup = Q(deadline__lt=timezone.localdate()) & ~Q(
            status__in=["DELIVERED", "CLOSED"]
        )
        return queryset.filter(lookup) if value else queryset.exclude(lookup)

    class Meta:
        model = RequestedItem
        fields = ["client", "requester", "received_via", "urgency", "status"]
