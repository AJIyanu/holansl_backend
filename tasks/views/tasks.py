from django.core.exceptions import (
    ValidationError as DjangoValidationError,
)
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import (
    DjangoFilterBackend,
)
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import (
    filters,
    mixins,
    status,
    viewsets,
)
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import (
    IsAuthenticated,
)
from rest_framework.response import Response

from tasks.filters import (
    TaskFilter,
    TaskOrderingFilter,
)
from tasks.models import (
    TaskActivity,
    TaskComment,
)
from tasks.permissions import IsActiveStaff
from tasks.serializers import (
    TaskActivitySerializer,
    TaskAssignmentCreateSerializer,
    TaskCancellationSerializer,
    TaskCommentCreateSerializer,
    TaskCommentRemovalSerializer,
    TaskCommentSerializer,
    TaskCommentUpdateSerializer,
    TaskDetailSerializer,
    TaskListSerializer,
    TaskStatusUpdateSerializer,
)
from tasks.services.access import visible_tasks_for
from tasks.services.comments import (
    add_task_comment,
    edit_task_comment,
    remove_task_comment,
)
from tasks.services.lifecycle import (
    archive_task,
    cancel_task,
    restore_task,
    transition_task_status,
)

from .utils import (
    raise_api_validation_error,
    validate_activity_type,
)

VALID_TASK_SCOPES = {
    "my",
    "created",
    "department",
    "all",
}


TASK_SCOPE_PARAMETER = OpenApiParameter(
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
        "Restricts results to the authenticated user's "
        "tasks, created assignments, managed departments "
        "or all organisation tasks. The backend still "
        "validates permission for the selected scope."
    ),
)


TASK_ARCHIVED_PARAMETER = OpenApiParameter(
    name="archived",
    type=OpenApiTypes.BOOL,
    location=OpenApiParameter.QUERY,
    required=False,
    description=("Defaults to false. Use true to return only archived task records."),
)


def get_valid_scope(request):
    scope = request.query_params.get("scope")

    if scope in (None, ""):
        return None

    scope = scope.strip().lower()

    if scope not in VALID_TASK_SCOPES:
        raise ValidationError(
            {"scope": ("scope must be one of: my, created, department or all.")}
        )

    return scope


