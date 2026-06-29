from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone

from accounts.models import (
    Department,
    DepartmentLeadership,
    StaffProfile,
)
from tasks.constants import TaskAssignmentType
from tasks.models import Task, TaskBatch

ALL_VIEW_PERMISSIONS = {
    "tasks.view_all_task",
    "tasks.manage_all_task",
}

ALL_ASSIGN_PERMISSIONS = {
    "tasks.assign_all_task",
    "tasks.manage_all_task",
}

ALL_MANAGE_PERMISSIONS = {
    "tasks.manage_all_task",
}

DEPARTMENT_VIEW_PERMISSIONS = {
    "tasks.view_department_task",
    "tasks.manage_department_task",
}

DEPARTMENT_ASSIGN_PERMISSIONS = {
    "tasks.assign_department_task",
    "tasks.manage_department_task",
}

DEPARTMENT_MANAGE_PERMISSIONS = {
    "tasks.manage_department_task",
}


def has_any_permission(user, permissions):
    if not user or not user.is_authenticated or not user.is_active:
        return False

    if user.is_superuser:
        return True

    return any(user.has_perm(permission) for permission in permissions)


def get_staff_profile(user):
    if not user or not user.is_authenticated:
        return None

    try:
        return StaffProfile.objects.select_related(
            "user",
            "department",
            "reports_to",
        ).get(user=user)
    except StaffProfile.DoesNotExist:
        return None


