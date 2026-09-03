# Procurement application

Status: PLACEHOLDER — not implemented. Verified 2026-09-03.

## Current state

[models.py](../../procurement/models.py), [serializers.py](../../procurement/serializers.py),
[views.py](../../procurement/views.py) and [signals.py](../../procurement/signals.py)
are intentional placeholders following removal of the previous design.
The app remains installed, but its inclusion in the
[root URL configuration](../../holansladminservices/urls.py) is commented out.

## Endpoints and contracts

There are no active procurement API endpoints, request/response schemas or
implemented workflow permissions to document. Frontend navigation/proxy remnants
do not establish a supported backend contract. Old URLs must not be resurrected
without an agreed design.

## Intended responsibility

The supplied design covers client requests/items, sourcing, independent supplier
and client quote revisions, client PO lines, purchasing, deliveries/proof and
requests to finance. Procurement does not create final accounting records.
See [procurement design](../design/PROCUREMENT.md) for line relationships, estimates
versus actuals, advance requests and unresolved decisions.

## Dependencies, safety and next documentation update

Reference CRM parties/contacts and accounts staff; integrate tasks/notifications
where the eventual workflow requires them. Exact models, lifecycle, approval
permissions, integrations, validation and test cases are not yet finalized.
Do not generate or run a replacement schema from the historical handover alone.

When implementation is authorized, replace this status document with verified
models/services, actual endpoints, serializers, authorization, audit and tests.
Tests currently present in the placeholder app are not evidence of a completed
procurement domain. Database migrations still require approval before execution.
