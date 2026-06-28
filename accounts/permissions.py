from django.conf import settings
from rest_framework.permissions import SAFE_METHODS, BasePermission


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