def active_staff_profiles():
    today = timezone.localdate()

    return (
        StaffProfile.objects.select_related(
            "user",
            "department",
            "reports_to",
        )
        .filter(
            user__is_active=True,
            user__is_staff=True,
            start_date__lte=today,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
    )


def can_cancel_task(user, task):
    if not user or not user.is_authenticated or not user.is_active:
        return False

    if (
        task.batch.assignment_type == TaskAssignmentType.PERSONAL
        and task.assigned_to_id == user.id
    ):
        return True

    return can_manage_task(user, task)


def can_archive_task(user, task):
    if not user or not user.is_authenticated or not user.is_active:
        return False

    if task.assigned_to_id == user.id:
        return True

    return can_manage_task(user, task)


def can_restore_task(user, task):
    return can_archive_task(user, task)


def get_managed_department_ids(user):
    """
    Return departments for which the user has an active
    DepartmentLeadership record.
    """

    profile = get_staff_profile(user)

    if profile is None:
        return set()

    today = timezone.localdate()

    return set(
        DepartmentLeadership.objects.filter(
            manager=profile,
            active_from__lte=today,
        )
        .filter(Q(active_until__isnull=True) | Q(active_until__gte=today))
        .values_list(
            "department_id",
            flat=True,
        )
    )


def get_reporting_descendant_profile_ids(user):
    """
    Recursively find all staff profiles below the user's
    profile in the reports_to hierarchy.

    This includes indirect reports.
    """

    profile = get_staff_profile(user)

    if profile is None:
        return set()

    descendant_ids = set()
    current_manager_ids = {profile.id}

    while current_manager_ids:
        next_profile_ids = set(
            StaffProfile.objects.filter(
                reports_to_id__in=current_manager_ids
            ).values_list(
                "id",
                flat=True,
            )
        )

        next_profile_ids -= descendant_ids

        if not next_profile_ids:
            break

        descendant_ids.update(next_profile_ids)
        current_manager_ids = next_profile_ids

    return descendant_ids


def get_reporting_descendant_user_ids(user):
    profile_ids = get_reporting_descendant_profile_ids(user)

    if not profile_ids:
        return set()

    return set(
        StaffProfile.objects.filter(id__in=profile_ids).values_list(
            "user_id",
            flat=True,
        )
    )


def can_view_all_tasks(user):
    return has_any_permission(
        user,
        ALL_VIEW_PERMISSIONS,
    )


def can_assign_across_organisation(user):
    return has_any_permission(
        user,
        ALL_ASSIGN_PERMISSIONS,
    )


def can_manage_all_tasks(user):
    return has_any_permission(
        user,
        ALL_MANAGE_PERMISSIONS,
    )


def can_view_department_tasks(user):
    return has_any_permission(
        user,
        DEPARTMENT_VIEW_PERMISSIONS,
    )


def can_assign_department_tasks(user):
    return has_any_permission(
        user,
        DEPARTMENT_ASSIGN_PERMISSIONS,
    )


def can_manage_department_tasks(user):
    return has_any_permission(
        user,
        DEPARTMENT_MANAGE_PERMISSIONS,
    )


def get_management_scope_user_ids(user):
    """
    Users controlled through either:

    1. an active department leadership; or
    2. the reports_to hierarchy.
    """

    user_ids = get_reporting_descendant_user_ids(user)

    department_ids = get_managed_department_ids(user)

    if department_ids:
        user_ids.update(
            StaffProfile.objects.filter(department_id__in=department_ids).values_list(
                "user_id",
                flat=True,
            )
        )

    return user_ids


def department_queryset_for_assignment(user):
    if can_assign_across_organisation(user):
        return Department.objects.all()

    if not can_assign_department_tasks(user):
        return Department.objects.none()

    return Department.objects.filter(id__in=get_managed_department_ids(user))


def can_assign_to_department(user, department):
    if not user or not user.is_authenticated or not user.is_active:
        return False

    if can_assign_across_organisation(user):
        return True

    if not can_assign_department_tasks(user):
        return False

    return department.id in get_managed_department_ids(user)


def can_assign_to_user(user, target_user):
    if (
        not user
        or not user.is_authenticated
        or not user.is_active
        or not target_user
        or not target_user.is_active
    ):
        return False

    if can_assign_across_organisation(user):
        return True

    if not user.has_perm("tasks.assign_task"):
        return False

    target_profile = get_staff_profile(target_user)

    if target_profile is None:
        return False

    managed_department_ids = get_managed_department_ids(user)

    if target_profile.department_id in managed_department_ids:
        return True

    descendant_user_ids = get_reporting_descendant_user_ids(user)

    return target_user.id in descendant_user_ids


def base_task_queryset():
    return Task.objects.select_related(
        "batch",
        "batch__created_by",
        "batch__created_by__profile",
        "batch__created_by__profile__department",
        "batch__source_department",
        "assigned_to",
        "assigned_to__profile",
        "assigned_to__profile__department",
        "assigned_department",
        "cancelled_by",
        "cancelled_by__profile",
        "cancelled_by__profile__department",
        "archived_by",
        "archived_by__profile",
        "archived_by__profile__department",
    )


def visible_tasks_for(
    user,
    *,
    scope=None,
    include_archived=None,
):
    """
    Return the task rows visible to a user.

    Supported scopes:
    - my
    - created
    - department
    - all

    With no scope, all rows visible through any allowed
    relationship are returned.
    """

    queryset = base_task_queryset()

    if not user or not user.is_authenticated or not user.is_active:
        return queryset.none()

    if include_archived is True:
        pass
    elif include_archived is False:
        queryset = queryset.filter(archived_at__isnull=True)

    if scope == "my":
        return queryset.filter(assigned_to=user)

    if scope == "created":
        return queryset.filter(batch__created_by=user)

    if scope == "all":
        if not can_view_all_tasks(user):
            raise PermissionDenied(
                "You do not have permission to view all organisation tasks."
            )

        return queryset

    managed_user_ids = set()
    managed_department_ids = set()

    if can_view_department_tasks(user):
        managed_user_ids = get_management_scope_user_ids(user)

        managed_department_ids = get_managed_department_ids(user)

    if scope == "department":
        if not can_view_department_tasks(user):
            raise PermissionDenied(
                "You do not have permission to view department tasks."
            )

        return queryset.filter(
            Q(assigned_to_id__in=managed_user_ids)
            | Q(assigned_department_id__in=managed_department_ids)
        ).distinct()

    if can_view_all_tasks(user):
        return queryset

    visibility = Q(assigned_to=user) | Q(batch__created_by=user)

    if can_view_department_tasks(user):
        visibility |= Q(assigned_to_id__in=managed_user_ids)

        visibility |= Q(assigned_department_id__in=managed_department_ids)

    return queryset.filter(visibility).distinct()


def visible_batches_for(
    user,
    *,
    scope=None,
    include_archived=None,
):
    queryset = TaskBatch.objects.select_related(
        "created_by",
        "created_by__profile",
        "created_by__profile__department",
        "source_department",
        "cancelled_by",
        "cancelled_by__profile",
        "cancelled_by__profile__department",
        "archived_by",
        "archived_by__profile",
        "archived_by__profile__department",
    )

    if not user or not user.is_authenticated or not user.is_active:
        return queryset.none()

    if include_archived is True:
        pass
    elif include_archived is False:
        queryset = queryset.filter(archived_at__isnull=True)

    if scope == "created":
        return queryset.filter(created_by=user)

    if scope == "all":
        if not can_view_all_tasks(user):
            raise PermissionDenied(
                "You do not have permission to view all task batches."
            )

        return queryset

    if scope in {"my", "department"}:
        visible_task_ids = visible_tasks_for(
            user,
            scope=scope,
            include_archived=include_archived,
        ).values_list(
            "id",
            flat=True,
        )

        return queryset.filter(tasks__id__in=visible_task_ids).distinct()

    visible_task_ids = visible_tasks_for(
        user,
        include_archived=include_archived,
    ).values_list(
        "id",
        flat=True,
    )

    return queryset.filter(
        Q(created_by=user) | Q(tasks__id__in=visible_task_ids)
    ).distinct()


def can_view_task(user, task):
    return (
        visible_tasks_for(
            user,
            include_archived=True,
        )
        .filter(pk=task.pk)
        .exists()
    )


def can_update_task_status(user, task):
    if not user or not user.is_authenticated:
        return False

    if task.assigned_to_id == user.id:
        return True

    if can_manage_all_tasks(user):
        return True

    if not can_manage_department_tasks(user):
        return False

    managed_user_ids = get_management_scope_user_ids(user)

    return task.assigned_to_id in managed_user_ids


def can_manage_task(user, task):
    if not user or not user.is_authenticated:
        return False

    if can_manage_all_tasks(user):
        return True

    if task.batch.created_by_id == user.id:
        return bool(
            user.has_perm("tasks.assign_task")
            or user.has_perm("tasks.assign_department_task")
        )

    if not can_manage_department_tasks(user):
        return False

    managed_user_ids = get_management_scope_user_ids(user)

    return task.assigned_to_id in managed_user_ids


def can_manage_batch(user, batch):
    if not user or not user.is_authenticated:
        return False

    if can_manage_all_tasks(user):
        return True

    if batch.created_by_id == user.id:
        return bool(
            user.has_perm("tasks.assign_task")
            or user.has_perm("tasks.assign_department_task")
            or batch.assignment_type == "PERSONAL"
        )

    if not can_manage_department_tasks(user):
        return False

    managed_department_ids = get_managed_department_ids(user)

    if (
        batch.source_department_id
        and batch.source_department_id in managed_department_ids
    ):
        return True

    managed_user_ids = get_management_scope_user_ids(user)

    return batch.tasks.filter(assigned_to_id__in=managed_user_ids).exists()


def can_comment_on_task(user, task):
    return bool(can_view_task(user, task) and task.archived_at is None)


def can_edit_task_comment(user, comment):
    if (
        not user
        or not user.is_authenticated
        or not user.is_active
        or comment.removed_at
    ):
        return False

    if comment.author_id != user.id:
        return False

    return can_comment_on_task(
        user,
        comment.task,
    )


def can_remove_task_comment(user, comment):
    if (
        not user
        or not user.is_authenticated
        or not user.is_active
        or comment.removed_at
    ):
        return False

    if user.is_superuser:
        return True

    if comment.author_id == user.id:
        return can_view_task(
            user,
            comment.task,
        )

    if user.has_perm("tasks.moderate_taskcomment"):
        return can_view_task(
            user,
            comment.task,
        )

    return can_manage_task(
        user,
        comment.task,
    )
