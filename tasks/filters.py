import django_filters

from django.utils import timezone

from rest_framework.filters import OrderingFilter

from .constants import (
    TaskAssignmentType,
    TaskPriority,
)
from .models import (
    ACTIVE_TASK_STATUSES,
    Task,
    TaskBatch,
    TaskReminder,
)


class TaskFilter(django_filters.FilterSet):
    batch = django_filters.UUIDFilter(field_name="batch_id")

    priority = django_filters.ChoiceFilter(
        field_name="batch__priority",
        choices=TaskPriority.choices,
    )

    assignment_type = django_filters.ChoiceFilter(
        field_name="batch__assignment_type",
        choices=TaskAssignmentType.choices,
    )

    assigned_to = django_filters.UUIDFilter(field_name="assigned_to_id")

    created_by = django_filters.UUIDFilter(field_name="batch__created_by_id")

    department = django_filters.UUIDFilter(field_name="assigned_department_id")

    source_department = django_filters.UUIDFilter(
        field_name="batch__source_department_id"
    )

    due_before = django_filters.IsoDateTimeFilter(
        field_name="batch__due_at",
        lookup_expr="lte",
    )

    due_after = django_filters.IsoDateTimeFilter(
        field_name="batch__due_at",
        lookup_expr="gte",
    )

    start_before = django_filters.IsoDateTimeFilter(
        field_name="batch__start_at",
        lookup_expr="lte",
    )

    start_after = django_filters.IsoDateTimeFilter(
        field_name="batch__start_at",
        lookup_expr="gte",
    )

    created_before = django_filters.IsoDateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
    )

    created_after = django_filters.IsoDateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
    )

    updated_before = django_filters.IsoDateTimeFilter(
        field_name="updated_at",
        lookup_expr="lte",
    )

    updated_after = django_filters.IsoDateTimeFilter(
        field_name="updated_at",
        lookup_expr="gte",
    )

    overdue = django_filters.BooleanFilter(method="filter_overdue")

    has_due_date = django_filters.BooleanFilter(method="filter_has_due_date")

    class Meta:
        model = Task

        fields = [
            "batch",
            "status",
            "priority",
            "assignment_type",
            "assigned_to",
            "created_by",
            "department",
            "source_department",
            "due_before",
            "due_after",
            "start_before",
            "start_after",
            "created_before",
            "created_after",
            "updated_before",
            "updated_after",
            "overdue",
            "has_due_date",
        ]

    def filter_overdue(
        self,
        queryset,
        _name,
        value,
    ):
        overdue_query = {
            "batch__due_at__lt": timezone.now(),
            "status__in": ACTIVE_TASK_STATUSES,
            "archived_at__isnull": True,
        }

        if value:
            return queryset.filter(**overdue_query)

        return queryset.exclude(**overdue_query)

    def filter_has_due_date(
        self,
        queryset,
        _name,
        value,
    ):
        return queryset.filter(batch__due_at__isnull=not value)


