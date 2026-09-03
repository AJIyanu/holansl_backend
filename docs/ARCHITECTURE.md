# Project architecture

## Identity and repositories

Holan Integrated Services Limited (HolanSL) is the Nigerian procurement company.
This dashboard is for staff operations, not visitors. Clients request products
they want HolanSL to supply; prefer “client” over “customer.”

Django/DRF backend: holansl_backend. Next.js App Router frontend:
../holansladmininterface. Backend is the primary guidance repository.
Branding shared with another Holan project does not change this product's
geography, business model or audience.

## Platform boundaries

Next.js handles server rendering, protected routes/actions, HTTP-only cookies
and user-facing interactions. Django owns authentication, authorization,
validation, business transitions, audit and the API layer.
PostgreSQL stores business data. Supabase is the intended managed database/private
storage platform, not a replacement for Django or an excuse to add browser
database access/Supabase Auth. New CRM uploads default to private Supabase storage;
existing documents use their recorded storage provider without automatic fallback.

Backend deployment references Render; frontend references Vercel. Verify runtime
configuration rather than assuming documentation proves live service availability.

## Domain ownership and status

| App           | Status               | Responsibility                                                                               |
| ------------- | -------------------- | -------------------------------------------------------------------------------------------- |
| accounts      | Built                | Users, staff, departments, hierarchy, roles/permissions, reset and security audit            |
| crm           | Built                | Shared Party identity, profiles, roles, affiliations, contacts, documents and history        |
| tasks         | Built                | Personal/staff/department assignments, batches, lifecycle, comments, reminders and activity  |
| notifications | Built infrastructure | Events, recipients, inbox, preferences/templates, outbox/retries; WhatsApp adapter not built |
| procurement   | Not implemented      | Requests, sourcing, quote revisions, PO lines, purchasing/delivery and financial requests    |
| ledger        | Not implemented      | Official invoices, expenses, advances, receipts/payments, allocations and accounting         |
| reports       | Future scope         | Consume stable domain data; no registered app currently                                      |

Accounts supplies identity and permissions. CRM supplies external parties.
Procurement and ledger reference CRM; CRM must not require their transactions to
exist. Preserve organisation and individual-contact identity independently and
retain historical references when an affiliation ends. Avoid duplicate/ghost
clients and suppliers or circular model ownership.

Procurement initiates finance requests; ledger owns approval/posting of official
money records. Notifications receives events but does not control business state.
Integrate existing tasks/notifications where required rather than rebuilding them.

## Current API conventions to preserve

See [API conventions](apps/API_CONVENTIONS.md) and each app document for active
paths, methods, payloads and access. Preserve current /account/ routes.
Django app labels and frontend permission mapping differ from URL prefixes:
accounts is the app; add→create and change→edit in format_permission.

Server-to-Django helpers send Authorization and X-Auth-Token and preserve the
existing single refresh/retry mechanism. Authenticated browser requests use a
protected Next route or Server Action. Do not add an unnecessary same-app HTTP
proxy hop for a Server Component/Action already using a shared server helper.

CRM Party create supports allowed nested records. Ordinary Party updates reject
nested profiles/contacts/addresses/sources; use dedicated endpoints, including
the existing PATCH /crm/parties/{id}/profile/. Keep Party roles rather than party_type.
Interactions are manual logs, not automatic WhatsApp/email capture.

Private downloads pass through Next.js and Django authorization. Provider flags
or channel enums are not proof of a working external integration.
