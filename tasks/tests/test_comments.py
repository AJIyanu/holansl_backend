from unittest.mock import patch

from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.test import override_settings

from tasks.constants import (
    TaskActivityType,
    TaskAssignmentType,
)
from tasks.models import (
    TaskActivity,
    TaskComment,
)
from tasks.services.assignments import (
    create_task_assignment,
)
from tasks.services.comments import (
    add_task_comment,
    edit_task_comment,
    remove_task_comment,
)

from .base import TaskModelTestCase


@override_settings(
    TASK_ASSIGNMENT_NOTIFICATIONS_ENABLED=False,
    TASK_LIFECYCLE_NOTIFICATION_CHANNELS=["DASHBOARD"],
)
class TaskCommentServiceTests(TaskModelTestCase):
    def create_manager_assignment(self):
        manager = self.create_staff(
            username="comment-manager",
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
            title="Comment test task",
            user_ids=[self.user.id],
        )

        return manager, result

    def test_assignee_can_add_comment(self):
        _manager, result = self.create_manager_assignment()

        comment = add_task_comment(
            task=result.tasks[0],
            actor=self.user,
            body="I have started this task.",
        )

        self.assertEqual(
            comment.author,
            self.user,
        )

        self.assertEqual(
            comment.task,
            result.tasks[0],
        )

        self.assertTrue(
            TaskActivity.objects.filter(
                task=result.tasks[0],
                activity_type=(TaskActivityType.COMMENT_ADDED),
            ).exists()
        )

    def test_invisible_user_cannot_comment(self):
        _manager, result = self.create_manager_assignment()

        outside_profile = self.create_staff(
            username="outside-comment-user",
            department=self.other_department,
        )

        with self.assertRaises(PermissionDenied):
            add_task_comment(
                task=result.tasks[0],
                actor=outside_profile.user,
                body="Unauthorised comment.",
            )

    def test_comment_author_can_edit(self):
        task = self.create_personal_task()

        comment = add_task_comment(
            task=task,
            actor=self.user,
            body="Original comment.",
        )

        updated = edit_task_comment(
            comment=comment,
            actor=self.user,
            body="Updated comment.",
        )

        self.assertEqual(
            updated.body,
            "Updated comment.",
        )

        self.assertIsNotNone(updated.edited_at)

    def test_other_user_cannot_edit_comment(self):
        task = self.create_personal_task()

        comment = add_task_comment(
            task=task,
            actor=self.user,
            body="Original comment.",
        )

        with self.assertRaises(PermissionDenied):
            edit_task_comment(
                comment=comment,
                actor=self.other_user,
                body="Unauthorised update.",
            )

    def test_author_can_remove_own_comment(self):
        task = self.create_personal_task()

        comment = add_task_comment(
            task=task,
            actor=self.user,
            body="Comment to remove.",
        )

        removed = remove_task_comment(
            comment=comment,
            actor=self.user,
            reason="Added in error.",
        )

        self.assertIsNotNone(removed.removed_at)

        self.assertEqual(
            removed.removed_by,
            self.user,
        )

        self.assertTrue(TaskComment.objects.filter(pk=comment.pk).exists())

    def test_manager_can_remove_assignee_comment(
        self,
    ):
        manager, result = self.create_manager_assignment()

        comment = add_task_comment(
            task=result.tasks[0],
            actor=self.user,
            body="Comment requiring moderation.",
        )

        removed = remove_task_comment(
            comment=comment,
            actor=manager.user,
            reason="Contains incorrect information.",
        )

        self.assertIsNotNone(removed.removed_at)

        self.assertEqual(
            removed.removed_by,
            manager.user,
        )

    def test_removed_comment_cannot_be_edited(self):
        task = self.create_personal_task()

        comment = add_task_comment(
            task=task,
            actor=self.user,
            body="Comment.",
        )

        remove_task_comment(
            comment=comment,
            actor=self.user,
            reason="Added in error.",
        )

        with self.assertRaises(PermissionDenied):
            edit_task_comment(
                comment=comment,
                actor=self.user,
                body="Attempted edit.",
            )

    def test_removal_requires_reason(self):
        task = self.create_personal_task()

        comment = add_task_comment(
            task=task,
            actor=self.user,
            body="Comment.",
        )

        with self.assertRaises(ValidationError):
            remove_task_comment(
                comment=comment,
                actor=self.user,
                reason="",
            )

    @patch(
        "tasks.services.task_notifications.notify",
        side_effect=RuntimeError("Notification failure"),
    )
    def test_notification_failure_does_not_remove_comment(
        self,
        notify_mock,
    ):
        manager, result = self.create_manager_assignment()

        with self.captureOnCommitCallbacks(execute=True):
            comment = add_task_comment(
                task=result.tasks[0],
                actor=self.user,
                body="Saved despite notification failure.",
            )

        self.assertTrue(TaskComment.objects.filter(pk=comment.pk).exists())

        notify_mock.assert_called_once()
