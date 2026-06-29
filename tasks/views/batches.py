from django.core.exceptions import (
    ValidationError as DjangoValidationError,
)
from django.db.models import Count, Q
from django_filters.rest_framework import (
    DjangoFilterBackend,
)
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import (
    filters,
    mixins,
    viewsets,
)
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import (
    IsAuthenticated,
)
from rest_framework.response import Response

from tasks.constants import TaskStatus
from tasks.filters import (
    TaskBatchFilter,
    TaskBatchOrderingFilter,
)
from tasks.models import TaskActivity
from tasks.permissions import IsActiveStaff
from tasks.serializers import (
    TaskActivitySerializer,
    TaskBatchCancellationSerializer,
    TaskBatchDetailSerializer,
    TaskBatchSummarySerializer,
    TaskBatchUpdateSerializer,
)
from tasks.services.access import (
    visible_batches_for,
)
from tasks.services.lifecycle import (
    archive_task_batch,
    cancel_task_batch,
    restore_task_batch,
)

from .utils import (
    raise_api_validation_error,
    validate_activity_type,
)

VALID_BATCH_SCOPES = {
    "my",
    "created",
    "department",
    "all",
}


BATCH_SCOPE_PARAMETER = OpenApiParameter(
    name="scope",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    required=False,
    enum=[
        "my",
        "created",
        "department",
        "all",
    ],
    description=(
        "Filters batches by tasks assigned to the user, "
        "batches created by the user, managed department "
        "tasks or all organisation tasks."
    ),
)


BATCH_ARCHIVED_PARAMETER = OpenApiParameter(
    name="archived",
    type=OpenApiTypes.BOOL,
    location=OpenApiParameter.QUERY,
    required=False,
    description=("Defaults to false. Use true to return only archived batches."),
)


def get_valid_batch_scope(request):
    scope = request.query_params.get("scope")

    if scope in (None, ""):
        return None

    scope = scope.strip().lower()

    if scope not in VALID_BATCH_SCOPES:
        raise ValidationError(
            {"scope": ("scope must be one of: my, created, department or all.")}
        )

    return scope


def get_batch_archived_filter(request):
    value = request.query_params.get("archived")

    if value in (None, ""):
        return False

    value = value.strip().lower()

    if value in {"true", "1", "yes", "on"}:
        return True

    if value in {"false", "0", "no", "off"}:
        return False

    raise ValidationError({"archived": ("archived must be true or false.")})


