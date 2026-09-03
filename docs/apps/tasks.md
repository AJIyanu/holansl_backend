# Tasks application

Status: implemented, not a future placeholder. Last reviewed: 2026-09-03.

## Purpose and architecture

TaskBatch stores shared assignment information; Task stores each individual's
assignment. Assignment types: PERSONAL, USERS, DEPARTMENT. Models also include
TaskComment, TaskReminder and immutable-style TaskActivity history. Priorities
are LOW/MEDIUM/HIGH/URGENT; statuses are TO_DO, IN_PROGRESS, BLOCKED, COMPLETED,
CANCELLED.

Sources: [models](../../tasks/models.py), [constants](../../tasks/constants.py),
[serializers](../../tasks/serializers/), [services](../../tasks/services/),
[active views package](../../tasks/views/), [filters](../../tasks/filters.py),
[legacy detailed notes](../../tasks/document.md).
The active views package exports the routed viewsets; do not assume the sibling
tasks/views.py file is the routing authority.

## Registered endpoints

Verified from Django's URL resolver and allowed HTTP methods on 2026-09-03.
Paths are relative to the backend origin; `{id}` is a resource identifier.
HEAD/OPTIONS and router discovery/format-suffix variants are omitted.
Permission classes below include action-decorator overrides; object checks,
serializer validation and service rules can impose additional restrictions.

| Methods | Path | Handler / operation | Permission classes |
| --- | --- | --- | --- |
| GET | `/tasks/batches/` | `TaskBatchViewSet`: list | IsAuthenticated, IsActiveStaff |
| GET, PATCH | `/tasks/batches/{id}/` | `TaskBatchViewSet`: retrieve, partial_update | IsAuthenticated, IsActiveStaff |
| GET | `/tasks/batches/{id}/activity/` | `TaskBatchViewSet`: activity | IsAuthenticated, IsActiveStaff |
| POST | `/tasks/batches/{id}/archive/` | `TaskBatchViewSet`: archive | IsAuthenticated, IsActiveStaff |
| POST | `/tasks/batches/{id}/cancel/` | `TaskBatchViewSet`: cancel | IsAuthenticated, IsActiveStaff |
| POST | `/tasks/batches/{id}/restore/` | `TaskBatchViewSet`: restore | IsAuthenticated, IsActiveStaff |
| GET, POST | `/tasks/reminders/` | `TaskReminderViewSet`: list, create | IsAuthenticated, IsActiveStaff |
| GET | `/tasks/reminders/capabilities/` | `TaskReminderViewSet`: capabilities | IsAuthenticated, IsActiveStaff |
| GET, PATCH | `/tasks/reminders/{id}/` | `TaskReminderViewSet`: retrieve, partial_update | IsAuthenticated, IsActiveStaff |
| POST | `/tasks/reminders/{id}/cancel/` | `TaskReminderViewSet`: cancel | IsAuthenticated, IsActiveStaff |
| GET, POST | `/tasks/` | `TaskViewSet`: list, create | IsAuthenticated, IsActiveStaff |
| GET | `/tasks/{id}/` | `TaskViewSet`: retrieve | IsAuthenticated, IsActiveStaff |
| GET | `/tasks/{id}/activity/` | `TaskViewSet`: activity | IsAuthenticated, IsActiveStaff |
| POST | `/tasks/{id}/archive/` | `TaskViewSet`: archive | IsAuthenticated, IsActiveStaff |
| POST | `/tasks/{id}/cancel/` | `TaskViewSet`: cancel | IsAuthenticated, IsActiveStaff |
| GET, POST | `/tasks/{id}/comments/` | `TaskViewSet`: comments | IsAuthenticated, IsActiveStaff |
| POST | `/tasks/{id}/comments/{comment_id}/remove/` | `TaskViewSet`: remove_comment | IsAuthenticated, IsActiveStaff |
| POST | `/tasks/{id}/restore/` | `TaskViewSet`: restore | IsAuthenticated, IsActiveStaff |
| POST | `/tasks/{id}/status/` | `TaskViewSet`: set_status | IsAuthenticated, IsActiveStaff |
| PATCH | `/tasks/{id}/comments/{comment_id}/` | `TaskViewSet`: update_comment | IsAuthenticated, IsActiveStaff |

## Contracts and lifecycle

POST /tasks/ uses TaskAssignmentCreateSerializer. title and assignment are
required; description, priority, start_at and due_at are optional. assignment
contains type, and user_ids or department_id as appropriate, plus include_assigner.

```json
{
  "title": "Review supplier specifications",
  "priority": "MEDIUM",
  "assignment": {"type": "PERSONAL"}
}
```

Creation returns detail, batch, tasks, recipient_count and notification_scheduled.
Personal creation rejects notification_channels and notification_event_mode;
shared assignment creation can select them. Each assignee receives an individual
task, not a single shared mutable completion flag.

Use the dedicated status/cancel/archive/restore actions. Task detail does not
support ordinary PATCH; batch details do. Batch/reminder PUT is disallowed even
though UpdateModelMixin exists. No hard-delete endpoints are exposed.
Comments use separate create/edit/remove actions; activities expose history.

POST /tasks/reminders/ accepts task_id, remind_at and channels. Reminders are for
the requesting user's active personal tasks, not arbitrary team assignments.
GET capabilities reports scheduler/channel readiness; do not infer email or
WhatsApp scheduling merely from the channel enum. Scheduled external delivery
requires configured provider and scheduler capability.

## Authorization, filtering and errors

Every view requires authentication and IsActiveStaff; services/access.py applies
personal/creator/department/all visibility and action permissions. Archive and
cancel operations have lifecycle restrictions and record activity.
Filters/search/ordering live in tasks/filters.py and individual viewsets.
Out-of-scope records may be unavailable through filtered querysets.

Expect validation errors for inconsistent assignees, due-before-start dates,
invalid status transitions, unavailable reminder channels and inaccessible tasks.
Follow the existing serializers and service errors rather than reproducing all
rules in the frontend.

## Integrations and verification

Tasks use accounts staff/department hierarchy and the central notification
service. TASK_* configuration controls notification/reminder features; actual
outbound delivery depends on notification provider and processing configuration.
Do not send real notifications in tests.

Run manage.py test tasks --keepdb with isolated local test settings.
Coverage includes access, assignments, lifecycle, comments, reminders, models/API
and hardening. Integrity checks and template commands exist under
tasks/management/commands; do not run data-changing commands as a documentation
verification step.
