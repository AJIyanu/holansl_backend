from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import (
    AuditLog,
    Department,
    DepartmentLeadership,
    Role,
    StaffProfile,
    User,
)


CEO_DATA = {
    "first_name": "Daniel",
    "last_name": "Adebayo",
    "department": "Management",
    "job_title": "Chief Executive Officer",
    "role": "CEO",
}


DEPARTMENT_STAFF = {
    "Technical": {
        "manager": {
            "first_name": "Michael",
            "last_name": "Okafor",
            "job_title": "Technical Manager",
        },
        "members": [
            {
                "first_name": "Samuel",
                "last_name": "Adeyemi",
                "job_title": "Software Engineer",
                "role": "Software Engineer",
            },
            {
                "first_name": "David",
                "last_name": "Eze",
                "job_title": "IT Technician",
                "role": "IT Technician",
            },
        ],
    },
    "Human Resource": {
        "manager": {
            "first_name": "Grace",
            "last_name": "Olawale",
            "job_title": "Human Resource Manager",
        },
        "members": [
            {
                "first_name": "Esther",
                "last_name": "Bello",
                "job_title": "HR Officer",
                "role": "Human Resource",
            },
            {
                "first_name": "Deborah",
                "last_name": "Udo",
                "job_title": "HR Assistant",
                "role": "Human Resource",
            },
        ],
    },
    "Account": {
        "manager": {
            "first_name": "Peter",
            "last_name": "Ogunleye",
            "job_title": "Account Manager",
        },
        "members": [
            {
                "first_name": "Joshua",
                "last_name": "Ibrahim",
                "job_title": "Account Officer",
                "role": "Account Officer",
            },
            {
                "first_name": "Ruth",
                "last_name": "Onyeka",
                "job_title": "Accounts Assistant",
                "role": "Account Officer",
            },
        ],
    },
    "Management": {
        "manager": {
            "first_name": "Victoria",
            "last_name": "Balogun",
            "job_title": "Administrative Manager",
        },
        "members": [
            {
                "first_name": "Nathan",
                "last_name": "Akinola",
                "job_title": "Administrative Officer",
                "role": "Viewer",
            },
            {
                "first_name": "Faith",
                "last_name": "Nwosu",
                "job_title": "Executive Assistant",
                "role": "Viewer",
            },
        ],
    },
    "Procurement": {
        "manager": {
            "first_name": "Andrew",
            "last_name": "Ojo",
            "job_title": "Procurement Manager",
        },
        "members": [
            {
                "first_name": "Mercy",
                "last_name": "Abiola",
                "job_title": "Procurement Officer",
                "role": "Procurement Officer",
            },
            {
                "first_name": "Isaac",
                "last_name": "Danladi",
                "job_title": "Procurement Assistant",
                "role": "Procurement Officer",
            },
        ],
    },
}


