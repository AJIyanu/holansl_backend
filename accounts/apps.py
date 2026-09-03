from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from django.contrib.auth.models import Group, Permission
        from django.db.models.signals import post_migrate, post_save, pre_migrate
        from .models import Role
        from .executive_permissions import (
            executive_created,
            permission_created,
            snapshot_permissions,
            grant_migration_permissions,
        )

        post_save.connect(
            permission_created,
            sender=Permission,
            dispatch_uid="accounts.new_permission",
        )
        for model in (Group, Role):
            post_save.connect(
                executive_created,
                sender=model,
                dispatch_uid=f"accounts.executive_created.{model._meta.label}",
            )
        pre_migrate.connect(
            snapshot_permissions, dispatch_uid="accounts.permission_snapshot"
        )
        post_migrate.connect(
            grant_migration_permissions, dispatch_uid="accounts.migration_permissions"
        )
