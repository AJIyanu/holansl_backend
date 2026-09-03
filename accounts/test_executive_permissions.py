from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.executive_permissions import (
    _migration_permissions,
    bootstrap_executives,
    grant_migration_permissions,
    snapshot_permissions,
)
from accounts.models import Role


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ExecutivePermissionTests(APITestCase):
    def setUp(self):
        self.ceo = Role.objects.create(name="CEO")
        self.cto = Group.objects.create(name="CTO")
        self.user = get_user_model().objects.create_user(
            username="exec", email="exec@example.invalid", is_staff=True
        )
        self.user.groups.add(self.ceo)
        self.target = get_user_model().objects.create_user(
            username="target", email="target@example.invalid", is_staff=True
        )
        self.content_type = ContentType.objects.get_for_model(get_user_model())
        self.addCleanup(_migration_permissions.clear)

    def permission(self, code):
        return Permission.objects.get(content_type=self.content_type, codename=code)

    def authenticate(self):
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_authenticate(self.user)

    def test_new_roles_have_all_permissions(self):
        expected = set(Permission.objects.values_list("pk", flat=True))
        for group in (self.ceo, self.cto):
            self.assertEqual(
                set(group.permissions.values_list("pk", flat=True)), expected
            )

    def test_only_new_permissions_are_granted(self):
        old = self.permission("change_user")
        self.ceo.permissions.remove(old)
        new = Permission.objects.create(
            content_type=self.content_type, codename="new_test_permission", name="New"
        )
        for role in (self.ceo, self.cto):
            self.assertTrue(role.permissions.filter(pk=new.pk).exists())
        self.assertFalse(self.ceo.permissions.filter(pk=old.pk).exists())
        self.ceo.permissions.remove(new)
        new.name = "Renamed"
        new.save()
        self.assertFalse(self.ceo.permissions.filter(pk=new.pk).exists())

    def test_bulk_migration_permissions_do_not_restore_old_grants(self):
        old = self.permission("change_user")
        self.ceo.permissions.remove(old)
        snapshot_permissions(None)
        Permission.objects.bulk_create(
            [
                Permission(
                    content_type=self.content_type,
                    codename="migration_permission",
                    name="Migration",
                )
            ]
        )
        grant_migration_permissions(None)
        new = Permission.objects.get(codename="migration_permission")
        self.assertTrue(self.ceo.permissions.filter(pk=new.pk).exists())
        self.assertTrue(self.cto.permissions.filter(pk=new.pk).exists())
        self.assertFalse(self.ceo.permissions.filter(pk=old.pk).exists())
        self.ceo.permissions.remove(new)
        grant_migration_permissions(None)
        self.assertFalse(self.ceo.permissions.filter(pk=new.pk).exists())
        snapshot_permissions(None)
        grant_migration_permissions(None)
        self.assertFalse(self.ceo.permissions.filter(pk=old.pk).exists())

    def test_bootstrap_requires_apply_and_seeding_preserves_removals(self):
        old = self.permission("change_user")
        self.ceo.permissions.remove(old)
        call_command("bootstrap_executive_permissions", stdout=StringIO())
        self.assertFalse(self.ceo.permissions.filter(pk=old.pk).exists())
        bootstrap_executives()
        self.assertTrue(self.ceo.permissions.filter(pk=old.pk).exists())
        self.ceo.permissions.remove(old)
        call_command("create_default_account_data", stdout=StringIO())
        self.assertFalse(self.ceo.permissions.filter(pk=old.pk).exists())

    def test_put_and_patch_require_permission_not_role_name(self):
        for method in ("put", "patch"):
            for allowed in (True, False):
                with self.subTest(method=method, allowed=allowed):
                    permission = self.permission("change_user")
                    if allowed:
                        self.ceo.permissions.add(permission)
                    else:
                        self.ceo.permissions.remove(permission)
                    self.authenticate()
                    response = getattr(self.client, method)(
                        f"/account/users/{self.target.pk}/",
                        {
                            "username": self.target.username,
                            "email": self.target.email,
                            "first_name": "Updated",
                        },
                        format="json",
                    )
                    self.assertEqual(response.status_code, 200 if allowed else 403)

    def test_role_name_alone_grants_no_security_access(self):
        self.ceo.permissions.clear()
        self.authenticate()
        for path in (
            "/account/audit-logs/",
            "/account/audit-logs/summary/",
            "/account/login-activity/ai-insight/",
            "/account/department-leadership/",
            "/account/roles/",
            "/account/permissions/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

    def test_ordinary_staff_with_permission_can_update(self):
        self.user.groups.clear()
        self.user.user_permissions.add(self.permission("change_user"))
        self.authenticate()
        response = self.client.patch(
            f"/account/users/{self.target.pk}/", {"first_name": "Allowed"}
        )
        self.assertEqual(response.status_code, 200)

    def test_cannot_assign_role_with_permissions_actor_lacks(self):
        self.ceo.permissions.remove(self.permission("delete_user"))
        self.authenticate()
        response = self.client.patch(
            f"/account/users/{self.target.pk}/", {"roles": [self.cto.pk]}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_superuser_without_role_and_permanent_delete_guard(self):
        self.authenticate()
        self.assertEqual(
            self.client.delete(f"/account/users/{self.target.pk}/").status_code, 403
        )
        self.user.groups.clear()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.authenticate()
        self.assertEqual(
            self.client.patch(
                f"/account/users/{self.target.pk}/", {"first_name": "Allowed"}
            ).status_code,
            200,
        )

    def test_non_superuser_cannot_modify_superuser(self):
        self.target.is_superuser = True
        self.target.save(update_fields=["is_superuser"])
        self.authenticate()
        self.assertEqual(
            self.client.patch(
                f"/account/users/{self.target.pk}/", {"is_active": False}
            ).status_code,
            403,
        )

    def test_cannot_create_executive_role_to_gain_missing_permissions(self):
        self.cto.delete()
        self.ceo.permissions.remove(self.permission("delete_user"))
        self.authenticate()
        response = self.client.post(
            "/account/roles/", {"name": "CTO", "permissions": []}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    @patch("accounts.views.get_cached_audit_summary", return_value={"count": 0})
    def test_security_summary_checks_permission_without_requiring_role(self, summary):
        self.user.groups.clear()
        self.user.user_permissions.add(
            Permission.objects.get(
                codename="view_auditlog", content_type__app_label="accounts"
            )
        )
        self.authenticate()
        self.assertEqual(
            self.client.get("/account/audit-logs/summary/").status_code, 200
        )
        summary.assert_called_once()

    def test_role_creation_cannot_drop_initial_executive_baseline(self):
        self.cto.delete()
        self.authenticate()
        response = self.client.post(
            "/account/roles/", {"name": "CTO", "permissions": []}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            Group.objects.get(name="CTO").permissions.count(),
            Permission.objects.count(),
        )

    def test_cannot_rename_role_into_executive_for_future_grants(self):
        self.cto.delete()
        ordinary = Role.objects.create(name="Ordinary")
        self.ceo.permissions.remove(self.permission("delete_user"))
        self.authenticate()
        response = self.client.patch(
            f"/account/roles/{ordinary.pk}/", {"name": "CTO"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
