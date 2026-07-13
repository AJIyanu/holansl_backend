"""
Shared Django REST Framework permission classes.

These permission classes are application-neutral and may be used by any
backend app that relies on standard Django model permissions.
"""

from django.conf import settings
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.permissions import DjangoModelPermissions


def can_manage_staff_security(user):
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    user_roles = {
        name.casefold() for name in user.groups.values_list("name", flat=True)
    }

    return bool(user_roles & settings.STAFF_MANAGEMENT_ROLES)


class CanViewAuditLogs(BasePermission):
    """
    Superusers, CEO/CTO, or users with accounts.view_auditlog.
    """

    def has_permission(self, request, view):
        user = request.user

        return bool(
            user
            and user.is_authenticated
            and (
                can_manage_staff_security(user)
                or user.has_perm("accounts.view_auditlog")
            )
        )


class IsSecurityExecutive(BasePermission):
    """
    Allows only Django superusers and users with configured
    executive roles such as CEO or CTO.
    """

    message = "You do not have permission to access this resource."

    def has_permission(self, request, view):
        return can_manage_staff_security(request.user)


class CanManageDepartmentLeadership(BasePermission):
    message = "You do not have permission to manage department leadership."

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        if request.method in SAFE_METHODS:
            return bool(
                can_manage_staff_security(user)
                or user.has_perm("accounts.view_departmentleadership")
            )

        return bool(
            can_manage_staff_security(user)
            or user.has_perm("accounts.manage_departmentleadership")
        )


class StrictDjangoModelPermissions(DjangoModelPermissions):
    """
    Require Django model permissions for every supported HTTP method.

    Unlike DRF's default DjangoModelPermissions class, GET requests require
    the model's ``view`` permission.

    Attributes:
        perms_map: Mapping between HTTP methods and Django permissions.
    """

    perms_map = {
        "GET": [
            "%(app_label)s.view_%(model_name)s",
        ],
        "OPTIONS": [],
        "HEAD": [],
        "POST": [
            "%(app_label)s.add_%(model_name)s",
        ],
        "PUT": [
            "%(app_label)s.change_%(model_name)s",
        ],
        "PATCH": [
            "%(app_label)s.change_%(model_name)s",
        ],
        "DELETE": [
            "%(app_label)s.delete_%(model_name)s",
        ],
    }
