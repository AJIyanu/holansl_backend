from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Department, Role


DEPARTMENTS = [
    {
        "name": "Management",
        "code": "MGT",
        "description": "Executive and general company management.",
    },
    {
        "name": "Procurement",
        "code": "PROC",
        "description": "Procurement, suppliers, quotations and purchase orders.",
    },
    {
        "name": "Account",
        "code": "ACC",
        "description": "Accounting, payments, transactions and reconciliation.",
    },
    {
        "name": "Human Resource",
        "code": "HR",
        "description": "Staff administration and human-resource operations.",
    },
    {
        "name": "Technical",
        "code": "TECH",
        "description": "Software engineering, IT and technical operations.",
    },
]


ROLE_PERMISSION_RULES = {
    "CEO": {
        "apps": ["accounts", "crm", "procurement", "ledger"],
        "actions": ["add", "change", "view", "delete"],
    },
    "CTO": {
        "apps": ["accounts", "crm", "procurement", "ledger"],
        "actions": ["add", "change", "view", "delete"],
    },
    "Super Admin": {
        "apps": ["accounts", "crm", "procurement", "ledger"],
        "actions": ["add", "change", "view", "delete"],
    },
    "Manager": {
        "apps": ["crm", "procurement", "ledger"],
        "actions": ["add", "change", "view"],
    },
    "Procurement Officer": {
        "apps": ["crm", "procurement"],
        "actions": ["add", "change", "view"],
    },
    "Account Officer": {
        "apps": ["crm", "ledger"],
        "actions": ["add", "change", "view"],
    },
    "Human Resource": {
        "apps": ["accounts"],
        "actions": ["add", "change", "view"],
    },
    "Software Engineer": {
        "apps": ["accounts", "crm", "procurement", "ledger"],
        "actions": ["view"],
    },
    "IT Technician": {
        "apps": ["accounts", "crm", "procurement", "ledger"],
        "actions": ["view"],
    },
    "Viewer": {
        "apps": ["accounts", "crm", "procurement", "ledger"],
        "actions": ["view"],
    },
}


class Command(BaseCommand):
    help = "Creates default departments, roles and role permissions safely."

    @transaction.atomic
    def handle(self, *args, **options):
        for department_data in DEPARTMENTS:
            department, created = Department.objects.get_or_create(
                name=department_data["name"],
                defaults={
                    "code": department_data["code"],
                    "description": department_data["description"],
                },
            )

            if created:
                self.stdout.write(
                    self.style.SUCCESS(f"Created department: {department.name}")
                )
            else:
                self.stdout.write(f"Department already exists: {department.name}")

        for role_name, rule in ROLE_PERMISSION_RULES.items():
            role, created = Role.objects.get_or_create(name=role_name)

            permissions = Permission.objects.filter(
                content_type__app_label__in=rule["apps"],
            )

            allowed_prefixes = tuple(f"{action}_" for action in rule["actions"])

            extra_codenames = set(rule.get("extra_codenames", []))

            permissions = [
                permission
                for permission in permissions
                if (
                    permission.codename.startswith(allowed_prefixes)
                    or permission.codename in extra_codenames
                )
            ]

            role.permissions.set(permissions)

            status = "Created" if created else "Updated"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{status} role: {role_name} ({len(permissions)} permissions)"
                )
            )

        self.stdout.write(
            self.style.SUCCESS("Default account data configured successfully.")
        )
