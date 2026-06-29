from collections.abc import Iterable

from tasks.models import (
    Task,
    TaskActivity,
    TaskBatch,
)


def create_batch_activity(
    *,
    batch: TaskBatch,
    activity_type: str,
    actor=None,
    previous_value=None,
    new_value=None,
    metadata=None,
):
    return TaskActivity.objects.create(
        batch=batch,
        actor=actor,
        activity_type=activity_type,
        previous_value=previous_value,
        new_value=new_value,
        metadata=metadata or {},
    )


def create_task_activity(
    *,
    task: Task,
    activity_type: str,
    actor=None,
    previous_value=None,
    new_value=None,
    metadata=None,
):
    return TaskActivity.objects.create(
        task=task,
        batch=task.batch,
        actor=actor,
        activity_type=activity_type,
        previous_value=previous_value,
        new_value=new_value,
        metadata=metadata or {},
    )


def create_task_activities(
    *,
    tasks: Iterable[Task],
    activity_type: str,
    actor=None,
    previous_value=None,
    new_value=None,
    metadata=None,
):
    activities = [
        TaskActivity(
            task=task,
            batch=task.batch,
            actor=actor,
            activity_type=activity_type,
            previous_value=previous_value,
            new_value=new_value,
            metadata={
                **(metadata or {}),
                "assigned_to_id": (
                    str(task.assigned_to_id) if task.assigned_to_id else None
                ),
            },
        )
        for task in tasks
    ]

    return TaskActivity.objects.bulk_create(activities)