class TaskBatchFilter(django_filters.FilterSet):
    source_department = django_filters.UUIDFilter(field_name="source_department_id")

    created_by = django_filters.UUIDFilter(field_name="created_by_id")

    due_before = django_filters.IsoDateTimeFilter(
        field_name="due_at",
        lookup_expr="lte",
    )

    due_after = django_filters.IsoDateTimeFilter(
        field_name="due_at",
        lookup_expr="gte",
    )

    start_before = django_filters.IsoDateTimeFilter(
        field_name="start_at",
        lookup_expr="lte",
    )

    start_after = django_filters.IsoDateTimeFilter(
        field_name="start_at",
        lookup_expr="gte",
    )

    created_before = django_filters.IsoDateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
    )

    created_after = django_filters.IsoDateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
    )

    cancelled = django_filters.BooleanFilter(method="filter_cancelled")

    overdue = django_filters.BooleanFilter(method="filter_overdue")

    has_due_date = django_filters.BooleanFilter(method="filter_has_due_date")

    class Meta:
        model = TaskBatch

        fields = [
            "assignment_type",
            "priority",
            "source_department",
            "created_by",
            "due_before",
            "due_after",
            "start_before",
            "start_after",
            "created_before",
            "created_after",
            "cancelled",
            "overdue",
            "has_due_date",
        ]

    def filter_cancelled(
        self,
        queryset,
        _name,
        value,
    ):
        return queryset.filter(cancelled_at__isnull=not value)

    def filter_overdue(
        self,
        queryset,
        _name,
        value,
    ):
        overdue_queryset = queryset.filter(
            due_at__lt=timezone.now(),
            tasks__status__in=ACTIVE_TASK_STATUSES,
            tasks__archived_at__isnull=True,
        ).distinct()

        if value:
            return overdue_queryset

        return queryset.exclude(
            id__in=overdue_queryset.values_list(
                "id",
                flat=True,
            )
        )

    def filter_has_due_date(
        self,
        queryset,
        _name,
        value,
    ):
        return queryset.filter(due_at__isnull=not value)


class AliasedOrderingFilter(OrderingFilter):
    aliases = {}

    def get_ordering(
        self,
        request,
        queryset,
        view,
    ):
        raw_ordering = request.query_params.get(self.ordering_param)

        if not raw_ordering:
            return self.get_default_ordering(view)

        requested_fields = [
            item.strip() for item in raw_ordering.split(",") if item.strip()
        ]

        ordering = []

        for requested_field in requested_fields:
            descending = requested_field.startswith("-")

            alias = requested_field[1:] if descending else requested_field

            actual_field = self.aliases.get(alias)

            if not actual_field:
                continue

            ordering.append(f"-{actual_field}" if descending else actual_field)

        return ordering or self.get_default_ordering(view)


class TaskOrderingFilter(AliasedOrderingFilter):
    aliases = {
        "created_at": "created_at",
        "updated_at": "updated_at",
        "status": "status",
        "title": "batch__title",
        "priority": "batch__priority",
        "start_at": "batch__start_at",
        "due_at": "batch__due_at",
        "assignee": "assignee_name",
        "department": "assigned_department_name",
    }


class TaskBatchOrderingFilter(AliasedOrderingFilter):
    aliases = {
        "created_at": "created_at",
        "updated_at": "updated_at",
        "title": "title",
        "priority": "priority",
        "assignment_type": "assignment_type",
        "start_at": "start_at",
        "due_at": "due_at",
        "department": "source_department_name",
    }


class TaskReminderFilter(django_filters.FilterSet):
    task = django_filters.UUIDFilter(field_name="task_id")

    cancelled = django_filters.BooleanFilter(method="filter_cancelled")

    due = django_filters.BooleanFilter(method="filter_due")

    remind_before = django_filters.IsoDateTimeFilter(
        field_name="remind_at",
        lookup_expr="lte",
    )

    remind_after = django_filters.IsoDateTimeFilter(
        field_name="remind_at",
        lookup_expr="gte",
    )

    class Meta:
        model = TaskReminder

        fields = [
            "task",
            "cancelled",
            "due",
            "remind_before",
            "remind_after",
        ]

    def filter_cancelled(
        self,
        queryset,
        _name,
        value,
    ):
        return queryset.filter(cancelled_at__isnull=not value)

    def filter_due(
        self,
        queryset,
        _name,
        value,
    ):
        due_query = {
            "cancelled_at__isnull": True,
            "remind_at__lte": timezone.now(),
        }

        if value:
            return queryset.filter(**due_query)

        return queryset.exclude(**due_query)


class TaskReminderOrderingFilter(AliasedOrderingFilter):
    aliases = {
        "remind_at": "remind_at",
        "created_at": "created_at",
        "updated_at": "updated_at",
        "task_title": "task__batch__title",
    }
