# 16. API examples

## Create personal task

```http
POST /tasks/
```

```json
{
  "title": "Prepare weekly report",
  "description": "Prepare the weekly activity report.",
  "priority": "MEDIUM",
  "start_at": "2026-07-01T09:00:00+01:00",
  "due_at": "2026-07-03T17:00:00+01:00",
  "assignment": {
    "type": "PERSONAL"
  }
}
```

## Assign selected users

```json
{
  "title": "Review supplier records",
  "description": "Review and update assigned supplier records.",
  "priority": "HIGH",
  "due_at": "2026-07-05T17:00:00+01:00",
  "assignment": {
    "type": "USERS",
    "user_ids": [
      "staff-user-uuid-1",
      "staff-user-uuid-2"
    ]
  },
  "notification_channels": [
    "DASHBOARD",
    "EMAIL"
  ],
  "notification_event_mode": "SHARED"
}
```

## Assign department

```json
{
  "title": "Complete monthly department report",
  "description": "Submit your individual contribution.",
  "priority": "HIGH",
  "due_at": "2026-07-10T17:00:00+01:00",
  "assignment": {
    "type": "DEPARTMENT",
    "department_id": "department-uuid",
    "include_assigner": false
  }
}
```

---

# 17. List examples

## My active tasks

```text
GET /tasks/?scope=my
```

## Tasks created by me

```text
GET /tasks/?scope=created
```

## Managed department tasks

```text
GET /tasks/?scope=department
```

## Organisation tasks

```text
GET /tasks/?scope=all
```

Requires an all-task permission.

## Archived tasks

```text
GET /tasks/?scope=my&archived=true
```

## Search and filter

```text
GET /tasks/?scope=my&search=report&priority=HIGH&status=TO_DO
```

## Overdue tasks

```text
GET /tasks/?scope=my&overdue=true
```

## Ordering

```text
GET /tasks/?scope=my&ordering=due_at
GET /tasks/?scope=my&ordering=-priority
GET /tasks/?scope=my&ordering=title
```

Supported task ordering aliases:

```text
created_at
updated_at
status
title
priority
start_at
due_at
assignee
department
```

---

# 18. Batch examples

```text
GET /tasks/batches/?scope=created
GET /tasks/batches/?scope=department
GET /tasks/batches/?scope=all
GET /tasks/batches/?archived=true
GET /tasks/batches/?search=monthly
GET /tasks/batches/?ordering=-due_at
```

The next stage will add task lifecycle operations:

* status transitions;
* cancellation;
* batch cancellation;
* archive and restore;
* shared-detail editing;
* related notifications;
* activity and audit records.


# New endpoints

## Status

```http
POST /tasks/{task-id}/status/
```

```json
{
  "status": "IN_PROGRESS"
}
```

## Cancel individual task

```http
POST /tasks/{task-id}/cancel/
```

```json
{
  "reason": "The task is no longer required."
}
```

## Archive and restore task

```text
POST /tasks/{task-id}/archive/
POST /tasks/{task-id}/restore/
```

## Edit shared assignment details

```http
PATCH /tasks/batches/{batch-id}/
```

```json
{
  "title": "Updated task title",
  "priority": "HIGH",
  "due_at": "2026-07-10T17:00:00+01:00"
}
```

## Cancel batch

```http
POST /tasks/batches/{batch-id}/cancel/
```

```json
{
  "reason": "The department requirement was withdrawn."
}
```

## Archive and restore batch

```text
POST /tasks/batches/{batch-id}/archive/
POST /tasks/batches/{batch-id}/restore/
```

## List comments

```http
GET /tasks/{task-id}/comments/
```

## Add comment

```http
POST /tasks/{task-id}/comments/
```

```json
{
  "body": "I have completed the first part of this task."
}
```

## Edit your comment

```http
PATCH /tasks/{task-id}/comments/{comment-id}/
```

```json
{
  "body": "Updated comment text."
}
```

## Remove comment

```http
POST /tasks/{task-id}/comments/{comment-id}/remove/
```

```json
{
  "reason": "Added by mistake."
}
```

The row remains in the database and is returned as:

```json
{
  "body": "[Comment removed]",
  "is_removed": true,
  "removal_reason": "Added by mistake."
}
```

## Individual task timeline

```http
GET /tasks/{task-id}/activity/
```

Optional filter:

```text
GET /tasks/{task-id}/activity/?activity_type=COMMENT_ADDED
```

## Assignment/batch timeline

```http
GET /tasks/batches/{batch-id}/activity/
```

Only batch-level events:

```text
GET /tasks/batches/{batch-id}/activity/?include_task_activity=false
```

# New reminder endpoints

## Deployment capabilities

