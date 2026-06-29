from rest_framework import serializers

from accounts.models import Department
from notifications.constants import (
    NotificationChannel,
    NotificationEventMode,
)
from tasks.constants import (
    TaskAssignmentType,
    TaskPriority,
)
from tasks.models import (
    ACTIVE_TASK_STATUSES,
    Task,
)
from tasks.services.access import (
    can_archive_task,
    can_cancel_task,
    can_comment_on_task,
    can_manage_task,
    can_restore_task,
    can_update_task_status,
)
from tasks.services.assignments import (
    create_task_assignment,
)

from .batches import (
    TaskBatchDetailSerializer,
    TaskBatchSummarySerializer,
)
from .common import (
    TaskDepartmentSummarySerializer,
    TaskUserSummarySerializer,
)


class TaskListSerializer(serializers.ModelSerializer):
    batch = TaskBatchSummarySerializer(read_only=True)

    assigned_to = TaskUserSummarySerializer(read_only=True)

    assigned_department = TaskDepartmentSummarySerializer(read_only=True)

    is_archived = serializers.BooleanField(read_only=True)

    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = Task

        fields = (
            "id",
            "batch",
            "assigned_to",
            "assignee_name",
            "assignee_email",
            "assignee_employee_id",
            "assigned_department",
            "assigned_department_name",
            "assigned_department_code",
            "status",
            "completed_at",
            "cancelled_at",
            "archived_at",
            "is_archived",
            "is_overdue",
            "created_at",
            "updated_at",
        )


class TaskDetailSerializer(TaskListSerializer):
    batch = TaskBatchDetailSerializer(read_only=True)

    cancelled_by = TaskUserSummarySerializer(read_only=True)

    archived_by = TaskUserSummarySerializer(read_only=True)

    permissions = serializers.SerializerMethodField()

    can_set_reminder = serializers.SerializerMethodField()

    reminders = serializers.SerializerMethodField()

    class Meta(TaskListSerializer.Meta):
        fields = (
            *TaskListSerializer.Meta.fields,
            "cancelled_by",
            "cancellation_reason",
            "archived_by",
            "permissions",
            "can_set_reminder",
            "reminders",
        )

    def get_permissions(self, task):
        request = self.context.get("request")

        if not request:
            return {
                "can_update_status": False,
                "can_manage": False,
                "can_comment": False,
                "can_cancel": False,
                "can_archive": False,
                "can_restore": False,
            }

        user = request.user

        return {
            "can_update_status": can_update_task_status(
                user,
                task,
            ),
            "can_manage": can_manage_task(
                user,
                task,
            ),
            "can_comment": can_comment_on_task(
                user,
                task,
            ),
            "can_cancel": can_cancel_task(
                user,
                task,
            ),
            "can_archive": can_archive_task(
                user,
                task,
            ),
            "can_restore": can_restore_task(
                user,
                task,
            ),
        }

    def get_reminders(self, task):
        request = self.context.get("request")

        if (
            not request
            or task.assigned_to_id != request.user.id
            or task.batch.assignment_type != TaskAssignmentType.PERSONAL
        ):
            return None

        queryset = task.reminders.filter(
            user=request.user,
            cancelled_at__isnull=True,
        ).order_by("remind_at")

        next_reminder = queryset.first()

        return {
            "active_count": queryset.count(),
            "next_reminder_at": (next_reminder.remind_at if next_reminder else None),
        }

    def get_can_set_reminder(self, task):
        request = self.context.get("request")

        if not request:
            return False

        return bool(
            task.assigned_to_id == request.user.id
            and task.batch.assignment_type == TaskAssignmentType.PERSONAL
            and task.status in ACTIVE_TASK_STATUSES
            and task.archived_at is None
        )


class TaskAssignmentInputSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=TaskAssignmentType.choices)

    user_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        default=list,
    )

    department_id = serializers.PrimaryKeyRelatedField(
        source="department",
        queryset=Department.objects.all(),
        required=False,
        allow_null=True,
    )

    include_assigner = serializers.BooleanField(
        required=False,
        default=False,
    )

    def validate(self, attrs):
        assignment_type = attrs["type"]

        user_ids = attrs.get(
            "user_ids",
            [],
        )

        department = attrs.get("department")

        include_assigner = attrs.get(
            "include_assigner",
            False,
        )

        if assignment_type == TaskAssignmentType.PERSONAL:
            if user_ids or department:
                raise serializers.ValidationError(
                    "A personal task cannot contain staff or department recipients."
                )

            if include_assigner:
                raise serializers.ValidationError(
                    {
                        "include_assigner": (
                            "include_assigner only applies to department assignments."
                        )
                    }
                )

        elif assignment_type == TaskAssignmentType.USERS:
            if not user_ids:
                raise serializers.ValidationError(
                    {"user_ids": ("Select at least one staff member.")}
                )

            if department:
                raise serializers.ValidationError(
                    {
                        "department_id": (
                            "A selected-staff assignment "
                            "cannot also specify a department."
                        )
                    }
                )

            if include_assigner:
                raise serializers.ValidationError(
                    {
                        "include_assigner": (
                            "include_assigner only applies to department assignments."
                        )
                    }
                )

        elif assignment_type == TaskAssignmentType.DEPARTMENT:
            if not department:
                raise serializers.ValidationError(
                    {"department_id": ("Select a department.")}
                )

            if user_ids:
                raise serializers.ValidationError(
                    {
                        "user_ids": (
                            "A department assignment cannot "
                            "also specify individual users."
                        )
                    }
                )

        return attrs


class TaskAssignmentCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)

    description = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )

    priority = serializers.ChoiceField(
        choices=TaskPriority.choices,
        default=TaskPriority.MEDIUM,
    )

    start_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
    )

    due_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
    )

    assignment = TaskAssignmentInputSerializer()

    notification_channels = serializers.ListField(
        child=serializers.ChoiceField(choices=NotificationChannel.choices),
        required=False,
        allow_empty=False,
    )

    notification_event_mode = serializers.ChoiceField(
        choices=NotificationEventMode.choices,
        required=False,
    )

    def validate_title(self, title):
        title = title.strip()

        if not title:
            raise serializers.ValidationError("A task title is required.")

        return title

    def validate_description(self, description):
        return description.strip()

    def validate(self, attrs):
        attrs = super().validate(attrs)

        start_at = attrs.get("start_at")
        due_at = attrs.get("due_at")

        if start_at and due_at and due_at < start_at:
            raise serializers.ValidationError(
                {"due_at": ("The due time cannot be before the start time.")}
            )

        assignment_type = attrs["assignment"]["type"]

        if assignment_type == TaskAssignmentType.PERSONAL:
            if "notification_channels" in attrs:
                raise serializers.ValidationError(
                    {
                        "notification_channels": (
                            "Personal task creation does not "
                            "send assignment notifications."
                        )
                    }
                )

            if "notification_event_mode" in attrs:
                raise serializers.ValidationError(
                    {
                        "notification_event_mode": (
                            "Personal task creation does not "
                            "use a notification event mode."
                        )
                    }
                )

        return attrs

    def create(self, validated_data):
        request = self.context["request"]

        assignment = validated_data.pop("assignment")

        assignment_type = assignment.pop("type")

        user_ids = assignment.pop(
            "user_ids",
            [],
        )

        department = assignment.pop(
            "department",
            None,
        )

        include_assigner = assignment.pop(
            "include_assigner",
            False,
        )

        notification_channels = validated_data.pop(
            "notification_channels",
            None,
        )

        notification_event_mode = validated_data.pop(
            "notification_event_mode",
            None,
        )

        return create_task_assignment(
            creator=request.user,
            assignment_type=assignment_type,
            user_ids=user_ids,
            department=department,
            include_assigner=include_assigner,
            notification_channels=(notification_channels),
            notification_event_mode=(notification_event_mode),
            request=request,
            **validated_data,
        )

    def to_representation(self, result):
        return {
            "detail": (
                "Task created successfully."
                if result.recipient_count == 1
                else (
                    f"{result.recipient_count} individual tasks created successfully."
                )
            ),
            "batch": TaskBatchDetailSerializer(
                result.batch,
                context=self.context,
            ).data,
            "tasks": TaskListSerializer(
                result.tasks,
                many=True,
                context=self.context,
            ).data,
            "recipient_count": result.recipient_count,
            "notification_scheduled": result.notification_scheduled,
        }