class Command(BaseCommand):
    help = (
        "Creates one CEO, one manager per department and two department "
        "members, including reporting relationships."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="created_staff_accounts.txt",
            help="Path for the generated account report.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        output_path = Path(options["output"]).resolve()

        required_departments = {
            "Technical",
            "Human Resource",
            "Account",
            "Management",
            "Procurement",
        }

        departments = {
            department.name: department
            for department in Department.objects.filter(name__in=required_departments)
        }

        missing_departments = required_departments - departments.keys()

        if missing_departments:
            raise CommandError(
                "Missing departments: "
                + ", ".join(sorted(missing_departments))
                + ". Run `python manage.py create_default_account_data` first."
            )

        required_roles = {
            "CEO",
            "Manager",
            "Software Engineer",
            "IT Technician",
            "Human Resource",
            "Account Officer",
            "Procurement Officer",
            "Viewer",
        }

        roles = {
            role.name: role for role in Role.objects.filter(name__in=required_roles)
        }

        missing_roles = required_roles - roles.keys()

        if missing_roles:
            raise CommandError(
                "Missing roles: "
                + ", ".join(sorted(missing_roles))
                + ". Run `python manage.py create_default_account_data` first."
            )

        created_records = []
        existing_records = []

        ceo_profile, ceo_created = self.create_staff(
            first_name=CEO_DATA["first_name"],
            last_name=CEO_DATA["last_name"],
            department=departments[CEO_DATA["department"]],
            job_title=CEO_DATA["job_title"],
            role=roles[CEO_DATA["role"]],
            reports_to=None,
        )

        self.record_result(
            ceo_profile,
            ceo_created,
            created_records,
            existing_records,
        )

        for department_name, staff_data in DEPARTMENT_STAFF.items():
            department = departments[department_name]

            manager_profile, manager_created = self.create_staff(
                first_name=staff_data["manager"]["first_name"],
                last_name=staff_data["manager"]["last_name"],
                department=department,
                job_title=staff_data["manager"]["job_title"],
                role=roles["Manager"],
                reports_to=ceo_profile,
            )

            self.record_result(
                manager_profile,
                manager_created,
                created_records,
                existing_records,
            )

            self.create_department_leadership(
                department=department,
                manager=manager_profile,
            )

            for member_data in staff_data["members"]:
                member_profile, member_created = self.create_staff(
                    first_name=member_data["first_name"],
                    last_name=member_data["last_name"],
                    department=department,
                    job_title=member_data["job_title"],
                    role=roles[member_data["role"]],
                    reports_to=manager_profile,
                )

                self.record_result(
                    member_profile,
                    member_created,
                    created_records,
                    existing_records,
                )

        report = self.build_report(
            created_records=created_records,
            existing_records=existing_records,
        )

        output_path.write_text(report, encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(f"Created {len(created_records)} new staff accounts.")
        )

        if existing_records:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped {len(existing_records)} existing accounts."
                )
            )

        self.stdout.write(self.style.SUCCESS(f"Report saved to: {output_path}"))

    def create_staff(
        self,
        *,
        first_name,
        last_name,
        department,
        job_title,
        role,
        reports_to,
    ):
        username = first_name.lower()
        email = f"{username}@holansl.com"

        user = User.objects.filter(email__iexact=email).first()
        user_created = user is None

        if user is None:
            user = User.objects.create_user(
                username=username,
                email=email,
                first_name=first_name,
                last_name=last_name,
                password=settings.DEFAULT_STAFF_PASSWORD,
                is_active=True,
                is_staff=True,
            )

            user.must_change_password = True
            user.save(update_fields=["must_change_password"])

        # Ensure the intended role is assigned even when the user existed.
        user.groups.add(role)

        phone_number = self.build_phone_number(username)

        profile, profile_created = StaffProfile.objects.get_or_create(
            user=user,
            defaults={
                "department": department,
                "reports_to": reports_to,
                "job_title": job_title,
                "employment_type": StaffProfile.EmploymentType.FULL_TIME,
                "start_date": timezone.localdate(),
                "phone_number": phone_number,
                "address": "",
                "nationality": "Nigerian",
            },
        )

        if not profile_created:
            changed_fields = []

            if profile.department_id != department.id:
                profile.department = department
                changed_fields.append("department")

            expected_reports_to_id = reports_to.id if reports_to is not None else None

            if profile.reports_to_id != expected_reports_to_id:
                profile.reports_to = reports_to
                changed_fields.append("reports_to")

            if not profile.job_title:
                profile.job_title = job_title
                changed_fields.append("job_title")

            if changed_fields:
                profile.full_clean()
                profile.save(update_fields=changed_fields)

        created = user_created or profile_created

        if created:
            AuditLog.objects.create(
                user=None,
                target_user=user,
                event_category=AuditLog.EventCategory.SECURITY,
                event_type=AuditLog.EventType.ACCOUNT_CREATED,
                status=AuditLog.EventStatus.SUCCESS,
                app_label="accounts",
                resource="staffprofile",
                action="create",
                object_id=str(profile.id),
                metadata={
                    "source": "create_sample_staff_command",
                    "department": department.name,
                    "job_title": job_title,
                    "role": role.name,
                    "reports_to": (str(reports_to.id) if reports_to else None),
                },
            )

        return profile, created

    def create_department_leadership(self, *, department, manager):
        leadership, created = DepartmentLeadership.objects.get_or_create(
            department=department,
            manager=manager,
            leadership_type=DepartmentLeadership.LeadershipType.MANAGER,
            active_from=timezone.localdate(),
            defaults={
                "is_primary": True,
            },
        )

        if not created and not leadership.is_primary:
            existing_primary = DepartmentLeadership.objects.filter(
                department=department,
                is_primary=True,
                active_until__isnull=True,
            ).exclude(pk=leadership.pk)

            if not existing_primary.exists():
                leadership.is_primary = True
                leadership.save(update_fields=["is_primary"])

    def build_phone_number(self, username):
        """
        StaffProfile.phone_number is unique and required.

        Generate a stable dummy number from the username so rerunning the
        command does not create changing values.
        """
        numeric_value = sum(
            (index + 1) * ord(character) for index, character in enumerate(username)
        )

        suffix = str(numeric_value).zfill(8)[-8:]
        return f"+23480{suffix}"

    def record_result(
        self,
        profile,
        created,
        created_records,
        existing_records,
    ):
        record = {
            "name": profile.user.get_full_name(),
            "username": profile.user.username,
            "email": profile.user.email,
            "department": (profile.department.name if profile.department else "None"),
            "job_title": profile.job_title,
            "role": ", ".join(profile.user.groups.values_list("name", flat=True)),
            "reports_to": (
                profile.reports_to.user.get_full_name()
                if profile.reports_to
                else "None"
            ),
            "employee_id": profile.employee_id,
        }

        if created:
            created_records.append(record)
        else:
            existing_records.append(record)

    def build_report(self, *, created_records, existing_records):
        generated_at = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S %Z")

        lines = [
            "=" * 100,
            "HOLANSL STAFF ACCOUNT CREATION REPORT",
            f"Generated: {generated_at}",
            "=" * 100,
            "",
            f"New accounts created: {len(created_records)}",
            f"Existing accounts skipped: {len(existing_records)}",
            "",
        ]

        if created_records:
            lines.extend(
                [
                    "SUCCESSFULLY CREATED ACCOUNTS",
                    "-" * 100,
                    (
                        f"{'NAME':<24}"
                        f"{'EMAIL':<30}"
                        f"{'DEPARTMENT':<20}"
                        f"{'JOB TITLE':<28}"
                        f"{'REPORTS TO'}"
                    ),
                    "-" * 100,
                ]
            )

            for record in created_records:
                lines.append(
                    f"{record['name']:<24}"
                    f"{record['email']:<30}"
                    f"{record['department']:<20}"
                    f"{record['job_title']:<28}"
                    f"{record['reports_to']}"
                )

                lines.append(
                    f"  Username: {record['username']} | "
                    f"Employee ID: {record['employee_id']} | "
                    f"Role(s): {record['role']}"
                )

            lines.append("")

        if existing_records:
            lines.extend(
                [
                    "EXISTING ACCOUNTS — NOT RECREATED",
                    "-" * 100,
                ]
            )

            for record in existing_records:
                lines.append(
                    f"{record['name']} | "
                    f"{record['email']} | "
                    f"{record['department']} | "
                    f"{record['job_title']}"
                )

        lines.extend(
            [
                "",
                "SECURITY NOTE",
                "-" * 100,
                (
                    "New accounts use the configured default staff password "
                    "and must change it through the email reset flow before "
                    "normal authentication."
                ),
                "",
            ]
        )

        return "\n".join(lines)
