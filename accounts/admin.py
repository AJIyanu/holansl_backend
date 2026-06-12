from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group

from .models import User, StaffProfile, Department, Role, AuditLog

# --- Inline Admin Definitions ---

class StaffProfileInline(admin.StackedInline):
    """
    Defines an inline admin descriptor for StaffProfile objects.
    This allows StaffProfile to be edited directly within the User admin page.
    """
    model = StaffProfile
    can_delete = False
    verbose_name_plural = 'Staff Profile'
    fk_name = 'user'

# --- Custom ModelAdmin Definitions ---

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Custom User admin configuration.
    Integrates the StaffProfile inline for a unified editing experience.
    """
    inlines = (StaffProfileInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff')
    list_select_related = ('profile',)

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Department model.
    """
    list_display = ('name', 'code')
    search_fields = ('name', 'code')

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "user",
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
        "username_attempted",
        "ip_address",
        "user_agent",
    )
    readonly_fields = (
        "user",
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

# Unregister the default Group model and register our Role proxy model
admin.site.unregister(Group)
admin.site.register(Role)