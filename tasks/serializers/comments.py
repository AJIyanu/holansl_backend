from rest_framework import serializers

from tasks.models import TaskComment
from tasks.services.access import (
    can_edit_task_comment,
    can_remove_task_comment,
)

from .common import TaskUserSummarySerializer


class TaskCommentSerializer(serializers.ModelSerializer):
    task_id = serializers.UUIDField(
        source="task.id",
        read_only=True,
    )

    author = TaskUserSummarySerializer(read_only=True)

    removed_by = TaskUserSummarySerializer(read_only=True)

    body = serializers.SerializerMethodField()

    is_removed = serializers.BooleanField(read_only=True)

    permissions = serializers.SerializerMethodField()

    class Meta:
        model = TaskComment

        fields = (
            "id",
            "task_id",
            "author",
            "body",
            "edited_at",
            "is_removed",
            "removed_at",
            "removed_by",
            "removal_reason",
            "permissions",
            "created_at",
            "updated_at",
        )

    def get_body(self, comment):
        if comment.removed_at:
            return "[Comment removed]"

        return comment.body

    def get_permissions(self, comment):
        request = self.context.get("request")

        if not request:
            return {
                "can_edit": False,
                "can_remove": False,
            }

        return {
            "can_edit": can_edit_task_comment(
                request.user,
                comment,
            ),
            "can_remove": can_remove_task_comment(
                request.user,
                comment,
            ),
        }


class TaskCommentCreateSerializer(serializers.Serializer):
    body = serializers.CharField(
        max_length=10000,
        trim_whitespace=True,
    )

    def validate_body(self, body):
        body = body.strip()

        if not body:
            raise serializers.ValidationError("A comment cannot be empty.")

        return body


class TaskCommentUpdateSerializer(TaskCommentCreateSerializer):
    pass


class TaskCommentRemovalSerializer(serializers.Serializer):
    reason = serializers.CharField(
        max_length=2000,
        trim_whitespace=True,
    )

    def validate_reason(self, reason):
        reason = reason.strip()

        if not reason:
            raise serializers.ValidationError("A removal reason is required.")

        return reason
