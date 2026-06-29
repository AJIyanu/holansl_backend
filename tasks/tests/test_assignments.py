from unittest.mock import patch

from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.test import override_settings
from django.utils import timezone

from accounts.models import AuditLog

from notifications.constants import (
    NotificationChannel,
    NotificationEventMode,
)

from tasks.constants import (
    TaskActivityType,
    TaskAssignmentType,
    TaskPriority,
)
from tasks.models import (
    Task,
    TaskActivity,
    TaskBatch,
)
from tasks.services.assignments import (
    create_task_assignment,
)

from .base import TaskModelTestCase


@override_settings(
    TASK_ASSIGNMENT_NOTIFICATIONS_ENABLED=True,
    TASK_ASSIGNMENT_NOTIFICATION_CHANNELS=[
        "DASHBOARD",
        "EMAIL",
    ],
    TASK_ASSIGNMENT_NOTIFICATION_EVENT_MODE=("SHARED"),
)
class TaskAssignmentServiceTests(TaskModelTestCase):
    def create_manager(self):
        profile = self.create_staff(
            username="task-manager",
            department=self.department,
            job_title="Technical Manager",
        )

        self.create_department_leader(profile=profile)

        self.grant_permission(
            profile.user,
            "assign_task",
        )

        self.grant_permission(
            profile.user,
            "assign_department_task",
        )

        self.grant_permission(
            profile.user,
            "view_department_task",
        )

        self.grant_permission(
            profile.user,
            "manage_department_task",
        )

        return profile

    @patch("tasks.services.assignments.notify")
    def test_personal_task_creates_one_task_without_assignment_notification(
        self,
        notify_mock,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            result = create_task_assignment(
                creator=self.user,
                assignment_type=(TaskAssignmentType.PERSONAL),
                title="Prepare my report",
                description="Complete before Friday.",
                priority=TaskPriority.MEDIUM,
                due_at=(timezone.now() + timezone.timedelta(days=2)),
            )

        self.assertEqual(
            result.recipient_count,
            1,
        )

        self.assertEqual(
            result.tasks[0].assigned_to,
            self.user,
        )

        self.assertFalse(result.notification_scheduled)

        notify_mock.assert_not_called()

    @patch("tasks.services.assignments.notify")
    def test_selected_users_receive_individual_tasks(
        self,
        notify_mock,
    ):
        manager_profile = self.create_manager()

        with self.captureOnCommitCallbacks(execute=True):
            result = create_task_assignment(
                creator=manager_profile.user,
                assignment_type=(TaskAssignmentType.USERS),
                title="Review system records",
                user_ids=[
                    self.user.id,
                    self.other_user.id,
                    self.user.id,
                ],
            )

        self.assertEqual(
            result.recipient_count,
            2,
        )

        self.assertEqual(
            TaskBatch.objects.count(),
            1,
        )

        self.assertEqual(
            Task.objects.count(),
            2,
        )

        self.assertEqual(
            {task.assigned_to_id for task in result.tasks},
            {
                self.user.id,
                self.other_user.id,
            },
        )

        notify_mock.assert_called_once()

        kwargs = notify_mock.call_args.kwargs

        self.assertEqual(
            kwargs["event_mode"],
            NotificationEventMode.SHARED,
        )

        self.assertEqual(
            kwargs["channels"],
            [
                NotificationChannel.DASHBOARD,
                NotificationChannel.EMAIL,
            ],
        )

        self.assertEqual(
            len(kwargs["recipients"]),
            2,
        )

    @patch("tasks.services.assignments.notify")
    def test_individual_notification_mode_is_supported(
        self,
        notify_mock,
    ):
        manager_profile = self.create_manager()

        with self.captureOnCommitCallbacks(execute=True):
            create_task_assignment(
                creator=manager_profile.user,
                assignment_type=(TaskAssignmentType.USERS),
                title="Individual notifications",
                user_ids=[
                    self.user.id,
                    self.other_user.id,
                ],
                notification_event_mode=(NotificationEventMode.INDIVIDUAL),
            )

        kwargs = notify_mock.call_args.kwargs

        self.assertEqual(
            kwargs["event_mode"],
            NotificationEventMode.INDIVIDUAL,
        )

    @patch("tasks.services.assignments.notify")
    def test_department_assignment_creates_task_for_each_active_staff_member(
        self,
        notify_mock,
    ):
        manager_profile = self.create_manager()

        inactive_profile = self.create_staff(
            username="inactive-staff",
            department=self.department,
            is_active=False,
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = create_task_assignment(
                creator=manager_profile.user,
                assignment_type=(TaskAssignmentType.DEPARTMENT),
                title="Department compliance check",
                department=self.department,
                include_assigner=False,
            )

        recipient_ids = {task.assigned_to_id for task in result.tasks}

        self.assertIn(
            self.user.id,
            recipient_ids,
        )

        self.assertIn(
            self.other_user.id,
            recipient_ids,
        )

        self.assertNotIn(
            manager_profile.user.id,
            recipient_ids,
        )

        self.assertNotIn(
            inactive_profile.user.id,
            recipient_ids,
        )

        self.assertEqual(
            result.batch.source_department,
            self.department,
        )

        notify_mock.assert_called_once()

    def test_manager_cannot_assign_outside_scope(
        self,
    ):
        manager_profile = self.create_manager()

        outside_profile = self.create_staff(
            username="outside-staff",
            department=self.other_department,
        )

        with self.assertRaises(PermissionDenied):
            create_task_assignment(
                creator=manager_profile.user,
                assignment_type=(TaskAssignmentType.USERS),
                title="Unauthorised task",
                user_ids=[outside_profile.user.id],
            )

    def test_inactive_selected_user_is_rejected(
        self,
    ):
        manager_profile = self.create_manager()

        inactive_profile = self.create_staff(
            username="inactive-selected",
            department=self.department,
            is_active=False,
        )

        with self.assertRaises(ValidationError):
            create_task_assignment(
                creator=manager_profile.user,
                assignment_type=(TaskAssignmentType.USERS),
                title="Invalid task",
                user_ids=[inactive_profile.user.id],
            )

    def test_reporting_manager_can_assign_to_direct_report(
        self,
    ):
        manager_profile = self.create_staff(
            username="line-manager",
            department=self.other_department,
        )

        self.user_profile.reports_to = manager_profile

        self.user_profile.save(update_fields=["reports_to"])

        self.grant_permission(
            manager_profile.user,
            "assign_task",
        )

        result = create_task_assignment(
            creator=manager_profile.user,
            assignment_type=(TaskAssignmentType.USERS),
            title="Reporting-line task",
            user_ids=[self.user.id],
        )

        self.assertEqual(
            result.tasks[0].assigned_to,
            self.user,
        )

    def test_organisation_permission_can_assign_any_staff(
        self,
    ):
        executive_profile = self.create_staff(
            username="organisation-executive",
            department=self.other_department,
        )

        self.grant_permission(
            executive_profile.user,
            "assign_all_task",
        )

        result = create_task_assignment(
            creator=executive_profile.user,
            assignment_type=(TaskAssignmentType.USERS),
            title="Organisation task",
            user_ids=[
                self.user.id,
                self.other_user.id,
            ],
        )

        self.assertEqual(
            result.recipient_count,
            2,
        )

    def test_assignment_creates_activity_records(
        self,
    ):
        manager_profile = self.create_manager()

        result = create_task_assignment(
            creator=manager_profile.user,
            assignment_type=(TaskAssignmentType.USERS),
            title="Activity test",
            user_ids=[
                self.user.id,
                self.other_user.id,
            ],
        )

        self.assertTrue(
            TaskActivity.objects.filter(
                batch=result.batch,
                activity_type=(TaskActivityType.BATCH_CREATED),
            ).exists()
        )

        self.assertEqual(
            TaskActivity.objects.filter(
                batch=result.batch,
                activity_type=(TaskActivityType.TASK_ASSIGNED),
            ).count(),
            2,
        )

    def test_assignment_creates_global_audit_log(
        self,
    ):
        manager_profile = self.create_manager()

        result = create_task_assignment(
            creator=manager_profile.user,
            assignment_type=(TaskAssignmentType.USERS),
            title="Audit test",
            user_ids=[self.user.id],
        )

        audit = AuditLog.objects.get(
            app_label="tasks",
            resource="task_batch",
            object_id=str(result.batch.id),
        )

        self.assertEqual(
            audit.user,
            manager_profile.user,
        )

        self.assertEqual(
            audit.event_type,
            AuditLog.EventType.CREATE,
        )

        self.assertEqual(
            audit.metadata["recipient_count"],
            1,
        )

    @patch(
        "tasks.services.assignments.notify",
        side_effect=RuntimeError("Notification test failure"),
    )
    def test_notification_failure_does_not_remove_created_tasks(
        self,
        notify_mock,
    ):
        manager_profile = self.create_manager()

        with self.captureOnCommitCallbacks(execute=True):
            result = create_task_assignment(
                creator=manager_profile.user,
                assignment_type=(TaskAssignmentType.USERS),
                title="Notification failure test",
                user_ids=[self.user.id],
            )

        self.assertTrue(TaskBatch.objects.filter(pk=result.batch.pk).exists())

        self.assertTrue(Task.objects.filter(batch=result.batch).exists())

        notify_mock.assert_called_once()
