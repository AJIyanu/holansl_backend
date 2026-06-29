from django.test import override_settings
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from tasks.constants import (
    TaskAssignmentType,
    TaskStatus,
)
from tasks.services.assignments import (
    create_task_assignment,
)

from .base import TaskModelTestCase


@override_settings(TASK_ASSIGNMENT_NOTIFICATIONS_ENABLED=False)
class TaskLifecycleApiTests(TaskModelTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def create_manager_assignment(self):
        manager = self.create_staff(
            username="lifecycle-api-manager",
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
            title="Lifecycle API task",
            user_ids=[self.user.id],
        )

        return manager, result

    def test_assignee_can_update_status(self):
        _manager, result = self.create_manager_assignment()

        self.authenticate(self.user)

        response = self.client.post(
            reverse(
                "task-set-status",
                args=[result.tasks[0].id],
            ),
            {
                "status": "IN_PROGRESS",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["status"],
            "IN_PROGRESS",
        )

    def test_personal_owner_can_cancel_task(self):
        task = self.create_personal_task()

        self.authenticate(self.user)

        response = self.client.post(
            reverse(
                "task-cancel",
                args=[task.id],
            ),
            {
                "reason": "No longer required.",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["status"],
            TaskStatus.CANCELLED,
        )

    def test_assignee_cannot_cancel_assigned_task(
        self,
    ):
        _manager, result = self.create_manager_assignment()

        self.authenticate(self.user)

        response = self.client.post(
            reverse(
                "task-cancel",
                args=[result.tasks[0].id],
            ),
            {
                "reason": "Not interested.",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_completed_task_can_be_archived_and_restored(
        self,
    ):
        task = self.create_personal_task()

        self.authenticate(self.user)

        self.client.post(
            reverse(
                "task-set-status",
                args=[task.id],
            ),
            {
                "status": "COMPLETED",
            },
            format="json",
        )

        archive_response = self.client.post(
            reverse(
                "task-archive",
                args=[task.id],
            ),
            format="json",
        )

        self.assertEqual(
            archive_response.status_code,
            status.HTTP_200_OK,
        )

        self.assertIsNotNone(archive_response.data["archived_at"])

        restore_response = self.client.post(
            reverse(
                "task-restore",
                args=[task.id],
            ),
            format="json",
        )

        self.assertEqual(
            restore_response.status_code,
            status.HTTP_200_OK,
        )

        self.assertIsNone(restore_response.data["archived_at"])

    def test_creator_can_patch_batch_details(self):
        manager, result = self.create_manager_assignment()

        self.authenticate(manager.user)

        response = self.client.patch(
            reverse(
                "task-batch-detail",
                args=[result.batch.id],
            ),
            {
                "title": "Updated API task",
                "priority": "URGENT",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["title"],
            "Updated API task",
        )

        self.assertEqual(
            response.data["priority"],
            "URGENT",
        )

    def test_manager_can_cancel_batch(self):
        manager, result = self.create_manager_assignment()

        self.authenticate(manager.user)

        response = self.client.post(
            reverse(
                "task-batch-cancel",
                args=[result.batch.id],
            ),
            {"reason": ("The assignment was withdrawn.")},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["affected_task_count"],
            1,
        )

        result.tasks[0].refresh_from_db()

        self.assertEqual(
            result.tasks[0].status,
            TaskStatus.CANCELLED,
        )

    def test_active_batch_cannot_be_archived(self):
        manager, result = self.create_manager_assignment()

        self.authenticate(manager.user)

        response = self.client.post(
            reverse(
                "task-batch-archive",
                args=[result.batch.id],
            ),
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
