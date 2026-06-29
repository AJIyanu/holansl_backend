from django.db.models import Q
from django.utils import timezone

from rest_framework.permissions import BasePermission

from accounts.models import StaffProfile


class IsActiveStaff(BasePermission):
    """
    Requires an active authenticated user with a currently
    active staff profile.
    """

    message = "An active staff profile is required to access task management."

    def has_permission(self, request, view):
        user = request.user

        if (
            not user
            or not user.is_authenticated
            or not user.is_active
            or not user.is_staff
        ):
            return False

        today = timezone.localdate()

        return (
            StaffProfile.objects.filter(
                user=user,
                start_date__lte=today,
            )
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
            .exists()
        )
