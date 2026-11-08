from io import StringIO

from django.core.management import call_command
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

    def test_existing_names_with_legacy_codes_are_preserved_on_repeat_runs(self):
        existing = [
            Department.objects.create(
                name=item["name"], code=f"OLD{i}", description="Existing description"
            )
            for i, item in enumerate(DEPARTMENTS)
        ]

        self.seed()
        self.seed()

        self.assertEqual(Department.objects.count(), len(existing))
        for i, department in enumerate(existing):
            department.refresh_from_db()
            self.assertEqual(department.name, DEPARTMENTS[i]["name"])
            self.assertEqual(department.code, f"OLD{i}")
            self.assertEqual(department.description, "Existing description")
        self.assertTrue(Role.objects.filter(name="Viewer").exists())

    def test_code_and_name_on_separate_records_are_preserved(self):
        by_code = Department.objects.create(name="Accounting", code="ACC")
        by_name = Department.objects.create(name="Account", code="FIN")

        self.seed()
        self.seed()

        by_code.refresh_from_db()
        by_name.refresh_from_db()
        self.assertEqual((by_code.name, by_code.code), ("Accounting", "ACC"))
        self.assertEqual((by_name.name, by_name.code), ("Account", "FIN"))
        self.assertEqual(Department.objects.count(), len(DEPARTMENTS) + 1)
