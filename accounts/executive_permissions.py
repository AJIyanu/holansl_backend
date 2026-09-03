"""Additive executive permission provisioning; never reconcile revoked grants."""

from django.contrib.auth.models import Group, Permission
from django.apps import apps
from django.db import connections, router, transaction
from django.db.models import Q

EXECUTIVE_NAMES = ("CEO", "CTO")
_migration_permissions = {}


def executive_groups(using):
    return Group.objects.using(using).filter(
        Q(name__iexact="CEO") | Q(name__iexact="CTO")
    )


def grant_new_permissions(permission_ids, *, using="default"):
    """Grant only the supplied newly created IDs, never all existing permissions."""
    ids = tuple(permission_ids)
    if ids:
        for group in executive_groups(using):
            group.permissions.add(*ids)


def permission_created(sender, instance, created, raw=False, using="default", **kwargs):
    if created and not raw:
        grant_new_permissions([instance.pk], using=using)


def executive_created(sender, instance, created, raw=False, using="default", **kwargs):
    if created and not raw and instance.name.upper() in EXECUTIVE_NAMES:
        instance.permissions.add(
            *Permission.objects.using(using).values_list("pk", flat=True)
        )


def snapshot_permissions(sender, using="default", **kwargs):
    """Capture IDs before migrations, including an initially empty auth schema."""
    if not router.allow_migrate_model(using, Permission):
        return
    tables = connections[using].introspection.table_names()
    _migration_permissions[using] = (
        set(Permission.objects.using(using).values_list("pk", flat=True))
        if Permission._meta.db_table in tables
        else set()
    )


def grant_migration_permissions(sender, using="default", **kwargs):
    """Django bulk-creates permissions in post_migrate, bypassing post_save."""
    if using not in _migration_permissions or not router.allow_migrate_model(
        using, Permission
    ):
        return
    current = set(Permission.objects.using(using).values_list("pk", flat=True))
    grant_new_permissions(current - _migration_permissions[using], using=using)
    _migration_permissions[using] = current
    emitting_apps = [
        config for config in apps.get_app_configs() if config.models_module is not None
    ]
    if emitting_apps and sender is emitting_apps[-1]:
        _migration_permissions.pop(using, None)


def bootstrap_executives(*, using="default"):
    """Explicit operator action: grant today's entire permission set once."""
    with transaction.atomic(using=using):
        ids = tuple(Permission.objects.using(using).values_list("pk", flat=True))
        for name in EXECUTIVE_NAMES:
            matches = list(executive_groups(using).filter(name__iexact=name))
            if not matches:
                matches = [Group.objects.using(using).create(name=name)]
            for group in matches:
                group.permissions.add(*ids)
    return len(ids)
