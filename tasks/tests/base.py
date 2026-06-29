from datetime import timedelta

from django.contrib.auth import (
    get_user_model,
)
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone

from accounts.models import (
    Department,
    DepartmentLeadership,
    StaffProfile,
)

from tasks.constants import (
    TaskAssignmentType,
    TaskPriority,
)
from tasks.models import Task, TaskBatch


User = get_user_model()


class TaskModelTestCase(TestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name="Technical",
            code="TECH",
            description="Technical department",
        )

        self.other_department = Department.objects.create(
            name="Procurement",
            code="PROC",
            description=("Procurement department"),
        )

        self.user_profile = self.create_staff(
            username="task-user",
            department=self.department,
            job_title="Software Engineer",
        )

        self.user = self.user_profile.user

        self.other_profile = self.create_staff(
            username="other-user",
            department=self.department,
            job_title="IT Technician",
        )

        self.other_user = self.other_profile.user

    def create_staff(
        self,
        *,
        username,
        department,
        job_title="Staff",
        reports_to=None,
        is_active=True,
        is_staff=True,
        end_date=None,
    ):
        sequence = StaffProfile.objects.count() + 1

        user = User.objects.create_user(
            username=username,
            email=f"{username}@holansl.com",
            password="TestPassword123!",
            first_name=username.replace(
                "-",
                " ",
            ).title(),
            last_name="Test",
            is_active=is_active,
            is_staff=is_staff,
        )

        return StaffProfile.objects.create(
            user=user,
            department=department,
            reports_to=reports_to,
            job_title=job_title,
            employment_type=(StaffProfile.EmploymentType.FULL_TIME),
            start_date=(timezone.localdate() - timedelta(days=365)),
            end_date=end_date,
            phone_number=(f"+23480000{sequence:05d}"),
        )

    def grant_permission(
        self,
        user,
        codename,
        *,
        app_label="tasks",
    ):
        permission = Permission.objects.get(
            content_type__app_label=app_label,
            codename=codename,
        )

        user.user_permissions.add(permission)
        user.refresh_from_db()

        return permission

    def create_department_leader(
        self,
        *,
        profile,
        department=None,
        leadership_type=(DepartmentLeadership.LeadershipType.MANAGER),
        is_primary=True,
    ):
        return DepartmentLeadership.objects.create(
            department=(department or self.department),
            manager=profile,
            leadership_type=leadership_type,
            is_primary=is_primary,
            active_from=timezone.localdate(),
            created_by=profile.user,
        )

    def create_personal_batch(
        self,
        **overrides,
    ):
        values = {
            "title": "Prepare weekly report",
            "description": ("Prepare and review the weekly report."),
            "assignment_type": (TaskAssignmentType.PERSONAL),
            "priority": TaskPriority.MEDIUM,
            "start_at": timezone.now(),
            "due_at": (timezone.now() + timedelta(days=2)),
            "created_by": self.user,
            "created_by_name": (self.user.get_full_name() or self.user.username),
            "created_by_email": self.user.email,
        }

        values.update(overrides)

        return TaskBatch.objects.create(**values)

    def create_personal_task(
        self,
        **overrides,
    ):
        batch = overrides.pop("batch", None) or self.create_personal_batch()

        values = {
            "batch": batch,
            "assigned_to": self.user,
            "assignee_name": (self.user.get_full_name() or self.user.username),
            "assignee_email": self.user.email,
            "assignee_employee_id": self.user_profile.employee_id,
            "assigned_department": self.department,
            "assigned_department_name": self.department.name,
            "assigned_department_code": self.department.code,
        }

        values.update(overrides)

        return Task.objects.create(**values)
