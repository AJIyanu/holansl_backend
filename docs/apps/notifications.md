# Notifications application

Status: implemented inbox/outbox infrastructure; WhatsApp transport unconfigured.
Last reviewed: 2026-09-03.

## Purpose and data model

Shared delivery service for domain events. Notification stores event content;
NotificationRecipient stores each recipient's inbox state; NotificationDelivery
tracks each channel attempt/state. NotificationTemplate and NotificationPreference
support templating and delivery preferences. Channels are DASHBOARD, EMAIL,
WHATSAPP; event modes are SHARED and INDIVIDUAL. Processing modes are inline,
outbox and hybrid.

Sources: [models](../../notifications/models.py),
[views](../../notifications/views.py), [serializers](../../notifications/serializers.py),
[permissions](../../notifications/permissions.py),
[service entry points](../../notifications/services/__init__.py),
[delivery](../../notifications/services/delivery.py),
[existing detailed reference](../../notifications/documentation.md).

## Registered endpoints

Verified from Django's URL resolver and allowed HTTP methods on 2026-09-03.
Paths are relative to the backend origin; `{id}` is a resource identifier.
HEAD/OPTIONS and router discovery/format-suffix variants are omitted.
Permission classes below include action-decorator overrides; object checks,
serializer validation and service rules can impose additional restrictions.

| Methods | Path | Handler / operation | Permission classes |
| --- | --- | --- | --- |
| POST | `/notifications/dispatch/` | `NotificationDispatchView`: post | CanDispatchNotifications |
| POST | `/notifications/internal/process-deliveries/` | `ProcessNotificationDeliveriesView`: post | AllowAny |
| GET, POST | `/notifications/preferences/` | `NotificationPreferenceViewSet`: list, create | IsAuthenticated |
| GET, PUT, PATCH, DELETE | `/notifications/preferences/{id}/` | `NotificationPreferenceViewSet`: retrieve, update, partial_update, destroy | IsAuthenticated |
| GET, POST | `/notifications/templates/` | `NotificationTemplateViewSet`: list, create | CanManageNotificationTemplates |
| GET, PUT, PATCH, DELETE | `/notifications/templates/{id}/` | `NotificationTemplateViewSet`: retrieve, update, partial_update, destroy | CanManageNotificationTemplates |
| GET | `/notifications/deliveries/` | `NotificationDeliveryViewSet`: list | CanViewNotificationDeliveries |
| GET | `/notifications/deliveries/{id}/` | `NotificationDeliveryViewSet`: retrieve | CanViewNotificationDeliveries |
| POST | `/notifications/deliveries/{id}/retry/` | `NotificationDeliveryViewSet`: retry | CanRetryNotificationDeliveries |
| GET | `/notifications/` | `NotificationInboxViewSet`: list | IsAuthenticated |
| POST | `/notifications/archive-all-read/` | `NotificationInboxViewSet`: archive_all_read | IsAuthenticated |
| POST | `/notifications/mark-all-read/` | `NotificationInboxViewSet`: mark_all_read | IsAuthenticated |
| GET | `/notifications/unread-count/` | `NotificationInboxViewSet`: unread_count | IsAuthenticated |
| GET | `/notifications/{id}/` | `NotificationInboxViewSet`: retrieve | IsAuthenticated |
| POST | `/notifications/{id}/archive/` | `NotificationInboxViewSet`: archive | IsAuthenticated |
| POST | `/notifications/{id}/dismiss/` | `NotificationInboxViewSet`: dismiss | IsAuthenticated |
| POST | `/notifications/{id}/read/` | `NotificationInboxViewSet`: read | IsAuthenticated |
| POST | `/notifications/{id}/restore/` | `NotificationInboxViewSet`: restore | IsAuthenticated |
| POST | `/notifications/{id}/seen/` | `NotificationInboxViewSet`: seen | IsAuthenticated |
| POST | `/notifications/{id}/unread/` | `NotificationInboxViewSet`: unread | IsAuthenticated |

## Service and API contracts

