# 16. Environment configuration

## Current testing deployment

```env
NOTIFICATION_PROCESSING_MODE=inline
NOTIFICATION_EMAIL_ENABLED=True
NOTIFICATION_WHATSAPP_ENABLED=False
NOTIFICATION_WHATSAPP_PROVIDER=disabled

NOTIFICATION_DEFAULT_MAX_ATTEMPTS=3
NOTIFICATION_RETRY_BASE_SECONDS=60
NOTIFICATION_RETRY_MAX_SECONDS=3600
NOTIFICATION_LOCK_TIMEOUT_SECONDS=900
NOTIFICATION_PROCESSING_BATCH_SIZE=100
NOTIFICATION_OPPORTUNISTIC_BATCH_SIZE=10
NOTIFICATION_RETENTION_DAYS=365

NOTIFICATION_CRON_SECRET=generate-a-long-random-secret
```

Generate the secret locally:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Production with cron available

Recommended:

```env
NOTIFICATION_PROCESSING_MODE=hybrid
```

This performs the first attempt immediately and lets cron handle retries.

Strict queue-only processing:

```env
NOTIFICATION_PROCESSING_MODE=outbox
```

---

# 17. Cron and free-tier fallback

## Native cron or cloud scheduler command

Run every few minutes:

```bash
python manage.py process_notification_deliveries \
  --batch-size 100 \
  --max-batches 5
```

Cleanup can run daily:

```bash
python manage.py cleanup_notifications \
  --retention-days 365
```

## External scheduler fallback

An external scheduler can call:

```bash
curl --request POST \
  "https://holansl-backend.onrender.com/notifications/internal/process-deliveries/?batch_size=100" \
  --header "X-Notification-Cron-Secret: YOUR_SECRET"
```

The endpoint:

* uses no JWT;
* requires the separate scheduler secret;
* uses constant-time secret comparison;
* accepts only `POST`;
* limits batch size to `500`;
* can be hidden from OpenAPI.

---

# 18. API routes produced

```text
GET    /notifications/
GET    /notifications/{recipient_id}/

POST   /notifications/{recipient_id}/seen/
POST   /notifications/{recipient_id}/read/
POST   /notifications/{recipient_id}/unread/
POST   /notifications/{recipient_id}/archive/
POST   /notifications/{recipient_id}/restore/
POST   /notifications/{recipient_id}/dismiss/

GET    /notifications/unread-count/
POST   /notifications/mark-all-read/
POST   /notifications/archive-all-read/

GET    /notifications/preferences/
POST   /notifications/preferences/
GET    /notifications/preferences/{id}/
PATCH  /notifications/preferences/{id}/
DELETE /notifications/preferences/{id}/

GET    /notifications/templates/
POST   /notifications/templates/
GET    /notifications/templates/{id}/
PATCH  /notifications/templates/{id}/
DELETE /notifications/templates/{id}/

GET    /notifications/deliveries/
GET    /notifications/deliveries/{id}/
POST   /notifications/deliveries/{id}/retry/

POST   /notifications/dispatch/

POST   /notifications/internal/process-deliveries/
```

Inbox filters include:

```text
category=
notification_type=
severity=
read=true|false
seen=true|false
archived=true|false
created_after=
created_before=
search=
ordering=
page=
```

Delivery administration filters include:

```text
channel=
status=
provider=
notification_type=
category=
recipient=
created_after=
created_before=
search=
ordering=
page=
```

---

# 19. Usage from the future Task app

Shared event for a department:

```python
from notifications.constants import (
    NotificationChannel,
    NotificationEventMode,
)
from notifications.data import RecipientSpec
from notifications.services import notify


recipients = [
    RecipientSpec(
        user=staff_user,
        action_url="/dashboard/tasks",
        action_label="View tasks",
        metadata={
            "task_id": str(task.id),
        },
        template_context={
            "task_title": task.title,
        },
    )
    for staff_user, task in generated_tasks
]


notify(
    recipients=recipients,
    notification_type="task.assigned",
    category="task",
    title="New task assigned",
    message=(
        "A new task has been assigned to you."
    ),
    channels=[
        NotificationChannel.DASHBOARD,
        NotificationChannel.EMAIL,
    ],
    event_mode=NotificationEventMode.SHARED,
    actor=request.user,
    source=task_batch,
    template_key="task.assigned",
    metadata={
        "task_batch_id": str(task_batch.id),
    },
    deduplication_key=(
        f"task-batch-assigned:{task_batch.id}"
    ),
)
```

Individual notification events with individual task links:

```python
notify(
    recipients=recipients,
    notification_type="task.assigned",
    category="task",
    title="New task assigned",
    message=(
        "A new task has been assigned to you."
    ),
    channels=[
        NotificationChannel.DASHBOARD,
        NotificationChannel.EMAIL,
    ],
    event_mode=NotificationEventMode.INDIVIDUAL,
    actor=request.user,
    source=task_batch,
    template_key="task.assigned",
    deduplication_key=(
        f"task-batch-assigned:{task_batch.id}"
    ),
)
```

The notification app is now independent of tasks. The next backend stage is the reporting hierarchy and Task/TaskBatch models that will call this service.

[1]: https://docs.djangoproject.com/en/5.2/topics/db/transactions/ "Database transactions | Django documentation | Django"
[2]: https://render.com/docs/cronjobs "Cron Jobs – Render Docs"