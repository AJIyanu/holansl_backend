"""
CRM permission classes.

This module maps CRM API actions to Django model permissions and ensures that
read operations also require the appropriate Django ``view`` permission.

Permission classes accept a DRF request and view and return a boolean
indicating whether access should be granted.
"""

from rest_framework.permissions import (
    BasePermission,
)

from accounts.permissions import StrictDjangoModelPermissions

# class StrictDjangoModelPermissions(DjangoModelPermissions):
#     """
#     Require Django model permissions for every HTTP method.

#     Accepts:
#         An authenticated DRF request and a view exposing a model queryset.

#     Returns:
#         True when the user has all required permissions; otherwise False.
#     """

#     perms_map = {
#         "GET": [
#             "%(app_label)s.view_%(model_name)s",
#         ],
#         "OPTIONS": [],
#         "HEAD": [],
#         "POST": [
#             "%(app_label)s.add_%(model_name)s",
#         ],
#         "PUT": [
#             "%(app_label)s.change_%(model_name)s",
#         ],
#         "PATCH": [
#             "%(app_label)s.change_%(model_name)s",
#         ],
#         "DELETE": [
#             "%(app_label)s.delete_%(model_name)s",
#         ],
#     }


class PartyActionPermission(BasePermission):
    """
    Authorise PartyViewSet actions using explicit CRM party permissions.

    Accepts:
        An authenticated DRF request, the PartyViewSet and optionally a party.

    Returns:
        True when the user has the permission required by the current action.
    """

    message = "You do not have permission to perform this CRM action."

    action_permissions = {
        "list": "crm.view_party",
        "retrieve": "crm.view_party",
        "history": "crm.view_party_history",
        "duplicate_check": "crm.view_party",
        "create": "crm.add_party",
        "quick_create": "crm.add_party",
        "update": "crm.change_party",
        "partial_update": "crm.change_party",
        "destroy": "crm.delete_party",
        "deactivate": "crm.deactivate_party",
        "reactivate": "crm.deactivate_party",
        "suspend": "crm.block_party",
        "block": "crm.block_party",
        "archive": "crm.archive_party",
        "restore": "crm.archive_party",
        "merge": "crm.merge_party",
    }

    def has_permission(self, request, view) -> bool:
        """
        Check authentication and the permission required by the view action.

        Accepts:
            A DRF request and PartyViewSet instance.

        Returns:
            True when the action is permitted; otherwise False.
        """

        user = request.user

        if not user or not user.is_authenticated or not user.is_active:
            return False

        if user.is_superuser:
            return True

        action = getattr(view, "action", None)

        if action == "reactivate":
            return user.has_perm("crm.deactivate_party") or user.has_perm(
                "crm.block_party"
            )

        required_permission = self.action_permissions.get(action)

        if required_permission is None:
            return False

        return user.has_perm(required_permission)

    def has_object_permission(
        self,
        request,
        view,
        obj,
    ) -> bool:
        """
        Apply status-aware permission checks to a specific CRM party.

        Accepts:
            A DRF request, PartyViewSet instance and Party model instance.

        Returns:
            True when the user may perform the action on that party.
        """

        user = request.user

        if user.is_superuser:
            return True

        if getattr(view, "action", None) == "reactivate":
            if obj.status in {
                obj.Status.BLOCKED,
                obj.Status.SUSPENDED,
            }:
                return user.has_perm("crm.block_party")

            return user.has_perm("crm.deactivate_party")

        return self.has_permission(request, view)


class SensitiveIdentifierPermission(StrictDjangoModelPermissions):
    """Enforce masked and revealed identifier access separately."""

    def has_permission(
        self,
        request,
        view,
    ) -> bool:
        """
        Check model and sensitive identifier permissions.

        Args:
            request: DRF request.
            view: PartyIdentifierViewSet instance.

        Returns:
            bool: Whether access is allowed.
        """

        if not super().has_permission(
            request,
            view,
        ):
            return False

        user = request.user

        if user.is_superuser:
            return True

        action = getattr(
            view,
            "action",
            None,
        )

        if action == "reveal":
            return user.has_perm("crm.view_sensitive_partyidentifier")

        if action in {
            "create",
            "update",
            "partial_update",
            "destroy",
        }:
            return user.has_perm("crm.manage_sensitive_partyidentifier")

        return True


class SensitiveBankAccountPermission(StrictDjangoModelPermissions):
    """Enforce masked and revealed payment-detail access separately."""

    def has_permission(
        self,
        request,
        view,
    ) -> bool:
        """
        Check model and sensitive bank-detail permissions.

        Args:
            request: DRF request.
            view: PartyBankAccountViewSet instance.

        Returns:
            bool: Whether access is allowed.
        """

        if not super().has_permission(
            request,
            view,
        ):
            return False

        user = request.user

        if user.is_superuser:
            return True

        action = getattr(
            view,
            "action",
            None,
        )

        if action == "reveal":
            return user.has_perm("crm.view_sensitive_partybankaccount")

        if action in {
            "create",
            "update",
            "partial_update",
            "destroy",
        }:
            return user.has_perm("crm.manage_sensitive_partybankaccount")

        return True


class PartyDocumentPermission(StrictDjangoModelPermissions):
    """Enforce ordinary, download and confidential-document permissions."""

    def has_permission(
        self,
        request,
        view,
    ) -> bool:
        """
        Check model-level document permissions.

        Args:
            request: DRF request.
            view: PartyDocumentViewSet instance.

        Returns:
            bool: Whether model-level access is allowed.
        """

        if not super().has_permission(
            request,
            view,
        ):
            return False

        if request.user.is_superuser:
            return True

        if (
            getattr(
                view,
                "action",
                None,
            )
            == "download"
        ):
            return request.user.has_perm("crm.download_partydocument")

        return True

    def has_object_permission(
        self,
        request,
        view,
        obj,
    ) -> bool:
        """
        Check confidential-document access for a specific record.

        Args:
            request: DRF request.
            view: PartyDocumentViewSet instance.
            obj: PartyDocument instance.

        Returns:
            bool: Whether the user may access the document.
        """

        if request.user.is_superuser:
            return True

        if not obj.is_confidential:
            return True

        action = getattr(
            view,
            "action",
            None,
        )

        if action in {
            "update",
            "partial_update",
            "destroy",
        }:
            return request.user.has_perm("crm.manage_confidential_partydocument")

        return request.user.has_perm("crm.view_confidential_partydocument")