```http
GET /tasks/reminders/capabilities/
```

Current expected response:

```json
{
  "enabled": true,
  "processing_mode": "inline",
  "scheduled_external_delivery_enabled": false,
  "channels": {
    "DASHBOARD": {
      "available": true,
      "reason": null
    },
    "EMAIL": {
      "available": false,
      "reason": "Scheduled email reminders require an enabled scheduler and configured email provider."
    },
    "WHATSAPP": {
      "available": false,
      "reason": "Scheduled WhatsApp reminders require an enabled scheduler and configured WhatsApp provider."
    }
  },
  "message": "Dashboard reminders are available. Scheduled email and WhatsApp reminders are unavailable on the current server configuration."
}
```

## Create reminder

```http
POST /tasks/reminders/
```

```json
{
  "task_id": "personal-task-uuid",
  "remind_at": "2026-07-03T15:30:00+01:00",
  "channels": [
    "DASHBOARD"
  ]
}
```

## List reminders

```text
GET /tasks/reminders/
GET /tasks/reminders/?task={task-id}
GET /tasks/reminders/?cancelled=false
GET /tasks/reminders/?due=true
GET /tasks/reminders/?ordering=remind_at
```

## Reschedule reminder

```http
PATCH /tasks/reminders/{reminder-id}/
```

```json
{
  "remind_at": "2026-07-03T16:00:00+01:00",
  "channels": [
    "DASHBOARD"
  ]
}
```

## Cancel reminder

```http
POST /tasks/reminders/{reminder-id}/cancel/
```

```json
{
  "reason": "No longer needed."
}
```

# 15. Run the integrity check

First inspect without changing anything:

```bash
python manage.py check_task_integrity
```

If it reports historical inconsistencies:

```bash
python manage.py check_task_integrity --fix
```

Then run it once more:

```bash
python manage.py check_task_integrity
```

Expected:

```text
Task integrity check passed.
```

---

# 16. Run the complete backend checks

```bash
python manage.py check

python manage.py makemigrations --check --dry-run

python manage.py test \
  accounts \
  notifications \
  tasks \
  -v 2
```

Expected migration result:

```text
No changes detected
```

Validate OpenAPI:

```bash
python manage.py spectacular \
  --file schema.yaml \
  --validate
```

---

# 17. Current free-deployment settings

Keep:

```env
TASK_REMINDERS_ENABLED=True
TASK_DASHBOARD_REMINDERS_ENABLED=True

TASK_SCHEDULED_EXTERNAL_DELIVERY_ENABLED=False

NOTIFICATION_PROCESSING_MODE=inline

TASK_ASSIGNMENT_NOTIFICATIONS_ENABLED=True
TASK_ASSIGNMENT_NOTIFICATION_CHANNELS=DASHBOARD,EMAIL
TASK_ASSIGNMENT_NOTIFICATION_EVENT_MODE=SHARED

TASK_LIFECYCLE_NOTIFICATION_CHANNELS=DASHBOARD
```

Behaviour:

* assignment emails can attempt immediate delivery;
* dashboard task reminders work without cron;
* future dashboard reminders appear after `scheduled_at`;
* email and WhatsApp reminders remain unavailable;
* the capabilities endpoint tells the frontend what to display.

Do not set this to `True` until an actual scheduler exists:

```env
TASK_SCHEDULED_EXTERNAL_DELIVERY_ENABLED=True
```

---

# 18. When scheduled external delivery becomes available

Set:

```env
TASK_SCHEDULED_EXTERNAL_DELIVERY_ENABLED=True
NOTIFICATION_PROCESSING_MODE=hybrid
```

Then run regularly:

```bash
python manage.py process_notification_deliveries \
  --batch-size 100 \
  --max-batches 5
```

A one-to-five-minute schedule is suitable for ordinary task reminders.

Alternatively, the scheduler can call the protected notification processing endpoint using:

```text
X-Notification-Cron-Secret
```

The frontend should always read:

```http
GET /tasks/reminders/capabilities/
```

before showing email or WhatsApp reminder options.

---

# Task backend completion

The task backend now covers:

* reporting hierarchy;
* department leadership;
* personal tasks;
* selected-staff assignments;
* department assignments;
* individual task copies;
* department and organisation access scopes;
* status transitions;
* cancellation;
* archive and restore;
* shared batch editing;
* comments;
* comment editing and removal;
* activity timelines;
* audit logs;
* assignment notifications;
* lifecycle notifications;
* personal task reminders;
* dashboard-only free-deployment behaviour;
* optional scheduled email and WhatsApp architecture;
* integrity repair;
* hard-delete protection;
* model, service and API tests;
* OpenAPI documentation.