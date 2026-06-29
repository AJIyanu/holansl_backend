from django.core.exceptions import (
    ValidationError as DjangoValidationError,
)

from rest_framework import serializers

from tasks.constants import (
    TaskPriority,
    TaskStatus,
)
from tasks.services.lifecycle import (
    update_task_batch,
)


def convert_django_validation_error(exc):
    if hasattr(exc, "message_dict"):
        return exc.message_dict

    if hasattr(exc, "messages"):
        return exc.messages

    return str(exc)


class TaskStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            choice for choice in TaskStatus.choices if choice[0] != TaskStatus.CANCELLED
        ]
    )


class TaskCancellationSerializer(serializers.Serializer):
    reason = serializers.CharField(
        max_length=2000,
        trim_whitespace=True,
    )

    def validate_reason(self, reason):
        reason = reason.strip()

        if not reason:
            raise serializers.ValidationError("A cancellation reason is required.")

        return reason


class TaskBatchCancellationSerializer(TaskCancellationSerializer):
    pass


class TaskBatchUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(
        max_length=255,
        required=False,
    )

    description = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    priority = serializers.ChoiceField(
        choices=TaskPriority.choices,
        required=False,
    )

    start_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
    )

    due_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)

        if not attrs:
            raise serializers.ValidationError("Provide at least one field to update.")

        start_at = attrs.get(
            "start_at",
            self.instance.start_at,
        )

        due_at = attrs.get(
            "due_at",
            self.instance.due_at,
        )

        if start_at and due_at and due_at < start_at:
            raise serializers.ValidationError(
                {"due_at": ("The due time cannot be before the start time.")}
            )

        return attrs

    def update(self, instance, validated_data):
        request = self.context["request"]

        try:
            return update_task_batch(
                batch=instance,
                actor=request.user,
                changes=validated_data,
                request=request,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                convert_django_validation_error(exc)
            ) from exc

    def create(self, validated_data):
        raise NotImplementedError
