from django_filters.rest_framework import (
    DjangoFilterBackend,
)

from drf_spectacular.utils import (
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
from rest_framework.permissions import (
    IsAuthenticated,
)
from rest_framework.response import Response

from tasks.filters import (
    TaskReminderFilter,
    TaskReminderOrderingFilter,
)
from tasks.models import TaskReminder
from tasks.permissions import IsActiveStaff
from tasks.serializers import (
    TaskReminderCancellationSerializer,
    TaskReminderCreateSerializer,
    TaskReminderSerializer,
    TaskReminderUpdateSerializer,
)
from tasks.services.reminders import (
    get_reminder_capabilities,
)


@extend_schema_view(
    list=extend_schema(tags=["Task Reminders"]),
    retrieve=extend_schema(tags=["Task Reminders"]),
    create=extend_schema(tags=["Task Reminders"]),
    partial_update=extend_schema(tags=["Task Reminders"]),
)
class TaskReminderViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [
        IsAuthenticated,
        IsActiveStaff,
    ]

    http_method_names = [
        "get",
        "post",
        "patch",
        "head",
        "options",
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        TaskReminderOrderingFilter,
    ]

    filterset_class = TaskReminderFilter

    search_fields = [
        "task__batch__title",
        "task__batch__description",
    ]

    ordering = ["remind_at"]

    def get_queryset(self):
        return TaskReminder.objects.select_related(
            "task",
            "task__batch",
            "task__assigned_to",
            "user",
            "user__profile",
            "user__profile__department",
            "notification",
            "cancelled_by",
            "cancelled_by__profile",
            "cancelled_by__profile__department",
        ).filter(user=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return TaskReminderCreateSerializer

        if self.action == "partial_update":
            return TaskReminderUpdateSerializer

        return TaskReminderSerializer

    def create(
        self,
        request,
        *args,
        **kwargs,
    ):
        serializer = self.get_serializer(data=request.data)

        serializer.is_valid(raise_exception=True)

        reminder = serializer.save()

        return Response(
            TaskReminderSerializer(
                reminder,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(
        self,
        request,
        *args,
        **kwargs,
    ):
        reminder = self.get_object()

        serializer = TaskReminderUpdateSerializer(
            reminder,
            data=request.data,
            partial=True,
            context=self.get_serializer_context(),
        )

        serializer.is_valid(raise_exception=True)

        updated_reminder = serializer.save()

        return Response(
            TaskReminderSerializer(
                updated_reminder,
                context=self.get_serializer_context(),
            ).data
        )

    @extend_schema(
        tags=["Task Reminders"],
        request=TaskReminderCancellationSerializer,
        responses=TaskReminderSerializer,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="cancel",
    )
    def cancel(self, request, pk=None):
        reminder = self.get_object()

        serializer = TaskReminderCancellationSerializer(
            reminder,
            data=request.data,
            context=self.get_serializer_context(),
        )

        serializer.is_valid(raise_exception=True)

        cancelled_reminder = serializer.save()

        return Response(
            TaskReminderSerializer(
                cancelled_reminder,
                context=self.get_serializer_context(),
            ).data
        )

    @extend_schema(
        tags=["Task Reminders"],
        responses=dict,
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="capabilities",
    )
    def capabilities(self, request):
        return Response(get_reminder_capabilities())
