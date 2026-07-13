from __future__ import annotations

import io
import json
import os
import uuid
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Model, QuerySet
from django.db.models.deletion import Collector, ProtectedError, RestrictedError
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Inspect, back up, and optionally purge the current CRM domain before "
        "the CRM schema is rebuilt."
    )

    CONFIRMATION_PHRASE = "RESET_CRM_DOMAIN"
    PURGE_ENVIRONMENT_VARIABLE = "ALLOW_CRM_REBUILD_PURGE"

    # A stable application-specific PostgreSQL advisory lock ID.
    POSTGRES_ADVISORY_LOCK_ID = 2_026_070_801

    RELEVANT_MIGRATION_APPS = {
        "crm",
        "procurement",
        "ledger",
    }

    def add_arguments(self, parser):
        mode_group = parser.add_mutually_exclusive_group()

        mode_group.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Inspect CRM records and dependencies without creating a "
                "backup or changing data. This is the default mode."
            ),
        )

        mode_group.add_argument(
            "--backup-only",
            action="store_true",
            help="Create a backup archive without deleting or changing data.",
        )

        mode_group.add_argument(
            "--purge-dependent-data",
            action="store_true",
            help=(
                "Back up and then purge the current CRM records and all rows "
                "Django would cascade-delete from them."
            ),
        )

        parser.add_argument(
            "--confirm",
            default="",
            help=(
                "Exact confirmation phrase required for destructive mode: "
                f"{self.CONFIRMATION_PHRASE}"
            ),
        )

        parser.add_argument(
            "--output-dir",
            default=None,
            help=(
                "Directory where backup archives will be written. Defaults "
                "to CRM_REBUILD_BACKUP_DIR or BASE_DIR/crm_rebuild_backups."
            ),
        )

        parser.add_argument(
            "--database",
            default=DEFAULT_DB_ALIAS,
            help="Django database alias to inspect. Defaults to 'default'.",
        )

    def handle(self, *args, **options):
        database = options["database"]
        mode = self._resolve_mode(options)

        if database not in connections:
            raise CommandError(f"Unknown database alias: {database}")

        connection = connections[database]

        try:
            connection.ensure_connection()
        except Exception as exc:
            raise CommandError(
                f"Could not connect to database '{database}': {exc}"
            ) from exc

        output_dir = self._resolve_output_directory(options["output_dir"])

        if mode == "purge":
            self._validate_purge_authorisation(options["confirm"])
            self._run_purge(database, output_dir)
            return

        state = self._inspect_database(database)
        self._print_inventory(state)

        if mode == "backup":
            backup_path = self._write_backup(
                state=state,
                database=database,
                output_dir=output_dir,
                mode="backup-only",
            )

            self.stdout.write(
                self.style.SUCCESS(f"\nBackup created successfully: {backup_path}")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nDry run completed. No files were created and no "
                    "database records were changed."
                )
            )

    def _resolve_mode(self, options: dict[str, Any]) -> str:
        if options["purge_dependent_data"]:
            return "purge"

        if options["backup_only"]:
            return "backup"

        return "dry-run"

    def _resolve_output_directory(self, supplied_path: str | None) -> Path:
        configured_path = (
            supplied_path
            or os.getenv("CRM_REBUILD_BACKUP_DIR")
            or str(Path(settings.BASE_DIR) / "crm_rebuild_backups")
        )

        return Path(configured_path).expanduser().resolve()

    def _validate_purge_authorisation(self, confirmation: str) -> None:
        enabled = os.getenv(
            self.PURGE_ENVIRONMENT_VARIABLE,
            "",
        ).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        if not enabled:
            raise CommandError(
                "Destructive CRM purge is disabled. Set "
                f"{self.PURGE_ENVIRONMENT_VARIABLE}=true only after reviewing "
                "the dry-run and backup output."
            )

        if confirmation != self.CONFIRMATION_PHRASE:
            raise CommandError(
                "Incorrect or missing confirmation phrase. Supply:\n"
                f"--confirm {self.CONFIRMATION_PHRASE}"
            )

    def _run_purge(self, database: str, output_dir: Path) -> None:
        self.stdout.write(
            self.style.WARNING(
                "\nDESTRUCTIVE MODE ENABLED\n"
                "The command will create a backup and then delete current "
                "CRM records together with rows affected by CASCADE."
            )
        )

        backup_path: Path
        deletion_result: dict[str, Any]

        with transaction.atomic(using=database):
            self._acquire_database_lock(database)

            # Recalculate everything inside the same transaction used for
            # deletion. This prevents the purge plan from becoming stale.
            state = self._inspect_database(database)
            self._print_inventory(state)

            blockers = state["manifest"]["blockers"]

            if blockers:
                raise CommandError(
                    "The purge cannot continue because protected or restricted "
                    "references were discovered. Review the blocker section "
                    "from the dry-run output."
                )

            backup_path = self._write_backup(
                state=state,
                database=database,
                output_dir=output_dir,
                mode="pre-purge",
            )

            collector: Collector = state["collector"]

            try:
                deleted_count, deleted_by_model = collector.delete()
            except (ProtectedError, RestrictedError) as exc:
                raise CommandError(
                    f"Deletion was stopped by a protected database relationship: {exc}"
                ) from exc

            remaining_crm_rows = self._crm_model_counts(database)

            non_zero_remaining = {
                model_label: count
                for model_label, count in remaining_crm_rows.items()
                if count
            }

            if non_zero_remaining:
                raise CommandError(
                    "The purge was rolled back because CRM records remained: "
                    f"{non_zero_remaining}"
                )

            deletion_result = {
                "completed_at": timezone.now().isoformat(),
                "database_alias": database,
                "backup_path": str(backup_path),
                "deleted_total": deleted_count,
                "deleted_by_model": deleted_by_model,
                "remaining_crm_rows": remaining_crm_rows,
            }

        result_path = self._write_purge_result(
            backup_path=backup_path,
            result=deletion_result,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "\nCRM purge completed successfully.\n"
                f"Backup: {backup_path}\n"
                f"Result manifest: {result_path}\n"
                f"Deleted records: {deletion_result['deleted_total']}"
            )
        )

    def _acquire_database_lock(self, database: str) -> None:
        connection = connections[database]

        if connection.vendor != "postgresql":
            self.stdout.write(
                self.style.WARNING(
                    "PostgreSQL advisory locking is unavailable for this "
                    f"{connection.vendor} database. The transaction lock still "
                    "applies."
                )
            )
            return

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                [self.POSTGRES_ADVISORY_LOCK_ID],
            )

        self.stdout.write("Acquired PostgreSQL CRM rebuild advisory lock.")

    def _inspect_database(self, database: str) -> dict[str, Any]:
        crm_models = self._get_crm_models()
        direct_references = self._discover_direct_references(
            crm_models=crm_models,
            database=database,
        )

        collector = Collector(using=database)
        blockers: list[dict[str, Any]] = []

        for model in crm_models:
            queryset = model._default_manager.using(database).all()

            if not queryset.exists():
                continue

            try:
                collector.collect(
                    queryset,
                    fail_on_restricted=False,
                )
            except (ProtectedError, RestrictedError) as exc:
                blockers.extend(self._extract_exception_blockers(exc))

        blockers.extend(self._extract_collector_restrictions(collector))
        blockers = self._deduplicate_blockers(blockers)

        delete_plan = self._build_delete_plan(collector)
        field_update_plan = self._build_field_update_plan(collector)

        connection = connections[database]

        manifest = {
            "generated_at": timezone.now().isoformat(),
            "database": self._safe_database_description(database),
            "crm_models": [
                {
                    "model": model._meta.label,
                    "table": model._meta.db_table,
                    "count": model._default_manager.using(database).count(),
                }
                for model in crm_models
            ],
            "direct_references": direct_references,
            "delete_plan": delete_plan,
            "field_update_plan": field_update_plan,
            "blockers": blockers,
            "relevant_tables": sorted(
                table_name
                for table_name in connection.introspection.table_names()
                if table_name.startswith(
                    (
                        "crm_",
                        "procurement_",
                        "ledger_",
                    )
                )
            ),
            "migration_state": self._migration_state(database),
            "warnings": [
                (
                    "This archive is an application-level JSON backup. It is "
                    "not a replacement for a PostgreSQL/Supabase database "
                    "backup."
                ),
                (
                    "This command does not drop tables and does not alter the "
                    "django_migrations table."
                ),
            ],
        }

        return {
            "manifest": manifest,
            "collector": collector,
            "crm_models": crm_models,
        }

    def _get_crm_models(self) -> list[type[Model]]:
        try:
            crm_config = apps.get_app_config("crm")
        except LookupError as exc:
            raise CommandError("The CRM application is not installed.") from exc

        return sorted(
            crm_config.get_models(include_auto_created=False),
            key=lambda model: model._meta.label_lower,
        )

    def _discover_direct_references(
        self,
        crm_models: list[type[Model]],
        database: str,
    ) -> list[dict[str, Any]]:
        crm_model_set = set(crm_models)
        references: list[dict[str, Any]] = []

        for model in apps.get_models(include_auto_created=False):
            for field in model._meta.get_fields(include_hidden=False):
                if getattr(field, "auto_created", False):
                    continue

                if not getattr(field, "is_relation", False):
                    continue

                remote_field = getattr(field, "remote_field", None)

                if remote_field is None:
                    continue

                remote_model = getattr(remote_field, "model", None)

                if remote_model not in crm_model_set:
                    continue

                if not (
                    getattr(field, "many_to_one", False)
                    or getattr(field, "one_to_one", False)
                    or getattr(field, "many_to_many", False)
                ):
                    continue

                queryset = self._reference_queryset(
                    model=model,
                    field_name=field.name,
                    database=database,
                )

                on_delete = getattr(remote_field, "on_delete", None)

                if on_delete is None:
                    on_delete_name = "MANY_TO_MANY"
                else:
                    on_delete_name = getattr(
                        on_delete,
                        "__name__",
                        str(on_delete),
                    )

                references.append(
                    {
                        "model": model._meta.label,
                        "table": model._meta.db_table,
                        "field": field.name,
                        "target_model": remote_model._meta.label,
                        "on_delete": on_delete_name,
                        "nullable": getattr(field, "null", False),
                        "count": queryset.count(),
                        "crm_internal": model._meta.app_label == "crm",
                    }
                )

        return sorted(
            references,
            key=lambda item: (
                item["model"].lower(),
                item["field"].lower(),
            ),
        )

    def _reference_queryset(
        self,
        model: type[Model],
        field_name: str,
        database: str,
    ) -> QuerySet:
        return (
            model._default_manager.using(database)
            .filter(**{f"{field_name}__isnull": False})
            .distinct()
        )

    def _build_delete_plan(
        self,
        collector: Collector,
    ) -> list[dict[str, Any]]:
        counts: dict[str, int] = defaultdict(int)
        sources: dict[str, set[str]] = defaultdict(set)

        for model, instances in collector.data.items():
            model_label = model._meta.label
            counts[model_label] += len(instances)
            sources[model_label].add("collector")

        for queryset in collector.fast_deletes:
            model = queryset.model
            model_label = model._meta.label
            counts[model_label] += queryset.count()
            sources[model_label].add("fast_delete")

        return [
            {
                "model": model_label,
                "count": counts[model_label],
                "sources": sorted(sources[model_label]),
            }
            for model_label in sorted(counts, key=str.lower)
        ]

    def _build_field_update_plan(
        self,
        collector: Collector,
    ) -> list[dict[str, Any]]:
        updates: dict[tuple[str, str, str], int] = defaultdict(int)

        for (field, value), batches in collector.field_updates.items():
            model_label = field.model._meta.label
            value_description = None if value is None else str(value)
            key = (
                model_label,
                field.name,
                str(value_description),
            )

            for batch in batches:
                updates[key] += self._count_collection(batch)

        return [
            {
                "model": model_label,
                "field": field_name,
                "new_value": (
                    None if value_description == "None" else value_description
                ),
                "count": count,
            }
            for (
                model_label,
                field_name,
                value_description,
            ), count in sorted(
                updates.items(),
                key=lambda item: (
                    item[0][0].lower(),
                    item[0][1].lower(),
                ),
            )
        ]

    def _count_collection(self, value: Any) -> int:
        if isinstance(value, QuerySet):
            return value.count()

        try:
            return len(value)
        except TypeError:
            return 1

    def _extract_exception_blockers(
        self,
        exc: Exception,
    ) -> list[dict[str, Any]]:
        raw_objects = (
            getattr(exc, "protected_objects", None)
            or getattr(exc, "restricted_objects", None)
            or []
        )

        objects = list(self._flatten_values(raw_objects))

        if not objects:
            return [
                {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                }
            ]

        blockers: list[dict[str, Any]] = []

        for obj in objects[:100]:
            if isinstance(obj, Model):
                blockers.append(
                    {
                        "type": exc.__class__.__name__,
                        "model": obj._meta.label,
                        "pk": str(obj.pk),
                    }
                )
            else:
                blockers.append(
                    {
                        "type": exc.__class__.__name__,
                        "message": str(obj),
                    }
                )

        return blockers

    def _extract_collector_restrictions(
        self,
        collector: Collector,
    ) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []

        for model, field_mapping in collector.restricted_objects.items():
            for field, instances in field_mapping.items():
                for instance in list(instances)[:100]:
                    blockers.append(
                        {
                            "type": "RestrictedError",
                            "model": model._meta.label,
                            "field": field.name,
                            "pk": str(instance.pk),
                        }
                    )

        return blockers

    def _flatten_values(self, value: Any) -> Iterable[Any]:
        if isinstance(value, dict):
            for nested_value in value.values():
                yield from self._flatten_values(nested_value)
            return

        if isinstance(value, (list, tuple, set, frozenset)):
            for nested_value in value:
                yield from self._flatten_values(nested_value)
            return

        yield value

    def _deduplicate_blockers(
        self,
        blockers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []

        for blocker in blockers:
            signature = json.dumps(
                blocker,
                sort_keys=True,
                default=str,
            )

            if signature in seen:
                continue

            seen.add(signature)
            unique.append(blocker)

        return unique

    def _crm_model_counts(self, database: str) -> dict[str, int]:
        return {
            model._meta.label: model._default_manager.using(database).count()
            for model in self._get_crm_models()
        }

    def _migration_state(self, database: str) -> list[dict[str, str]]:
        recorder = MigrationRecorder(connections[database])

        return [
            {
                "app": app_label,
                "migration": migration_name,
                "applied_at": (
                    applied_at.isoformat() if applied_at is not None else ""
                ),
            }
            for app_label, migration_name, applied_at in (
                recorder.migration_qs.filter(app__in=self.RELEVANT_MIGRATION_APPS)
                .order_by("app", "name")
                .values_list("app", "name", "applied")
            )
        ]

    def _safe_database_description(
        self,
        database: str,
    ) -> dict[str, str]:
        configuration = connections[database].settings_dict

        return {
            "alias": database,
            "engine": str(configuration.get("ENGINE", "")),
            "name": str(configuration.get("NAME", "")),
            "host": str(configuration.get("HOST", "")),
            "port": str(configuration.get("PORT", "")),
            "vendor": connections[database].vendor,
        }

    def _write_backup(
        self,
        state: dict[str, Any],
        database: str,
        output_dir: Path,
        mode: str,
    ) -> Path:
        output_dir.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )

        try:
            output_dir.chmod(0o700)
        except OSError:
            pass

        if os.getenv("RENDER"):
            self.stdout.write(
                self.style.WARNING(
                    "Render filesystems may be ephemeral. Copy this backup "
                    "off the service immediately or run the command locally "
                    "against the same PostgreSQL database."
                )
            )

        timestamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        archive_path = (
            output_dir / f"crm-rebuild-{mode}-{timestamp}-{uuid.uuid4().hex[:8]}.zip"
        )

        model_primary_keys = self._collect_impacted_primary_keys(
            state=state,
            database=database,
        )

        backup_files: list[dict[str, Any]] = []
        skipped_models: list[dict[str, Any]] = []

        manifest = dict(state["manifest"])
        manifest["backup"] = {
            "mode": mode,
            "created_at": timezone.now().isoformat(),
            "files": backup_files,
            "skipped_models": skipped_models,
        }

        with zipfile.ZipFile(
            archive_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for model in sorted(
                model_primary_keys,
                key=lambda item: item._meta.label_lower,
            ):
                if model._meta.auto_created:
                    skipped_models.append(
                        {
                            "model": model._meta.label,
                            "reason": "Auto-created intermediary model",
                        }
                    )
                    continue

                primary_keys = model_primary_keys[model]

                if primary_keys:
                    queryset = (
                        model._default_manager.using(database)
                        .filter(pk__in=primary_keys)
                        .order_by(model._meta.pk.attname)
                    )
                else:
                    queryset = model._default_manager.using(database).none()

                stream = io.StringIO()

                serializers.serialize(
                    "json",
                    queryset.iterator(chunk_size=500),
                    stream=stream,
                    indent=2,
                    use_natural_foreign_keys=False,
                    use_natural_primary_keys=False,
                )

                file_name = (
                    f"data/{model._meta.app_label}/{model._meta.model_name}.json"
                )

                archive.writestr(
                    file_name,
                    stream.getvalue(),
                )

                backup_files.append(
                    {
                        "model": model._meta.label,
                        "file": file_name,
                        "count": len(primary_keys),
                    }
                )

            archive.writestr(
                "manifest.json",
                json.dumps(
                    manifest,
                    indent=2,
                    sort_keys=True,
                    default=str,
                ),
            )

            archive.writestr(
                "RESTORE_NOTICE.txt",
                (
                    "This archive contains Django JSON fixture-style exports "
                    "for CRM and affected records.\n\n"
                    "Do not run loaddata blindly against the redesigned CRM "
                    "schema. The files are intended for audit, recovery, and "
                    "controlled transformation during the CRM rebuild.\n\n"
                    "A separate PostgreSQL/Supabase backup should also be "
                    "taken before a production purge.\n"
                ),
            )

        try:
            archive_path.chmod(0o600)
        except OSError:
            pass

        return archive_path

    def _collect_impacted_primary_keys(
        self,
        state: dict[str, Any],
        database: str,
    ) -> dict[type[Model], set[Any]]:
        collector: Collector = state["collector"]
        crm_models: list[type[Model]] = state["crm_models"]

        model_primary_keys: dict[type[Model], set[Any]] = defaultdict(set)

        # Always include every current CRM model in the archive, even when
        # the model contains no records.
        for model in crm_models:
            model_primary_keys[model].update(
                model._default_manager.using(database).values_list("pk", flat=True)
            )

        # Include models that Django plans to delete through CASCADE.
        for model, instances in collector.data.items():
            model_primary_keys[model].update(
                instance.pk for instance in instances if instance.pk is not None
            )

        for queryset in collector.fast_deletes:
            model_primary_keys[queryset.model].update(
                queryset.values_list("pk", flat=True)
            )

        # Include rows directly referencing CRM even where the relationship
        # will be changed with SET_NULL rather than deleted.
        for reference in state["manifest"]["direct_references"]:
            model = apps.get_model(reference["model"])

            queryset = self._reference_queryset(
                model=model,
                field_name=reference["field"],
                database=database,
            )

            model_primary_keys[model].update(queryset.values_list("pk", flat=True))

        return model_primary_keys

    def _write_purge_result(
        self,
        backup_path: Path,
        result: dict[str, Any],
    ) -> Path:
        result_path = backup_path.with_name(f"{backup_path.stem}-purge-result.json")

        result_path.write_text(
            json.dumps(
                result,
                indent=2,
                sort_keys=True,
                default=str,
            ),
            encoding="utf-8",
        )

        try:
            result_path.chmod(0o600)
        except OSError:
            pass

        return result_path

    def _print_inventory(self, state: dict[str, Any]) -> None:
        manifest = state["manifest"]

        self.stdout.write("\nCRM rebuild inspection")
        self.stdout.write("=" * 72)

        self.stdout.write("\nCurrent CRM models:")

        for item in manifest["crm_models"]:
            self.stdout.write(
                f"  {item['model']}: {item['count']} record(s) [{item['table']}]"
            )

        self.stdout.write("\nDirect CRM references:")

        if not manifest["direct_references"]:
            self.stdout.write("  None discovered.")
        else:
            for reference in manifest["direct_references"]:
                relationship = (
                    f"{reference['model']}.{reference['field']} -> "
                    f"{reference['target_model']}"
                )

                self.stdout.write(
                    f"  {relationship}: {reference['count']} row(s), "
                    f"on_delete={reference['on_delete']}"
                )

        self.stdout.write("\nRows Django plans to delete:")

        if not manifest["delete_plan"]:
            self.stdout.write("  None.")
        else:
            for item in manifest["delete_plan"]:
                self.stdout.write(f"  {item['model']}: {item['count']} row(s)")

        self.stdout.write("\nRows Django plans to update:")

        if not manifest["field_update_plan"]:
            self.stdout.write("  None.")
        else:
            for item in manifest["field_update_plan"]:
                self.stdout.write(
                    f"  {item['model']}.{item['field']}: "
                    f"{item['count']} row(s) -> {item['new_value']}"
                )

        self.stdout.write("\nDeletion blockers:")

        if not manifest["blockers"]:
            self.stdout.write("  None discovered.")
        else:
            for blocker in manifest["blockers"]:
                self.stdout.write(
                    self.style.ERROR(f"  {json.dumps(blocker, default=str)}")
                )

        self.stdout.write(
            "\nNo table will be dropped by this command. Schema replacement "
            "will be performed by reviewed Django migrations in Stage 2."
        )
