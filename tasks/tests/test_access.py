from django.core.exceptions import (
    PermissionDenied,
)

from tasks.constants import (
    TaskAssignmentType,
)
from tasks.models import Task, TaskBatch
from tasks.services.access import (
    can_assign_to_department,
    can_assign_to_user,
    get_reporting_descendant_user_ids,
    visible_tasks_for,
)

from .base import TaskModelTestCase


class TaskAccessTests(TaskModelTestCase):
    def create_assigned_task(
        self,
        *,
        creator,
        assignee_profile,
    ):
        batch = TaskBatch.objects.create(
            title="Assigned task",
            assignment_type=(TaskAssignmentType.USERS),
            created_by=creator,
            created_by_name=(creator.get_full_name() or creator.username),
            created_by_email=creator.email,
        )

        return Task.objects.create(
            batch=batch,
            assigned_to=assignee_profile.user,
            assignee_name=(
                assignee_profile.user.get_full_name() or assignee_profile.user.username
            ),
            assignee_email=(assignee_profile.user.email),
            assignee_employee_id=(assignee_profile.employee_id),
            assigned_department=(assignee_profile.department),
            assigned_department_name=(
                assignee_profile.department.name if assignee_profile.department else ""
            ),
            assigned_department_code=(
                assignee_profile.department.code if assignee_profile.department else ""
            ),
        )

    def test_owner_can_see_own_personal_task(self):
        task = self.create_personal_task()

        queryset = visible_tasks_for(self.user)

        self.assertTrue(queryset.filter(pk=task.pk).exists())

    def test_colleague_cannot_see_another_personal_task(
        self,
    ):
        task = self.create_personal_task()

        queryset = visible_tasks_for(self.other_user)

        self.assertFalse(queryset.filter(pk=task.pk).exists())

    def test_department_leader_can_see_department_staff_task(
        self,
    ):
        manager_profile = self.create_staff(
            username="department-manager",
            department=self.department,
            job_title="Department Manager",
        )

        self.create_department_leader(profile=manager_profile)

        self.grant_permission(
            manager_profile.user,
            "view_department_task",
        )

        task = self.create_personal_task()

        queryset = visible_tasks_for(manager_profile.user)

        self.assertTrue(queryset.filter(pk=task.pk).exists())

    def test_reporting_manager_can_see_descendant_task(
        self,
    ):
        manager_profile = self.create_staff(
            username="line-manager",
            department=self.other_department,
            job_title="Line Manager",
        )

        self.user_profile.reports_to = manager_profile

        self.user_profile.save(update_fields=["reports_to"])

        self.grant_permission(
            manager_profile.user,
            "view_department_task",
        )

        task = self.create_personal_task()

        queryset = visible_tasks_for(manager_profile.user)

        self.assertTrue(queryset.filter(pk=task.pk).exists())

    def test_reporting_scope_includes_indirect_reports(
        self,
    ):
        senior_profile = self.create_staff(
            username="senior-manager",
            department=self.department,
        )

        middle_profile = self.create_staff(
            username="middle-manager",
            department=self.department,
            reports_to=senior_profile,
        )

        junior_profile = self.create_staff(
            username="junior-staff",
            department=self.department,
            reports_to=middle_profile,
        )

        descendant_ids = get_reporting_descendant_user_ids(senior_profile.user)

        self.assertIn(
            middle_profile.user_id,
            descendant_ids,
        )

        self.assertIn(
            junior_profile.user_id,
            descendant_ids,
        )

    def test_all_task_permission_can_see_everything(
        self,
    ):
        executive_profile = self.create_staff(
            username="executive",
            department=self.other_department,
        )

        self.grant_permission(
            executive_profile.user,
            "view_all_task",
        )

        task = self.create_personal_task()

        queryset = visible_tasks_for(executive_profile.user)

        self.assertTrue(queryset.filter(pk=task.pk).exists())

    def test_scope_all_requires_permission(self):
        with self.assertRaises(PermissionDenied):
            visible_tasks_for(
                self.other_user,
                scope="all",
            )

    def test_department_leader_can_assign_to_department(
        self,
    ):
        manager_profile = self.create_staff(
            username="assigning-manager",
            department=self.department,
        )

        self.create_department_leader(profile=manager_profile)

        self.grant_permission(
            manager_profile.user,
            "assign_department_task",
        )

        self.assertTrue(
            can_assign_to_department(
                manager_profile.user,
                self.department,
            )
        )

        self.assertFalse(
            can_assign_to_department(
                manager_profile.user,
                self.other_department,
            )
        )

    def test_reporting_manager_can_assign_to_report(
        self,
    ):
        manager_profile = self.create_staff(
            username="reporting-manager",
            department=self.other_department,
        )

        self.user_profile.reports_to = manager_profile

        self.user_profile.save(update_fields=["reports_to"])

        self.grant_permission(
            manager_profile.user,
            "assign_task",
        )

        self.assertTrue(
            can_assign_to_user(
                manager_profile.user,
                self.user,
            )
        )

        self.assertFalse(
            can_assign_to_user(
                manager_profile.user,
                self.other_user,
            )
        )
