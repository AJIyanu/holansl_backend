from rest_framework.permissions import BasePermission


class CanDispatchNotifications(BasePermission):
    message = "You do not have permission to dispatch notifications."

    def has_permission(self, request, view):
        user = request.user

        return bool(
            user
            and user.is_authenticated
            and (
                user.is_superuser
                or user.has_perm("notifications.dispatch_notification")
            )
        )


class CanManageNotificationTemplates(BasePermission):
    message = "You do not have permission to manage notification templates."

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        if request.method in {
            "GET",
            "HEAD",
            "OPTIONS",
        }:
            return user.has_perm("notifications.view_notificationtemplate")

        return user.has_perm("notifications.manage_notificationtemplate")


class CanViewNotificationDeliveries(BasePermission):
    message = "You do not have permission to view notification deliveries."

    def has_permission(self, request, view):
        user = request.user

        return bool(
            user
            and user.is_authenticated
            and (
                user.is_superuser
                or user.has_perm("notifications.view_notificationdelivery")
            )
        )


class CanRetryNotificationDeliveries(BasePermission):
    message = "You do not have permission to retry notification deliveries."

    def has_permission(self, request, view):
        user = request.user

        return bool(
            user
            and user.is_authenticated
            and (
                user.is_superuser
                or user.has_perm("notifications.retry_notificationdelivery")
            )
        )
