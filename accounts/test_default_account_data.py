from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.management.commands.create_default_account_data import DEPARTMENTS
from accounts.models import Department, Role


class DefaultAccountDataTests(TestCase):
    def seed(self):
        call_command("create_default_account_data", stdout=StringIO())

    def test_repeated_seeding_preserves_renamed_department(self):
        existing = Department.objects.create(
            name="Accounting", code="ACC", description="Staff-maintained description"
        )

        self.seed()
        first_ids = dict(Department.objects.values_list("code", "pk"))
        self.seed()

        existing.refresh_from_db()
        self.assertEqual(existing.name, "Accounting")
        self.assertEqual(existing.description, "Staff-maintained description")
        self.assertEqual(Department.objects.get(code="ACC").pk, existing.pk)
        self.assertEqual(dict(Department.objects.values_list("code", "pk")), first_ids)
        self.assertEqual(set(first_ids), {item["code"] for item in DEPARTMENTS})
        self.assertFalse(Department.objects.filter(name="Account").exists())

    def test_fresh_database_gets_default_department_fields(self):
        self.seed()

        for item in DEPARTMENTS:
            department = Department.objects.get(code=item["code"])
            self.assertEqual(department.name, item["name"])
            self.assertEqual(department.description, item["description"])

    def test_name_conflict_reports_error_and_rolls_back(self):
        existing = Department.objects.create(name="Account", code="FIN")

        with self.assertRaisesMessage(
            CommandError, "that name already uses another code"
        ):
            self.seed()

        self.assertEqual(
            list(Department.objects.values_list("pk", flat=True)), [existing.pk]
        )
        existing.refresh_from_db()
        self.assertEqual(existing.code, "FIN")
        self.assertFalse(Role.objects.exists())