Internal domain callers use notifications.services.notify and
cancel_scheduled_notification; see service signatures and RecipientSpec rather
than issuing an HTTP call back into the same Django app.

Privileged POST /notifications/dispatch/ uses NotificationDispatchSerializer:

```json
{
  "recipient_ids": ["00000000-0000-0000-0000-000000000001"],
  "notification_type": "general.message",
  "category": "general",
  "title": "Internal update",
  "message": "Review the new internal update.",
  "channels": ["DASHBOARD"],
  "event_mode": "SHARED"
}
```

The UUID is illustrative and must identify an eligible existing recipient.
Dispatch returns NotificationResult.as_dict() with HTTP 201; accepted/created
does not prove every channel has delivered. Templates, recipient overrides,
scheduling and metadata have additional fields in the serializer.

Inbox reads and actions operate on the current recipient's records, not global
event read state. List/filter/order/search and pagination use the inbox view and
NotificationInboxFilter. Preferences are scoped to the authenticated user.
Template/delivery management, dispatch and retry use separate permissions.

## Scheduler, delivery and security

POST /notifications/internal/process-deliveries/ bypasses session/JWT auth but
requires X-Notification-Cron-Secret matching configured NOTIFICATION_CRON_SECRET.
Missing server configuration returns 503, wrong secret 403, invalid batch_size
400. Batch size is constrained to 1–500. This is not an unrestricted public
business endpoint; never include the real secret in docs/logs.

Delivery statuses include PENDING, PROCESSING, SENT, DELIVERED, READ, RETRYING,
FAILED, SKIPPED and CANCELLED. Retry/locking/batch configuration controls outbox
processing; user actions do not guarantee provider-side delivery receipts.

Dashboard and Resend email implementations exist. WhatsAppProvider is explicitly
a future adapter and raises NotificationConfigurationError. Do not claim WhatsApp
messages are sent until an adapter is implemented/configured/tested.
Email requires sender/API configuration; external scheduled delivery requires an
actual running worker/scheduler, not just a model row.

## Configuration and verification

### Sensitive authentication email

Accounts uses `notifications.services.send_sensitive_email` for password-reset
and initial-password links. This internal Python service is not an HTTP endpoint.
It reuses the Resend adapter but sends an in-memory payload immediately, even in
outbox processing mode. No Notification, NotificationRecipient or NotificationDelivery
rows are created. Do not pass authentication secrets to ordinary `notify` or dispatch.

There is no channel override, dashboard/WhatsApp fallback, preference suppression,
quiet-hours delay or automatic retry. Missing configuration/destination fails;
provider exceptions are sanitized and response metadata is discarded. Only acceptance
status/provider ID returns to the caller. Accounts owns token lifecycle and safe audits.

Required configuration: `NOTIFICATION_EMAIL_ENABLED=True`, `RESEND_API_KEY`, and
`RESEND_FROM_EMAIL` (complete verified sender). Existing `RESEND_FROM_NAME` plus
`EMAIL_HOST_USER` remains the compatibility fallback for the sender, not SMTP transport.
Accounts also needs `FRONTEND_URL`. Local settings are present; live Resend key/domain
validity and inbox receipt require a separately authorized smoke test. No new settings,
dependencies or migrations are needed. Do not print secrets to validate configuration.

Focused tests: `manage.py test accounts.test_password_email notifications.tests.test_sensitive_email`
with isolated local test settings. All provider requests must be mocked.

NOTIFICATION_PROCESSING_MODE, retry/lock/batch settings, retention, email settings,
NOTIFICATION_CRON_SECRET and provider flags live in project settings.
The process_notification_deliveries and cleanup_notifications management commands
can mutate delivery/inbox data; inspect and obtain appropriate authorization
before operating against non-test data.

Run manage.py test notifications --keepdb with isolated local test settings.
Existing API/model/service/command tests cover dispatch, ownership, delivery,
preferences and administrative controls. Mock external providers. No real
delivery/provider credentials were exercised by this documentation task.