@extend_schema_view(
    list=extend_schema(
        tags=["Task Batches"],
        parameters=[
            BATCH_SCOPE_PARAMETER,
            BATCH_ARCHIVED_PARAMETER,
        ],
    ),
    retrieve=extend_schema(tags=["Task Batches"]),
)
class TaskBatchViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    http_method_names = [
        "get",
        "patch",
        "post",
        "head",
        "options",
    ]
    permission_classes = [
        IsAuthenticated,
        IsActiveStaff,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        TaskBatchOrderingFilter,
    ]

    filterset_class = TaskBatchFilter

    search_fields = [
        "title",
        "description",
        "source_department_name",
        "source_department_code",
        "created_by_name",
        "created_by_email",
        "tasks__assignee_name",
        "tasks__assignee_employee_id",
    ]

    ordering = ["-created_at"]

    def get_queryset(self):
        scope = get_valid_batch_scope(self.request)

        archived = get_batch_archived_filter(self.request)

        queryset = visible_batches_for(
            self.request.user,
            scope=scope,
            include_archived=True,
        )

        if archived:
            queryset = queryset.filter(archived_at__isnull=False)
        else:
            queryset = queryset.filter(archived_at__isnull=True)

        return queryset.annotate(
            total_tasks_count=Count(
                "tasks",
                distinct=True,
            ),
            to_do_count=Count(
                "tasks",
                filter=Q(tasks__status=TaskStatus.TO_DO),
                distinct=True,
            ),
            in_progress_count=Count(
                "tasks",
                filter=Q(tasks__status=(TaskStatus.IN_PROGRESS)),
                distinct=True,
            ),
            blocked_count=Count(
                "tasks",
                filter=Q(tasks__status=TaskStatus.BLOCKED),
                distinct=True,
            ),
            completed_count=Count(
                "tasks",
                filter=Q(tasks__status=TaskStatus.COMPLETED),
                distinct=True,
            ),
            cancelled_count=Count(
                "tasks",
                filter=Q(tasks__status=TaskStatus.CANCELLED),
                distinct=True,
            ),
        )

    def get_serializer_class(self):
        if self.action == "partial_update":
            return TaskBatchUpdateSerializer

        if self.action == "retrieve":
            return TaskBatchDetailSerializer

        return TaskBatchSummarySerializer

    def partial_update(
        self,
        request,
        *args,
        **kwargs,
    ):
        batch = self.get_object()

        serializer = TaskBatchUpdateSerializer(
            batch,
            data=request.data,
            partial=True,
            context=self.get_serializer_context(),
        )

        serializer.is_valid(raise_exception=True)

        updated_batch = serializer.save()

        return Response(
            TaskBatchDetailSerializer(
                updated_batch,
                context=self.get_serializer_context(),
            ).data
        )

    @extend_schema(
        tags=["Task Batches"],
        request=TaskBatchCancellationSerializer,
        responses=TaskBatchDetailSerializer,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="cancel",
    )
    def cancel(self, request, pk=None):
        batch = self.get_object()

        serializer = TaskBatchCancellationSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)

        try:
            cancelled_batch, affected_tasks = cancel_task_batch(
                batch=batch,
                actor=request.user,
                reason=(serializer.validated_data["reason"]),
                request=request,
            )
        except DjangoValidationError as exc:
            raise_api_validation_error(exc)

        data = TaskBatchDetailSerializer(
            cancelled_batch,
            context=self.get_serializer_context(),
        ).data

        data["affected_task_count"] = len(affected_tasks)

        return Response(data)

    @extend_schema(
        tags=["Task Batches"],
        request=None,
        responses=TaskBatchDetailSerializer,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="archive",
    )
    def archive(self, request, pk=None):
        batch = self.get_object()

        try:
            archived_batch, archived_tasks = archive_task_batch(
                batch=batch,
                actor=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            raise_api_validation_error(exc)

        data = TaskBatchDetailSerializer(
            archived_batch,
            context=self.get_serializer_context(),
        ).data

        data["archived_task_count"] = len(archived_tasks)

        return Response(data)

    @extend_schema(
        tags=["Task Batches"],
        request=None,
        responses=TaskBatchDetailSerializer,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="restore",
    )
    def restore(self, request, pk=None):
        batch = self.get_object()

        try:
            restored_batch, restored_tasks = restore_task_batch(
                batch=batch,
                actor=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            raise_api_validation_error(exc)

        data = TaskBatchDetailSerializer(
            restored_batch,
            context=self.get_serializer_context(),
        ).data

        data["restored_task_count"] = len(restored_tasks)

        return Response(data)

    @extend_schema(
        tags=["Task Activity"],
        responses=TaskActivitySerializer(many=True),
    )
    @action(
        detail=True,
        methods=["get"],
        url_path="activity",
        url_name="activity",
    )
    def activity(self, request, pk=None):
        batch = self.get_object()

        queryset = (
            TaskActivity.objects.select_related(
                "actor",
                "actor__profile",
                "actor__profile__department",
                "task",
                "batch",
            )
            .filter(batch=batch)
            .order_by("-created_at")
        )

        activity_type = validate_activity_type(
            request.query_params.get("activity_type")
        )

        if activity_type:
            queryset = queryset.filter(activity_type=activity_type)

        include_task_activity = (
            request.query_params.get(
                "include_task_activity",
                "true",
            )
            .strip()
            .lower()
        )

        if include_task_activity in {
            "false",
            "0",
            "no",
            "off",
        }:
            queryset = queryset.filter(task__isnull=True)

        elif include_task_activity not in {
            "true",
            "1",
            "yes",
            "on",
        }:
            raise ValidationError({"include_task_activity": ("Must be true or false.")})

        page = self.paginate_queryset(queryset)

        serializer = TaskActivitySerializer(
            page if page is not None else queryset,
            many=True,
            context=self.get_serializer_context(),
        )

        if page is not None:
            return self.get_paginated_response(serializer.data)

        return Response(serializer.data)
