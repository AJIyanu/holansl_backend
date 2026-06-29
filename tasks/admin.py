from django.contrib import admin

from .models import (
    Task,
    TaskActivity,
    TaskBatch,
    TaskComment,
    TaskReminder,
)


class TaskInline(admin.TabularInline):
    model = Task
    extra = 0
    show_change_link = True

    fields = (
        "assigned_to",
        "assignee_name",
        "assigned_department",
        "status",
        "completed_at",
        "cancelled_at",
        "archived_at",
    )

    readonly_fields = fields
    can_delete = False

    def has_add_permission(
        self,
        request,
        obj=None,
    ):
        return False


@admin.register(TaskBatch)
class TaskBatchAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "assignment_type",
        "priority",
        "source_department",
        "created_by",
        "due_at",
        "cancelled_at",
        "archived_at",
        "created_at",
    )

    list_filter = (
        "assignment_type",
        "priority",
        "source_department",
        "cancelled_at",
        "archived_at",
        "created_at",
    )

    search_fields = (
        "title",
        "description",
        "source_department_name",
        "created_by__username",
        "created_by__email",
        "created_by_name",
    )

    autocomplete_fields = (
        "source_department",
        "created_by",
        "cancelled_by",
        "archived_by",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    date_hierarchy = "created_at"
    inlines = [TaskInline]

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return False


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        "batch",
        "assigned_to",
        "assignee_name",
        "assigned_department",
        "status",
        "completed_at",
        "cancelled_at",
        "archived_at",
        "created_at",
    )

    list_filter = (
        "status",
        "assigned_department",
        "batch__priority",
        "archived_at",
        "created_at",
    )

    search_fields = (
        "batch__title",
        "batch__description",
        "assigned_to__username",
        "assigned_to__email",
        "assignee_name",
        "assignee_email",
        "assignee_employee_id",
        "assigned_department_name",
    )

    autocomplete_fields = (
        "batch",
        "assigned_to",
        "assigned_department",
        "cancelled_by",
        "archived_by",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "batch",
        "assigned_to",
        "assigned_department",
    )

    date_hierarchy = "created_at"

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return False


@admin.register(TaskComment)
class TaskCommentAdmin(admin.ModelAdmin):
    list_display = (
        "task",
        "author",
        "is_removed",
        "edited_at",
        "created_at",
    )

    list_filter = (
        "removed_at",
        "edited_at",
        "created_at",
    )

    search_fields = (
        "task__batch__title",
        "author__username",
        "author__email",
        "body",
        "removal_reason",
    )

    autocomplete_fields = (
        "task",
        "author",
        "removed_by",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return False


@admin.register(TaskReminder)
class TaskReminderAdmin(admin.ModelAdmin):
    list_display = (
        "task",
        "user",
        "remind_at",
        "channels",
        "notification",
        "cancelled_at",
        "created_at",
    )

    list_filter = (
        "cancelled_at",
        "remind_at",
        "created_at",
    )

    search_fields = (
        "task__batch__title",
        "user__username",
        "user__email",
    )

    autocomplete_fields = (
        "task",
        "user",
        "notification",
        "cancelled_by",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return False


@admin.register(TaskActivity)
class TaskActivityAdmin(admin.ModelAdmin):
    list_display = (
        "activity_type",
        "task",
        "batch",
        "actor",
        "created_at",
    )

    list_filter = (
        "activity_type",
        "created_at",
    )

    search_fields = (
        "task__batch__title",
        "batch__title",
        "actor__username",
        "actor__email",
    )

    readonly_fields = (
        "id",
        "task",
        "batch",
        "actor",
        "activity_type",
        "previous_value",
        "new_value",
        "metadata",
        "created_at",
    )

    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(
        self,
        request,
        obj=None,
    ):
        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return False
