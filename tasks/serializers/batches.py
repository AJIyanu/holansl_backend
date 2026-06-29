from django.db.models import Count

from rest_framework import serializers

from tasks.constants import TaskStatus
from tasks.models import TaskBatch
from tasks.services.access import (
    can_manage_batch,
    can_view_all_tasks,
)

from .common import (
    TaskDepartmentSummarySerializer,
    TaskUserSummarySerializer,
)


class TaskBatchSummarySerializer(serializers.ModelSerializer):
    created_by = TaskUserSummarySerializer(read_only=True)

    source_department = TaskDepartmentSummarySerializer(read_only=True)

    is_cancelled = serializers.BooleanField(read_only=True)

    is_archived = serializers.BooleanField(read_only=True)

    class Meta:
        model = TaskBatch

        fields = (
            "id",
            "title",
            "description",
            "assignment_type",
            "priority",
            "start_at",
            "due_at",
            "source_department",
            "source_department_name",
            "source_department_code",
            "created_by",
            "created_by_name",
            "created_by_email",
            "is_cancelled",
            "is_archived",
            "created_at",
            "updated_at",
        )


class TaskBatchDetailSerializer(TaskBatchSummarySerializer):
    cancelled_by = TaskUserSummarySerializer(read_only=True)

    archived_by = TaskUserSummarySerializer(read_only=True)

    progress = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()

    class Meta(TaskBatchSummarySerializer.Meta):
        fields = (
            *TaskBatchSummarySerializer.Meta.fields,
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
            "archived_at",
            "archived_by",
            "progress",
            "can_manage",
        )

    def get_can_manage(self, batch):
        request = self.context.get("request")

        if not request:
            return False

        return can_manage_batch(
            request.user,
            batch,
        )

    def get_progress(self, batch):
        request = self.context.get("request")

        if not request:
            return None

        user = request.user

        may_view_progress = bool(
            batch.created_by_id == user.id
            or can_manage_batch(user, batch)
            or can_view_all_tasks(user)
        )

        if not may_view_progress:
            return None

        total = getattr(
            batch,
            "total_tasks_count",
            None,
        )

        if total is None:
            counts = {
                row["status"]: row["count"]
                for row in (
                    batch.tasks.values("status")
                    .annotate(count=Count("id"))
                )
            }

            total = sum(counts.values())

            to_do = counts.get(
                TaskStatus.TO_DO,
                0,
            )

            in_progress = counts.get(
                TaskStatus.IN_PROGRESS,
                0,
            )

            blocked = counts.get(
                TaskStatus.BLOCKED,
                0,
            )

            completed = counts.get(
                TaskStatus.COMPLETED,
                0,
            )

            cancelled = counts.get(
                TaskStatus.CANCELLED,
                0,
            )

        else:
            to_do = getattr(
                batch,
                "to_do_count",
                0,
            )

            in_progress = getattr(
                batch,
                "in_progress_count",
                0,
            )

            blocked = getattr(
                batch,
                "blocked_count",
                0,
            )

            completed = getattr(
                batch,
                "completed_count",
                0,
            )

            cancelled = getattr(
                batch,
                "cancelled_count",
                0,
            )

        completion_percentage = (
            round(
                (completed / total) * 100,
                2,
            )
            if total
            else 0
        )

        return {
            "total": total,
            "to_do": to_do,
            "in_progress": in_progress,
            "blocked": blocked,
            "completed": completed,
            "cancelled": cancelled,
            "completion_percentage": completion_percentage,
        }
