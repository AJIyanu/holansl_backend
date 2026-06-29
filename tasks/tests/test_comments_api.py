from django.test import override_settings
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from tasks.constants import TaskAssignmentType
from tasks.models import TaskComment
from tasks.services.assignments import (
    create_task_assignment,
)
from tasks.services.comments import (
    add_task_comment,
)

from .base import TaskModelTestCase


@override_settings(TASK_ASSIGNMENT_NOTIFICATIONS_ENABLED=False)
class TaskCommentApiTests(TaskModelTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def create_manager_assignment(self):
        manager = self.create_staff(
            username="comment-api-manager",
            department=self.department,
            job_title="Manager",
        )

        self.create_department_leader(profile=manager)

        for codename in [
            "assign_task",
            "view_department_task",
            "manage_department_task",
            "moderate_taskcomment",
        ]:
            self.grant_permission(
                manager.user,
                codename,
            )

        result = create_task_assignment(
            creator=manager.user,
            assignment_type=(TaskAssignmentType.USERS),
            title="Comment API task",
            user_ids=[self.user.id],
        )

        return manager, result

    def test_assignee_can_add_comment(self):
        _manager, result = self.create_manager_assignment()

        self.authenticate(self.user)

        response = self.client.post(
            reverse(
                "task-comments",
                args=[result.tasks[0].id],
            ),
            {"body": ("I have started working on this.")},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )

        self.assertEqual(
            response.data["body"],
            "I have started working on this.",
        )

    def test_user_can_list_task_comments(self):
        task = self.create_personal_task()

        add_task_comment(
            task=task,
            actor=self.user,
            body="First comment.",
        )

        add_task_comment(
            task=task,
            actor=self.user,
            body="Second comment.",
        )

        self.authenticate(self.user)

        response = self.client.get(
            reverse(
                "task-comments",
                args=[task.id],
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["count"],
            2,
        )

    def test_author_can_patch_comment(self):
        task = self.create_personal_task()

        comment = add_task_comment(
            task=task,
            actor=self.user,
            body="Original.",
        )

        self.authenticate(self.user)

        response = self.client.patch(
            reverse(
                "task-comment-detail",
                args=[task.id, comment.id],
            ),
            {
                "body": "Updated.",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["body"],
            "Updated.",
        )

    def test_other_user_cannot_patch_comment(
        self,
    ):
        task = self.create_personal_task()

        comment = add_task_comment(
            task=task,
            actor=self.user,
            body="Original.",
        )

        self.authenticate(self.other_user)

        response = self.client.patch(
            reverse(
                "task-comment-detail",
                args=[task.id, comment.id],
            ),
            {
                "body": "Unauthorised.",
            },
            format="json",
        )

        # The other user cannot see the personal task,
        # so the protected queryset returns 404.
        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_author_can_remove_comment(self):
        task = self.create_personal_task()

        comment = add_task_comment(
            task=task,
            actor=self.user,
            body="Remove this.",
        )

        self.authenticate(self.user)

        response = self.client.post(
            reverse(
                "task-comment-remove",
                args=[task.id, comment.id],
            ),
            {
                "reason": "Added by mistake.",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertTrue(response.data["is_removed"])

        self.assertEqual(
            response.data["body"],
            "[Comment removed]",
        )

        self.assertTrue(TaskComment.objects.filter(pk=comment.id).exists())

    def test_manager_can_remove_assignee_comment(
        self,
    ):
        manager, result = self.create_manager_assignment()

        comment = add_task_comment(
            task=result.tasks[0],
            actor=self.user,
            body="Incorrect information.",
        )

        self.authenticate(manager.user)

        response = self.client.post(
            reverse(
                "task-comment-remove",
                args=[
                    result.tasks[0].id,
                    comment.id,
                ],
            ),
            {"reason": ("Contains incorrect information.")},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertTrue(response.data["is_removed"])

    def test_task_activity_endpoint(self):
        task = self.create_personal_task()

        add_task_comment(
            task=task,
            actor=self.user,
            body="Activity comment.",
        )

        self.authenticate(self.user)

        response = self.client.get(
            reverse(
                "task-activity",
                args=[task.id],
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        activity_types = {row["activity_type"] for row in response.data["results"]}

        self.assertIn(
            "COMMENT_ADDED",
            activity_types,
        )

    def test_batch_activity_endpoint(self):
        manager, result = self.create_manager_assignment()

        add_task_comment(
            task=result.tasks[0],
            actor=self.user,
            body="Batch activity comment.",
        )

        self.authenticate(manager.user)

        response = self.client.get(
            reverse(
                "task-batch-activity",
                args=[result.batch.id],
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        activity_types = {row["activity_type"] for row in response.data["results"]}

        self.assertIn(
            "BATCH_CREATED",
            activity_types,
        )

        self.assertIn(
            "COMMENT_ADDED",
            activity_types,
        )

    def test_invisible_task_activity_returns_404(
        self,
    ):
        other_result = create_task_assignment(
            creator=self.other_user,
            assignment_type=(TaskAssignmentType.PERSONAL),
            title="Private activity",
        )

        self.authenticate(self.user)

        response = self.client.get(
            reverse(
                "task-activity",
                args=[other_result.tasks[0].id],
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )
