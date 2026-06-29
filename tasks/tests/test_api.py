from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from django.test import override_settings

from rest_framework import status
from rest_framework.test import APIClient

from tasks.constants import (
    TaskAssignmentType,
    TaskPriority,
    TaskStatus,
)
from tasks.services.assignments import (
    create_task_assignment,
)

from .base import TaskModelTestCase


@override_settings(TASK_ASSIGNMENT_NOTIFICATIONS_ENABLED=False)
class TaskApiTests(TaskModelTestCase):
    def setUp(self):
        super().setUp()

        self.client = APIClient()

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def create_manager(self):
        profile = self.create_staff(
            username="api-manager",
            department=self.department,
            job_title="Technical Manager",
        )

        self.create_department_leader(profile=profile)

        for codename in [
            "assign_task",
            "assign_department_task",
            "view_department_task",
            "manage_department_task",
        ]:
            self.grant_permission(
                profile.user,
                codename,
            )

        return profile

    def test_active_staff_can_create_personal_task(
        self,
    ):
        self.authenticate(self.user)

        response = self.client.post(
            reverse("task-list"),
            {
                "title": "Prepare weekly report",
                "description": ("Prepare the weekly report."),
                "priority": "MEDIUM",
                "due_at": (timezone.now() + timedelta(days=2)).isoformat(),
                "assignment": {"type": "PERSONAL"},
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )

        self.assertEqual(
            response.data["recipient_count"],
            1,
        )

        self.assertFalse(response.data["notification_scheduled"])

        self.assertEqual(
            response.data["tasks"][0]["assigned_to"]["id"],
            str(self.user.id),
        )

    def test_ordinary_staff_cannot_assign_colleague(
        self,
    ):
        self.authenticate(self.user)

        response = self.client.post(
            reverse("task-list"),
            {
                "title": "Unauthorised task",
                "assignment": {
                    "type": "USERS",
                    "user_ids": [str(self.other_user.id)],
                },
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_manager_can_assign_selected_staff(
        self,
    ):
        manager = self.create_manager()

        self.authenticate(manager.user)

        response = self.client.post(
            reverse("task-list"),
            {
                "title": "Review technical records",
                "priority": "HIGH",
                "assignment": {
                    "type": "USERS",
                    "user_ids": [
                        str(self.user.id),
                        str(self.other_user.id),
                    ],
                },
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )

        self.assertEqual(
            response.data["recipient_count"],
            2,
        )

        self.assertEqual(
            len(response.data["tasks"]),
            2,
        )

    def test_manager_can_assign_department(
        self,
    ):
        manager = self.create_manager()

        self.authenticate(manager.user)

        response = self.client.post(
            reverse("task-list"),
            {
                "title": ("Complete department report"),
                "assignment": {
                    "type": "DEPARTMENT",
                    "department_id": (str(self.department.id)),
                    "include_assigner": False,
                },
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )

        recipient_ids = {task["assigned_to"]["id"] for task in response.data["tasks"]}

        self.assertIn(
            str(self.user.id),
            recipient_ids,
        )

        self.assertIn(
            str(self.other_user.id),
            recipient_ids,
        )

        self.assertNotIn(
            str(manager.user.id),
            recipient_ids,
        )

    def test_task_list_is_row_level_scoped(
        self,
    ):
        own_task = self.create_personal_task()

        other_result = create_task_assignment(
            creator=self.other_user,
            assignment_type=(TaskAssignmentType.PERSONAL),
            title="Other user's task",
        )

        self.authenticate(self.user)

        response = self.client.get(reverse("task-list"))

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        returned_ids = {row["id"] for row in response.data["results"]}

        self.assertIn(
            str(own_task.id),
            returned_ids,
        )

        self.assertNotIn(
            str(other_result.tasks[0].id),
            returned_ids,
        )

    def test_scope_my_returns_only_assigned_tasks(
        self,
    ):
        manager = self.create_manager()

        self.user_profile.reports_to = manager
        self.user_profile.save(update_fields=["reports_to"])

        self.grant_permission(
            self.user,
            "assign_task",
        )

        create_task_assignment(
            creator=manager.user,
            assignment_type=(TaskAssignmentType.USERS),
            title="Assigned by manager",
            user_ids=[self.user.id],
        )

        create_task_assignment(
            creator=self.user,
            assignment_type=(TaskAssignmentType.USERS),
            title="Created for manager",
            user_ids=[manager.user.id],
        )

        self.authenticate(self.user)

        response = self.client.get(
            reverse("task-list"),
            {
                "scope": "my",
            },
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertTrue(
            all(
                row["assigned_to"]["id"] == str(self.user.id)
                for row in response.data["results"]
            )
        )

    def test_scope_all_requires_permission(self):
        self.authenticate(self.user)

        response = self.client.get(
            reverse("task-list"),
            {
                "scope": "all",
            },
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_manager_department_scope_returns_managed_tasks(
        self,
    ):
        manager = self.create_manager()

        task = self.create_personal_task()

        outside_profile = self.create_staff(
            username="outside-api-staff",
            department=self.other_department,
        )

        outside_result = create_task_assignment(
            creator=outside_profile.user,
            assignment_type=(TaskAssignmentType.PERSONAL),
            title="Outside task",
        )

        self.authenticate(manager.user)

        response = self.client.get(
            reverse("task-list"),
            {
                "scope": "department",
            },
        )

        returned_ids = {row["id"] for row in response.data["results"]}

        self.assertIn(
            str(task.id),
            returned_ids,
        )

        self.assertNotIn(
            str(outside_result.tasks[0].id),
            returned_ids,
        )

    def test_archived_tasks_are_hidden_by_default(
        self,
    ):
        task = self.create_personal_task()

        task.status = TaskStatus.COMPLETED
        task.completed_at = timezone.now()
        task.archived_at = timezone.now()

        task.save(
            update_fields=[
                "status",
                "completed_at",
                "archived_at",
                "updated_at",
            ]
        )

        self.authenticate(self.user)

        default_response = self.client.get(reverse("task-list"))

        default_ids = {row["id"] for row in default_response.data["results"]}

        self.assertNotIn(
            str(task.id),
            default_ids,
        )

        archived_response = self.client.get(
            reverse("task-list"),
            {
                "archived": "true",
            },
        )

        archived_ids = {row["id"] for row in archived_response.data["results"]}

        self.assertIn(
            str(task.id),
            archived_ids,
        )

    def test_task_filters_search_and_ordering(
        self,
    ):
        first = create_task_assignment(
            creator=self.user,
            assignment_type=(TaskAssignmentType.PERSONAL),
            title="Alpha report",
            priority=TaskPriority.LOW,
            due_at=(timezone.now() + timedelta(days=3)),
        )

        second = create_task_assignment(
            creator=self.user,
            assignment_type=(TaskAssignmentType.PERSONAL),
            title="Beta urgent report",
            priority=TaskPriority.URGENT,
            due_at=(timezone.now() + timedelta(days=1)),
        )

        self.authenticate(self.user)

        response = self.client.get(
            reverse("task-list"),
            {
                "search": "urgent",
                "priority": "URGENT",
                "ordering": "due_at",
            },
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["count"],
            1,
        )

        self.assertEqual(
            response.data["results"][0]["id"],
            str(second.tasks[0].id),
        )

        self.assertNotEqual(
            first.tasks[0].id,
            second.tasks[0].id,
        )

    def test_user_cannot_retrieve_invisible_task(
        self,
    ):
        other_result = create_task_assignment(
            creator=self.other_user,
            assignment_type=(TaskAssignmentType.PERSONAL),
            title="Private task",
        )

        self.authenticate(self.user)

        response = self.client.get(
            reverse(
                "task-detail",
                args=[other_result.tasks[0].id],
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_task_detail_contains_action_permissions(
        self,
    ):
        task = self.create_personal_task()

        self.authenticate(self.user)

        response = self.client.get(
            reverse(
                "task-detail",
                args=[task.id],
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertTrue(response.data["permissions"]["can_update_status"])

        self.assertTrue(response.data["permissions"]["can_comment"])

        self.assertTrue(response.data["can_set_reminder"])

    def test_batch_progress_visible_to_creator(
        self,
    ):
        manager = self.create_manager()

        result = create_task_assignment(
            creator=manager.user,
            assignment_type=(TaskAssignmentType.USERS),
            title="Progress test",
            user_ids=[
                self.user.id,
                self.other_user.id,
            ],
        )

        self.authenticate(manager.user)

        response = self.client.get(
            reverse(
                "task-batch-detail",
                args=[result.batch.id],
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["progress"]["total"],
            2,
        )

        self.assertEqual(
            response.data["progress"]["to_do"],
            2,
        )

    def test_batch_progress_hidden_from_ordinary_assignee(
        self,
    ):
        manager = self.create_manager()

        result = create_task_assignment(
            creator=manager.user,
            assignment_type=(TaskAssignmentType.USERS),
            title="Private progress test",
            user_ids=[
                self.user.id,
                self.other_user.id,
            ],
        )

        self.authenticate(self.user)

        response = self.client.get(
            reverse(
                "task-batch-detail",
                args=[result.batch.id],
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertIsNone(response.data["progress"])
