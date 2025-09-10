from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group

from .models import User, StaffProfile, Department, Role

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

# Unregister the default Group model and register our Role proxy model
admin.site.unregister(Group)
admin.site.register(Role)