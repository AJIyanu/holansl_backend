from datetime import timedelta

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import (
    Department,
    DepartmentLeadership,
    Role,
    StaffProfile,
    User,
)


class ReportingHierarchyTestCase(APITestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name="Technical",
            code="TECH",
            description="Technical operations.",
        )

        self.other_department = Department.objects.create(
            name="Procurement",
            code="PROC",
            description="Procurement operations.",
        )

        self.executive = self.create_user_with_profile(
            username="executive",
            department=self.department,
            job_title="Chief Executive Officer",
            phone_number="+2348000000001",
        )

        self.manager = self.create_user_with_profile(
            username="manager",
            department=self.department,
            job_title="Technical Manager",
            phone_number="+2348000000002",
        )

        self.staff = self.create_user_with_profile(
            username="staff",
            department=self.department,
            job_title="Software Engineer",
            phone_number="+2348000000003",
        )

        self.ordinary_user = self.create_user_with_profile(
            username="ordinary",
            department=self.other_department,
            job_title="Procurement Officer",
            phone_number="+2348000000004",
        )

        executive_role = Role.objects.create(name="CEO")
        self.executive.user.groups.add(executive_role)

        change_staff_permission = Permission.objects.get(
            content_type__app_label="accounts",
            codename="change_staffprofile",
        )

        self.executive.user.user_permissions.add(change_staff_permission)

    def create_user_with_profile(
        self,
        *,
        username,
        department,
        job_title,
        phone_number,
    ):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@holansl.com",
            password="TestPassword123!",
            first_name=username.title(),
            last_name="Test",
            is_active=True,
            is_staff=True,
        )

        profile = StaffProfile.objects.create(
            user=user,
            department=department,
            job_title=job_title,
            employment_type=(StaffProfile.EmploymentType.FULL_TIME),
            start_date=timezone.localdate() - timedelta(days=365),
            phone_number=phone_number,
        )

        profile.user = user
        return profile

    def test_staff_profile_can_have_reporting_manager(self):
        self.staff.reports_to = self.manager
        self.staff.full_clean()
        self.staff.save(update_fields=["reports_to"])

        self.staff.refresh_from_db()

        self.assertEqual(
            self.staff.reports_to,
            self.manager,
        )

        self.assertIn(
            self.staff,
            self.manager.direct_reports.all(),
        )

    def test_staff_cannot_report_to_self(self):
        self.staff.reports_to = self.staff

        with self.assertRaises(ValidationError):
            self.staff.full_clean()

    def test_reporting_relationship_cannot_form_cycle(self):
        self.staff.reports_to = self.manager
        self.staff.full_clean()
        self.staff.save(update_fields=["reports_to"])

        self.manager.reports_to = self.staff

        with self.assertRaises(ValidationError):
            self.manager.full_clean()

    def test_inactive_staff_cannot_be_reporting_manager(self):
        self.manager.user.is_active = False
        self.manager.user.save(update_fields=["is_active"])

        self.staff.reports_to = self.manager

        with self.assertRaises(ValidationError):
            self.staff.full_clean()

    def test_patch_profile_updates_reports_to(self):
        self.client.force_authenticate(self.executive.user)

        response = self.client.patch(
            reverse(
                "staffprofile-detail",
                args=[self.staff.id],
            ),
            {
                "reports_to": str(self.manager.id),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["reports_to"]["id"],
            str(self.manager.id),
        )

        self.staff.refresh_from_db()

        self.assertEqual(
            self.staff.reports_to_id,
            self.manager.id,
        )

    def test_current_user_response_contains_reports_to(self):
        self.staff.reports_to = self.manager
        self.staff.save(update_fields=["reports_to"])

        self.client.force_authenticate(self.staff.user)

        response = self.client.get(reverse("current-user"))

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["profile"]["reports_to"]["id"],
            str(self.manager.id),
        )

    def test_executive_can_create_department_leadership(self):
        self.client.force_authenticate(self.executive.user)

        response = self.client.post(
            reverse("department-leadership-list"),
            {
                "department": str(self.department.id),
                "manager": str(self.manager.id),
                "leadership_type": "MANAGER",
                "is_primary": True,
                "active_from": (timezone.localdate().isoformat()),
                "active_until": None,
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )

        self.assertEqual(
            response.data["department"]["id"],
            str(self.department.id),
        )

        self.assertEqual(
            response.data["manager"]["id"],
            str(self.manager.id),
        )

        self.assertTrue(response.data["is_primary"])

    def test_ordinary_staff_cannot_create_leadership(self):
        self.client.force_authenticate(self.ordinary_user.user)

        response = self.client.post(
            reverse("department-leadership-list"),
            {
                "department": str(self.department.id),
                "manager": str(self.manager.id),
                "leadership_type": "MANAGER",
                "is_primary": True,
                "active_from": (timezone.localdate().isoformat()),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_overlapping_primary_leaders_are_rejected(self):
        DepartmentLeadership.objects.create(
            department=self.department,
            manager=self.manager,
            leadership_type=(DepartmentLeadership.LeadershipType.MANAGER),
            is_primary=True,
            active_from=timezone.localdate(),
            created_by=self.executive.user,
        )

        second_leader = DepartmentLeadership(
            department=self.department,
            manager=self.executive,
            leadership_type=(DepartmentLeadership.LeadershipType.MANAGER),
            is_primary=True,
            active_from=timezone.localdate(),
            created_by=self.executive.user,
        )

        with self.assertRaises(ValidationError):
            second_leader.full_clean()

    def test_department_response_contains_active_leaders(self):
        DepartmentLeadership.objects.create(
            department=self.department,
            manager=self.manager,
            leadership_type=(DepartmentLeadership.LeadershipType.MANAGER),
            is_primary=True,
            active_from=timezone.localdate(),
            created_by=self.executive.user,
        )

        self.client.force_authenticate(self.executive.user)

        response = self.client.get(
            reverse(
                "department-detail",
                args=[self.department.id],
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            len(response.data["leaders"]),
            1,
        )

        self.assertEqual(
            response.data["leaders"][0]["manager"]["id"],
            str(self.manager.id),
        )
