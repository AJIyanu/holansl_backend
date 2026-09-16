# Backend application documentation

Last verified: 2026-09-03. Scope: local source-code contracts, not a claim that the
deployed environment or every integration has passed acceptance testing.

## Application index

| Application   | Status                                                         | Document                          |
| ------------- | -------------------------------------------------------------- | --------------------------------- |
| Accounts      | Implemented authentication, staff, hierarchy and security APIs | [Accounts](accounts.md)           |
| CRM           | Implemented Party-centred master data and protected records    | [CRM](crm.md)                     |
| Tasks         | Implemented assignments, lifecycle, comments and reminders     | [Tasks](tasks.md)                 |
| Notifications | Implemented inbox/outbox; WhatsApp transport unconfigured      | [Notifications](notifications.md) |
| Procurement   | Backend built; ledger integrated and migrated; frontend pending     | [Procurement](procurement.md)     |
| Ledger        | Backend built and migrated; configuration pending                                   | [Ledger](ledger.md)               |

Reports are future scope, not a registered Django application.
Read [shared API conventions](API_CONVENTIONS.md), the
[design specifications](../design/README.md) and [project standards](../README.md).
Existing detailed [task notes](../../tasks/document.md) and
[notification notes](../../notifications/documentation.md) remain available;
current routes and serializers take precedence if older prose differs.

## Maintenance standard

When a feature or API changes, update the relevant app document in the same task:
purpose/status, models and services, methods/paths, permissions, request/response
contracts, validation/errors, filtering, integrations/configuration and tests.
Label proposals clearly; do not document speculative endpoints as active.
Use serializer source and the generated OpenAPI schema for exhaustive field
definitions. Endpoint tables are an inventory, not a substitute for object-level
authorization rules.

The Git-ignored root DEVELOPMENT_LOG.local.md is a local development inventory
and roadmap, not production audit storage. It is intentionally absent from new
clones; re-establish a dated baseline when needed.
