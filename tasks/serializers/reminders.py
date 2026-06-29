from django.utils import timezone
from rest_framework import serializers

from notifications.constants import (
    NotificationChannel,
)
from tasks.constants import (
    TaskAssignmentType,
)
from tasks.models import (
    ACTIVE_TASK_STATUSES,
    Task,
    TaskReminder,
)
from tasks.services.reminders import (
    cancel_task_reminder,
    create_task_reminder,
    update_task_reminder,
)

from .common import TaskUserSummarySerializer


class TaskReminderTaskSerializer(serializers.ModelSerializer):
    title = serializers.CharField(
        source="batch.title",
        read_only=True,
    )

    priority = serializers.CharField(
        source="batch.priority",
        read_only=True,
    )

    due_at = serializers.DateTimeField(
        source="batch.due_at",
        read_only=True,
    )

    class Meta:
        model = Task

        fields = (
            "id",
            "title",
            "priority",
            "due_at",
            "status",
        )


class TaskReminderSerializer(serializers.ModelSerializer):
    task = TaskReminderTaskSerializer(read_only=True)

    user = TaskUserSummarySerializer(read_only=True)

    cancelled_by = TaskUserSummarySerializer(read_only=True)

    notification_id = serializers.UUIDField(
        source="notification.id",
        read_only=True,
        allow_null=True,
    )

    state = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = TaskReminder

        fields = (
            "id",
            "task",
            "user",
            "remind_at",
            "channels",
            "notification_id",
            "state",
            "cancelled_at",
            "cancelled_by",
            "permissions",
            "created_at",
            "updated_at",
        )

    def get_state(self, reminder):
        if reminder.cancelled_at:
            return "CANCELLED"

        if reminder.is_due:
            return "DUE"

        return "SCHEDULED"

    def get_permissions(self, reminder):
        request = self.context.get("request")

        is_owner = bool(request and request.user.id == reminder.user_id)

        can_edit = bool(
            is_owner
            and reminder.cancelled_at is None
            and reminder.remind_at > timezone.now()
            and reminder.task.status in ACTIVE_TASK_STATUSES
            and reminder.task.archived_at is None
        )

        return {
            "can_edit": can_edit,
            "can_cancel": bool(is_owner and reminder.cancelled_at is None),
        }


class TaskReminderCreateSerializer(serializers.Serializer):
    task_id = serializers.PrimaryKeyRelatedField(
        source="task",
        queryset=Task.objects.select_related(
            "batch",
            "assigned_to",
        ),
    )

    remind_at = serializers.DateTimeField()

    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=NotificationChannel.choices),
        allow_empty=False,
    )

    def create(self, validated_data):
        request = self.context["request"]

        return create_task_reminder(
            user=request.user,
            request=request,
            **validated_data,
        )

    def to_representation(self, instance):
        return TaskReminderSerializer(
            instance,
            context=self.context,
        ).data

    def get_fields(self):
        fields = super().get_fields()

        request = self.context.get("request")

        queryset = Task.objects.none()

        if request and request.user and request.user.is_authenticated:
            queryset = Task.objects.select_related(
                "batch",
                "assigned_to",
            ).filter(
                assigned_to=request.user,
                batch__assignment_type=(TaskAssignmentType.PERSONAL),
                status__in=ACTIVE_TASK_STATUSES,
                archived_at__isnull=True,
                batch__cancelled_at__isnull=True,
            )

        fields["task_id"].queryset = queryset

        return fields


class TaskReminderUpdateSerializer(serializers.Serializer):
    remind_at = serializers.DateTimeField(required=False)

    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=NotificationChannel.choices),
        required=False,
        allow_empty=False,
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide remind_at or channels.")

        return attrs

    def update(self, instance, validated_data):
        request = self.context["request"]

        return update_task_reminder(
            reminder=instance,
            user=request.user,
            request=request,
            **validated_data,
        )

    def create(self, validated_data):
        raise NotImplementedError

    def to_representation(self, instance):
        return TaskReminderSerializer(
            instance,
            context=self.context,
        ).data


class TaskReminderCancellationSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=500,
        default="Cancelled by user.",
    )

    def update(self, instance, validated_data):
        request = self.context["request"]

        return cancel_task_reminder(
            reminder=instance,
            user=request.user,
            reason=validated_data.get(
                "reason",
                "Cancelled by user.",
            ),
            request=request,
        )

    def create(self, validated_data):
        raise NotImplementedError

    def to_representation(self, instance):
        return TaskReminderSerializer(
            instance,
            context=self.context,
        ).data
