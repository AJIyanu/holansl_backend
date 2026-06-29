from unittest.mock import patch

from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.test import override_settings
from django.utils import timezone

from tasks.constants import (
    TaskActivityType,
    TaskAssignmentType,
    TaskStatus,
)
from tasks.models import TaskActivity
from tasks.services.assignments import (
    create_task_assignment,
)
from tasks.services.lifecycle import (
    archive_task,
    archive_task_batch,
    cancel_task,
    cancel_task_batch,
    restore_task,
    restore_task_batch,
    transition_task_status,
    update_task_batch,
)

from .base import TaskModelTestCase


@override_settings(
    TASK_ASSIGNMENT_NOTIFICATIONS_ENABLED=False,
    TASK_LIFECYCLE_NOTIFICATION_CHANNELS=["DASHBOARD"],
)
class TaskLifecycleTests(TaskModelTestCase):
    def create_manager_assignment(self):
        manager = self.create_staff(
            username="lifecycle-manager",
            department=self.department,
            job_title="Manager",
        )

        self.create_department_leader(profile=manager)

        for codename in [
            "assign_task",
            "view_department_task",
            "manage_department_task",
        ]:
            self.grant_permission(
                manager.user,
                codename,
            )

        result = create_task_assignment(
            creator=manager.user,
            assignment_type=(TaskAssignmentType.USERS),
            title="Assigned lifecycle task",
            user_ids=[self.user.id],
        )

        return manager, result

    def test_assignee_can_change_status(self):
        _manager, result = self.create_manager_assignment()

        task = transition_task_status(
            task=result.tasks[0],
            actor=self.user,
            new_status=TaskStatus.IN_PROGRESS,
        )

        self.assertEqual(
            task.status,
            TaskStatus.IN_PROGRESS,
        )

        self.assertTrue(
            TaskActivity.objects.filter(
                task=task,
                activity_type=(TaskActivityType.STATUS_CHANGED),
            ).exists()
        )

    def test_cancelled_status_requires_cancel_action(
        self,
    ):
        task = self.create_personal_task()

        with self.assertRaises(ValidationError):
            transition_task_status(
                task=task,
                actor=self.user,
                new_status=TaskStatus.CANCELLED,
            )

    def test_completed_task_is_final(self):
        task = self.create_personal_task()

        task = transition_task_status(
            task=task,
            actor=self.user,
            new_status=TaskStatus.COMPLETED,
        )

        with self.assertRaises(ValidationError):
            transition_task_status(
                task=task,
                actor=self.user,
                new_status=TaskStatus.IN_PROGRESS,
            )

    def test_personal_task_owner_can_cancel(self):
        task = self.create_personal_task()

        cancelled = cancel_task(
            task=task,
            actor=self.user,
            reason="No longer required.",
        )

        self.assertEqual(
            cancelled.status,
            TaskStatus.CANCELLED,
        )

        self.assertEqual(
            cancelled.cancelled_by,
            self.user,
        )

    def test_assignee_cannot_cancel_assigned_task(
        self,
    ):
        _manager, result = self.create_manager_assignment()

        with self.assertRaises(PermissionDenied):
            cancel_task(
                task=result.tasks[0],
                actor=self.user,
                reason="I do not want to do it.",
            )

    def test_manager_can_cancel_assigned_task(self):
        manager, result = self.create_manager_assignment()

        cancelled = cancel_task(
            task=result.tasks[0],
            actor=manager.user,
            reason="Requirement withdrawn.",
        )

        self.assertEqual(
            cancelled.status,
            TaskStatus.CANCELLED,
        )

    def test_batch_cancellation_leaves_completed_tasks_completed(
        self,
    ):
        manager = self.create_staff(
            username="batch-manager",
            department=self.department,
        )

        self.create_department_leader(profile=manager)

        for codename in [
            "assign_task",
            "manage_department_task",
        ]:
            self.grant_permission(
                manager.user,
                codename,
            )

        result = create_task_assignment(
            creator=manager.user,
            assignment_type=(TaskAssignmentType.USERS),
            title="Batch cancellation",
            user_ids=[
                self.user.id,
                self.other_user.id,
            ],
        )

        transition_task_status(
            task=result.tasks[0],
            actor=self.user,
            new_status=TaskStatus.COMPLETED,
        )

        batch, affected = cancel_task_batch(
            batch=result.batch,
            actor=manager.user,
            reason="Assignment withdrawn.",
        )

        result.tasks[0].refresh_from_db()
        result.tasks[1].refresh_from_db()

        self.assertEqual(
            result.tasks[0].status,
            TaskStatus.COMPLETED,
        )

        self.assertEqual(
            result.tasks[1].status,
            TaskStatus.CANCELLED,
        )

        self.assertEqual(len(affected), 1)
        self.assertIsNotNone(batch.cancelled_at)

    def test_only_final_task_can_be_archived(self):
        task = self.create_personal_task()

        with self.assertRaises(ValidationError):
            archive_task(
                task=task,
                actor=self.user,
            )

        task = transition_task_status(
            task=task,
            actor=self.user,
            new_status=TaskStatus.COMPLETED,
        )

        archived = archive_task(
            task=task,
            actor=self.user,
        )

        self.assertIsNotNone(archived.archived_at)

    def test_archived_task_can_be_restored(self):
        task = self.create_personal_task()

        task = transition_task_status(
            task=task,
            actor=self.user,
            new_status=TaskStatus.COMPLETED,
        )

        task = archive_task(
            task=task,
            actor=self.user,
        )

        restored = restore_task(
            task=task,
            actor=self.user,
        )

        self.assertIsNone(restored.archived_at)

    def test_batch_archive_requires_all_tasks_final(
        self,
    ):
        manager, result = self.create_manager_assignment()

        with self.assertRaises(ValidationError):
            archive_task_batch(
                batch=result.batch,
                actor=manager.user,
            )

    def test_batch_archive_and_restore_children(
        self,
    ):
        manager, result = self.create_manager_assignment()

        transition_task_status(
            task=result.tasks[0],
            actor=self.user,
            new_status=TaskStatus.COMPLETED,
        )

        batch, archived_tasks = archive_task_batch(
            batch=result.batch,
            actor=manager.user,
        )

        self.assertEqual(
            len(archived_tasks),
            1,
        )

        batch, restored_tasks = restore_task_batch(
            batch=batch,
            actor=manager.user,
        )

        self.assertEqual(
            len(restored_tasks),
            1,
        )

        self.assertIsNone(batch.archived_at)

    def test_creator_can_update_shared_details(
        self,
    ):
        manager, result = self.create_manager_assignment()

        updated = update_task_batch(
            batch=result.batch,
            actor=manager.user,
            changes={
                "title": "Updated lifecycle task",
                "priority": "HIGH",
                "due_at": (timezone.now() + timezone.timedelta(days=5)),
            },
        )

        self.assertEqual(
            updated.title,
            "Updated lifecycle task",
        )

        self.assertEqual(
            updated.priority,
            "HIGH",
        )

    @patch(
        "tasks.services.task_notifications.notify",
        side_effect=RuntimeError("Notification failure"),
    )
    def test_notification_failure_does_not_rollback_status(
        self,
        notify_mock,
    ):
        manager, result = self.create_manager_assignment()

        with self.captureOnCommitCallbacks(execute=True):
            completed = transition_task_status(
                task=result.tasks[0],
                actor=self.user,
                new_status=TaskStatus.COMPLETED,
            )

        completed.refresh_from_db()

        self.assertEqual(
            completed.status,
            TaskStatus.COMPLETED,
        )

        notify_mock.assert_called_once()
