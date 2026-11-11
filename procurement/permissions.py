from rest_framework.permissions import BasePermission, SAFE_METHODS


class ProcurementPermission(BasePermission):
    message = "You do not have permission to use this Procurement feature."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if not (user.is_staff or user.is_superuser):
            return False
        if user.is_superuser:
            return True
        action = getattr(view, "action", None)
        explicit = getattr(view, "action_permissions", {}).get(action)
        if isinstance(explicit, dict):
            explicit = explicit.get(request.method)
        if explicit:
            return user.has_perm(explicit)
        model = view.queryset.model
        operation = "view" if request.method in SAFE_METHODS else "add"
        return user.has_perm(f"procurement.{operation}_{model._meta.model_name}")