def get_archived_filter(request):
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
        tags=["Tasks"],
        parameters=[
            TASK_SCOPE_PARAMETER,
            TASK_ARCHIVED_PARAMETER,
        ],
    ),
    retrieve=extend_schema(tags=["Tasks"]),
    create=extend_schema(
        tags=["Tasks"],
        examples=[
            OpenApiExample(
                "Create personal task",
                value={
                    "title": "Prepare weekly report",
                    "description": ("Prepare the weekly activity report."),
                    "priority": "MEDIUM",
                    "due_at": ("2026-07-03T16:00:00+01:00"),
                    "assignment": {"type": "PERSONAL"},
                },
                request_only=True,
            ),
            OpenApiExample(
                "Assign selected staff",
                value={
                    "title": ("Review supplier records"),
                    "description": ("Review and update supplier records."),
                    "priority": "HIGH",
                    "due_at": ("2026-07-05T17:00:00+01:00"),
                    "assignment": {
                        "type": "USERS",
                        "user_ids": [
                            "00000000-0000-0000-0000-000000000001",
                            "00000000-0000-0000-0000-000000000002",
                        ],
                    },
                    "notification_channels": ["DASHBOARD", "EMAIL"],
                    "notification_event_mode": "SHARED",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Assign department",
                value={
                    "title": ("Complete department report"),
                    "description": ("Submit your individual contribution."),
                    "priority": "HIGH",
                    "assignment": {
                        "type": "DEPARTMENT",
                        "department_id": ("00000000-0000-0000-0000-000000000001"),
                        "include_assigner": False,
                    },
                },
                request_only=True,
            ),
        ],
    ),
)
class TaskViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [
        IsAuthenticated,
        IsActiveStaff,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        TaskOrderingFilter,
    ]

    filterset_class = TaskFilter

    search_fields = [
        "batch__title",
        "batch__description",
        "assignee_name",
        "assignee_email",
        "assignee_employee_id",
        "assigned_to__username",
        "assigned_to__first_name",
        "assigned_to__last_name",
        "assigned_department_name",
        "assigned_department_code",
        "batch__created_by_name",
        "batch__source_department_name",
    ]

    ordering = ["-created_at"]

    def get_queryset(self):
        scope = get_valid_scope(self.request)

        archived = get_archived_filter(self.request)

        queryset = visible_tasks_for(
            self.request.user,
            scope=scope,
            include_archived=True,
        )

        if archived:
            queryset = queryset.filter(archived_at__isnull=False)
        else:
            queryset = queryset.filter(archived_at__isnull=True)

        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return TaskAssignmentCreateSerializer

        if self.action == "retrieve":
            return TaskDetailSerializer

        return TaskListSerializer

    def create(
        self,
        request,
        *args,
        **kwargs,
    ):
        serializer = self.get_serializer(data=request.data)

        serializer.is_valid(raise_exception=True)

        result = serializer.save()

        response_serializer = self.get_serializer(result)

        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Tasks"],
        request=TaskStatusUpdateSerializer,
        responses=TaskDetailSerializer,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="status",
    )
    def set_status(self, request, pk=None):
        task = self.get_object()

        serializer = TaskStatusUpdateSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)

        try:
            updated_task = transition_task_status(
                task=task,
                actor=request.user,
                new_status=(serializer.validated_data["status"]),
                request=request,
            )
        except DjangoValidationError as exc:
            raise_api_validation_error(exc)

        return Response(
            TaskDetailSerializer(
                updated_task,
                context=self.get_serializer_context(),
            ).data
        )

    @extend_schema(
        tags=["Tasks"],
        request=TaskCancellationSerializer,
        responses=TaskDetailSerializer,
        examples=[
            OpenApiExample(
                "Cancel task",
                value={"reason": ("The task is no longer required.")},
                request_only=True,
            )
        ],
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="cancel",
    )
    def cancel(self, request, pk=None):
        task = self.get_object()

        serializer = TaskCancellationSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)

        try:
            cancelled_task = cancel_task(
                task=task,
                actor=request.user,
                reason=(serializer.validated_data["reason"]),
                request=request,
            )
        except DjangoValidationError as exc:
            raise_api_validation_error(exc)

        return Response(
            TaskDetailSerializer(
                cancelled_task,
                context=self.get_serializer_context(),
            ).data
        )

    @extend_schema(
        tags=["Tasks"],
        request=None,
        responses=TaskDetailSerializer,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="archive",
    )
    def archive(self, request, pk=None):
        task = self.get_object()

        try:
            archived_task = archive_task(
                task=task,
                actor=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            raise_api_validation_error(exc)

        return Response(
            TaskDetailSerializer(
                archived_task,
                context=self.get_serializer_context(),
            ).data
        )

    @extend_schema(
        tags=["Tasks"],
        request=None,
        responses=TaskDetailSerializer,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="restore",
    )
    def restore(self, request, pk=None):
        task = self.get_object()

        try:
            restored_task = restore_task(
                task=task,
                actor=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            raise_api_validation_error(exc)

        return Response(
            TaskDetailSerializer(
                restored_task,
                context=self.get_serializer_context(),
            ).data
        )

    @extend_schema(
        tags=["Task Comments"],
        request=TaskCommentCreateSerializer,
        responses=TaskCommentSerializer,
    )
    @action(
        detail=True,
        methods=["get", "post"],
        url_path="comments",
        url_name="comments",
    )
    def comments(self, request, pk=None):
        task = self.get_object()

        if request.method == "POST":
            input_serializer = TaskCommentCreateSerializer(data=request.data)

            input_serializer.is_valid(raise_exception=True)

            try:
                comment = add_task_comment(
                    task=task,
                    actor=request.user,
                    body=(input_serializer.validated_data["body"]),
                    request=request,
                )
            except DjangoValidationError as exc:
                raise_api_validation_error(exc)

            return Response(
                TaskCommentSerializer(
                    comment,
                    context=(self.get_serializer_context()),
                ).data,
                status=status.HTTP_201_CREATED,
            )

        queryset = (
            TaskComment.objects.select_related(
                "task",
                "task__batch",
                "author",
                "author__profile",
                "author__profile__department",
                "removed_by",
                "removed_by__profile",
                "removed_by__profile__department",
            )
            .filter(task=task)
            .order_by("created_at")
        )

        page = self.paginate_queryset(queryset)

        serializer = TaskCommentSerializer(
            page if page is not None else queryset,
            many=True,
            context=self.get_serializer_context(),
        )

        if page is not None:
            return self.get_paginated_response(serializer.data)

        return Response(serializer.data)

    @extend_schema(
        tags=["Task Comments"],
        request=TaskCommentUpdateSerializer,
        responses=TaskCommentSerializer,
    )
    @action(
        detail=True,
        methods=["patch"],
        url_path=(r"comments/(?P<comment_id>[^/.]+)"),
        url_name="comment-detail",
    )
    def update_comment(
        self,
        request,
        pk=None,
        comment_id=None,
    ):
        task = self.get_object()

        comment = get_object_or_404(
            TaskComment.objects.select_related(
                "task",
                "task__batch",
                "task__batch__created_by",
                "task__assigned_to",
                "author",
                "removed_by",
            ),
            pk=comment_id,
            task=task,
        )

        input_serializer = TaskCommentUpdateSerializer(data=request.data)

        input_serializer.is_valid(raise_exception=True)

        try:
            updated_comment = edit_task_comment(
                comment=comment,
                actor=request.user,
                body=(input_serializer.validated_data["body"]),
                request=request,
            )
        except DjangoValidationError as exc:
            raise_api_validation_error(exc)

        return Response(
            TaskCommentSerializer(
                updated_comment,
                context=self.get_serializer_context(),
            ).data
        )

    @extend_schema(
        tags=["Task Comments"],
        request=TaskCommentRemovalSerializer,
        responses=TaskCommentSerializer,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=(r"comments/(?P<comment_id>[^/.]+)/remove"),
        url_name="comment-remove",
    )
    def remove_comment(
        self,
        request,
        pk=None,
        comment_id=None,
    ):
        task = self.get_object()

        comment = get_object_or_404(
            TaskComment.objects.select_related(
                "task",
                "task__batch",
                "task__batch__created_by",
                "task__assigned_to",
                "author",
                "removed_by",
            ),
            pk=comment_id,
            task=task,
        )

        input_serializer = TaskCommentRemovalSerializer(data=request.data)

        input_serializer.is_valid(raise_exception=True)

        try:
            removed_comment = remove_task_comment(
                comment=comment,
                actor=request.user,
                reason=(input_serializer.validated_data["reason"]),
                request=request,
            )
        except DjangoValidationError as exc:
            raise_api_validation_error(exc)

        return Response(
            TaskCommentSerializer(
                removed_comment,
                context=self.get_serializer_context(),
            ).data
        )

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
        task = self.get_object()

        queryset = (
            TaskActivity.objects.select_related(
                "actor",
                "actor__profile",
                "actor__profile__department",
                "task",
                "batch",
            )
            .filter(task=task)
            .order_by("-created_at")
        )

        activity_type = validate_activity_type(
            request.query_params.get("activity_type")
        )

        if activity_type:
            queryset = queryset.filter(activity_type=activity_type)

        page = self.paginate_queryset(queryset)

        serializer = TaskActivitySerializer(
            page if page is not None else queryset,
            many=True,
            context=self.get_serializer_context(),
        )

        if page is not None:
            return self.get_paginated_response(serializer.data)

        return Response(serializer.data)
