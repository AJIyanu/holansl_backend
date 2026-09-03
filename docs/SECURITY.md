# Security and access standards

## Authentication and onboarding

The configured default/temporary staff password comes from the environment, not
from a hard-coded new value. A new user must not enter the dashboard with that
password: direct the user into password update/reset first and enforce the block
in Django, not just a frontend redirect.

Current implementation rejects token login when must_change_password is true
and initiates the existing reset-link flow. Keep current account routes.
Any gap between UI redirection and this flow needs a separately approved,
tested change; do not invent a new change-password endpoint.

Keep access/refresh tokens in HTTP-only cookies. Never expose tokens, raw reset
codes, storage keys or sensitive CRM data in logs, general caches or URL parameters
other than the existing protected reset-link flow. Preserve neutral reset-request
responses and expiry/single-use rules.

## Permission policy — approved target

- Django superusers bypass permission checks without needing an executive role.
  This does not waive data-integrity protections or required business validation.
- CEO and CTO users are authorized through assigned Django permissions. Their role
  names alone must not bypass checks.
- Both roles receive all permissions by default, and every newly introduced
  permission must be automatically assigned to those roles.
- Backend enforcement, including object/queryset scope, is authoritative. Frontend
  navigation/action visibility mirrors access; it is not security enforcement.
- Preserve current per-resource deletion/integrity protections. Do not interpret
  broad permission assignment as permission to erase referenced records.

This target is NOT fully reflected in current code. Existing executive bypasses
and restrictive update/PATCH paths need reconciliation, as does automatic
provisioning. Implementation is deferred under TODO.md. Do not document the target
as live behaviour or change groups/permissions during unrelated work.

## Email/notification policy — deferred implementation

Password-reset email must use the notification service and its Resend adapter.
Accounts should retain token creation/verification and reset business rules;
notifications owns delivery. Currently accounts calls Resend directly because it
predates notifications. Preserve that working behaviour until the dedicated
plan/implementation is approved. Never send a real reset email as a test.

## Storage policy — deferred implementation

Supabase private storage is the approved default target. Google Drive is dormant/
redundant for now, not an automatic fallback and not authorized for deletion.
Current source defaults/configuration may differ. Do not alter settings, .env,
buckets, existing documents or adapters in this documentation/theme task.
Later verify configuration without revealing secrets and plan historical-document
compatibility before switching defaults. See TODO.md.

Keep downloads/previews permission-checked, private URLs hidden and confidential
documents under stronger authorization. Retain masking/encryption/audited reveal
of identifiers and bank details.

## Audit and safe operation

Audit meaningful security/business changes in the database. Runtime text logs
are diagnostics. DEVELOPMENT_LOG.local.md is local engineering history, neither
a financial ledger nor permanent production audit storage.

Treat application databases as production and obtain migration approval.
Use disposable local tests and mock external providers. Do not infer staff-only
protection from IsAuthenticated alone when an action also requires staff or
resource permissions. Mobile account-action restrictions are UX policy; backend
authorization still applies at every screen size.
