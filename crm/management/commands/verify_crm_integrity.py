"""
Verify CRM independence and removal of the old Procurement and Ledger domains.
"""

from __future__ import annotations

import json

from django.apps import apps
from django.core.management.base import (
    BaseCommand,
    CommandError,
)
from django.db import connection

from crm.models import Party


class Command(BaseCommand):
    """Verify the final independent CRM application state."""

    help = (
        "Verify that CRM legacy fields and models are removed and that "
        "no Procurement or Ledger tables remain."
    )

    def add_arguments(self, parser) -> None:
        """
        Register command-line options.

        Args:
            parser: Django management-command argument parser.

        Returns:
            None.
        """

        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Return the result as JSON.",
        )

    def handle(self, *args, **options) -> None:
        """
        Run all final CRM reset checks.

        Args:
            *args: Positional arguments supplied by Django.
            **options: Parsed command options.

        Returns:
            None.

        Raises:
            CommandError: If legacy models, fields or tables remain.
        """

        errors = []

        self._check_empty_application(
            "procurement",
            errors,
        )

        self._check_empty_application(
            "ledger",
            errors,
        )

        self._check_removed_tables(
            errors,
        )

        self._check_crm_legacy_fields(
            errors,
        )

        self._check_contact_person_removed(
            errors,
        )

        report = {
            "status": ("failed" if errors else "passed"),
            "errors": errors,
            "party_count": Party.objects.count(),
        }

        if options["as_json"]:
            self.stdout.write(
                json.dumps(
                    report,
                    indent=2,
                )
            )
        elif errors:
            self.stdout.write(self.style.ERROR("CRM integrity verification failed."))

            for error in errors:
                self.stdout.write(f"  {json.dumps(error)}")
        else:
            self.stdout.write(self.style.SUCCESS("CRM integrity verification passed."))

        if errors:
            raise CommandError(
                f"CRM integrity verification found {len(errors)} error(s)."
            )

    def _check_empty_application(
        self,
        app_label: str,
        errors: list[dict],
    ) -> None:
        """
        Confirm that an application has no registered models.

        Args:
            app_label: Django application label.
            errors: Mutable collection receiving errors.

        Returns:
            None.
        """

        app_config = apps.get_app_config(
            app_label,
        )

        models = [model._meta.label for model in app_config.get_models()]

        if models:
            errors.append(
                {
                    "code": "application_not_empty",
                    "app": app_label,
                    "models": models,
                }
            )

    def _check_removed_tables(
        self,
        errors: list[dict],
    ) -> None:
        """
        Confirm that old Procurement and Ledger tables are absent.

        Args:
            errors: Mutable collection receiving errors.

        Returns:
            None.
        """

        table_names = set(connection.introspection.table_names())

        remaining_tables = sorted(
            table_name
            for table_name in table_names
            if table_name.startswith(
                (
                    "procurement_",
                    "ledger_",
                )
            )
        )

        if remaining_tables:
            errors.append(
                {
                    "code": "legacy_tables_remain",
                    "tables": remaining_tables,
                }
            )

    def _check_crm_legacy_fields(
        self,
        errors: list[dict],
    ) -> None:
        """
        Confirm that Party compatibility fields are gone.

        Args:
            errors: Mutable collection receiving errors.

        Returns:
            None.
        """

        party_fields = {field.name for field in Party._meta.get_fields()}

        remaining_fields = sorted(
            {
                "name",
                "party_type",
            }
            & party_fields
        )

        if remaining_fields:
            errors.append(
                {
                    "code": "legacy_party_fields",
                    "fields": remaining_fields,
                }
            )

    def _check_contact_person_removed(
        self,
        errors: list[dict],
    ) -> None:
        """
        Confirm that the temporary ContactPerson model is gone.

        Args:
            errors: Mutable collection receiving errors.

        Returns:
            None.
        """

        try:
            apps.get_model(
                "crm",
                "ContactPerson",
            )
        except LookupError:
            return

        errors.append(
            {
                "code": "legacy_contact_person_model",
            }
        )
