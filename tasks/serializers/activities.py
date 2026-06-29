from rest_framework import serializers

from tasks.models import TaskActivity

from .common import TaskUserSummarySerializer


SENSITIVE_ACTIVITY_KEYS = {
    "body",
    "comment_body",
    "previous_body",
    "new_body",
    "password",
    "token",
    "secret",
    "authorization",
}


def redact_activity_value(value):
    if isinstance(value, dict):
        redacted = {}

        for key, item in value.items():
            if str(key).lower() in SENSITIVE_ACTIVITY_KEYS:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_activity_value(item)

        return redacted

    if isinstance(value, list):
        return [redact_activity_value(item) for item in value]

    return value


class TaskActivitySerializer(serializers.ModelSerializer):
    task_id = serializers.UUIDField(
        source="task.id",
        read_only=True,
        allow_null=True,
    )

    batch_id = serializers.UUIDField(
        source="batch.id",
        read_only=True,
        allow_null=True,
    )

    actor = TaskUserSummarySerializer(read_only=True)

    activity_display = serializers.CharField(
        source="get_activity_type_display",
        read_only=True,
    )

    previous_value = serializers.SerializerMethodField()

    new_value = serializers.SerializerMethodField()

    metadata = serializers.SerializerMethodField()

    class Meta:
        model = TaskActivity

        fields = (
            "id",
            "task_id",
            "batch_id",
            "actor",
            "activity_type",
            "activity_display",
            "previous_value",
            "new_value",
            "metadata",
            "created_at",
        )

    def get_previous_value(self, activity):
        return redact_activity_value(activity.previous_value)

    def get_new_value(self, activity):
        return redact_activity_value(activity.new_value)

    def get_metadata(self, activity):
        return redact_activity_value(activity.metadata)
