from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group

from .models import User, StaffProfile, Department, Role, AuditLog, PasswordResetCode
from .models import (
    AuditLog,
    Department,
    DepartmentLeadership,
    PasswordResetCode,
    Role,
    StaffProfile,
    User,
)

# --- Inline Admin Definitions ---


class StaffProfileInline(admin.StackedInline):
    """
    Defines an inline admin descriptor for StaffProfile objects.
    This allows StaffProfile to be edited directly within the User admin page.
    """

    model = StaffProfile
    can_delete = False
    verbose_name_plural = "Staff Profile"
    fk_name = "user"


# --- Custom ModelAdmin Definitions ---


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Custom User admin configuration.
    Integrates the StaffProfile inline for a unified editing experience.
    """

    inlines = (StaffProfileInline,)
    list_display = ("username", "email", "first_name", "last_name", "is_staff")
    list_select_related = ("profile",)
    search_fields = (
        "username",
        "email",
        "first_name",
        "last_name",
    )


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Department model.
    """

    list_display = ("name", "code")
    search_fields = ("name", "code")


@admin.register(DepartmentLeadership)
class DepartmentLeadershipAdmin(admin.ModelAdmin):
    list_display = (
        "department",
        "manager",
        "leadership_type",
        "is_primary",
        "active_from",
        "active_until",
        "is_active",
    )

    list_filter = (
        "leadership_type",
        "is_primary",
        "department",
        "active_from",
        "active_until",
    )

    search_fields = (
        "department__name",
        "department__code",
        "manager__employee_id",
        "manager__user__username",
        "manager__user__email",
        "manager__user__first_name",
        "manager__user__last_name",
    )

    autocomplete_fields = (
        "department",
        "manager",
        "created_by",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = (
        "employee_id",
        "user",
        "job_title",
        "department",
        "reports_to",
        "employment_type",
        "start_date",
        "end_date",
    )

    list_filter = (
        "department",
        "employment_type",
        "sex",
    )

    search_fields = (
        "employee_id",
        "user__username",
        "user__email",
        "user__first_name",
        "user__last_name",
        "job_title",
        "reports_to__employee_id",
        "reports_to__user__first_name",
        "reports_to__user__last_name",
    )

    autocomplete_fields = (
        "user",
        "department",
        "reports_to",
    )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "user",
        "target_user",
        "username_attempted",
        "event_category",
        "event_type",
        "status",
        "ip_address",
    )
    list_filter = (
        "event_category",
        "event_type",
        "status",
        "created_at",
    )
    search_fields = (
        "user__username",
        "user__email",
        "target_user__username",
        "target_user__email",
        "username_attempted",
        "ip_address",
        "user_agent",
    )
    readonly_fields = (
        "user",
        "target_user",
        "event_category",
        "event_type",
        "status",
        "username_attempted",
        "app_label",
        "resource",
        "action",
        "object_id",
        "ip_address",
        "user_agent",
        "metadata",
        "created_at",
    )


@admin.register(PasswordResetCode)
class PasswordResetCodeAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "user",
        "purpose",
        "expires_at",
        "opened_at",
        "used_at",
    )
    list_filter = (
        "purpose",
        "created_at",
        "expires_at",
        "opened_at",
        "used_at",
    )
    search_fields = (
        "user__username",
        "user__email",
    )
    readonly_fields = (
        "user",
        "token_hash",
        "purpose",
        "created_at",
        "expires_at",
        "opened_at",
        "used_at",
        "ip_address",
        "user_agent",
    )


# Unregister the default Group model and register our Role proxy model
admin.site.unregister(Group)
admin.site.register(Role)
