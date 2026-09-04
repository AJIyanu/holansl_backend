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

Logout revokes both the access and refresh JWT IDs through SimpleJWT's blacklist
tables. JWT authentication checks the blacklist so a revoked access token cannot
be reused for its remaining lifetime, while standard refresh validation rejects
the revoked refresh token. Logout audits must contain only safe outcome metadata,
never either raw token. Deployments must apply the packaged blacklist migrations
before serving logout or authenticated requests with blacklist-aware settings.

## Permission policy

- Django superusers bypass permission checks without needing an executive role.
  This does not waive data-integrity protections or required business validation.
- CEO and CTO users are authorized through assigned Django permissions. Their role
  names alone must not bypass checks.
- Both roles receive all permissions by default, and every newly introduced
  permission must be automatically assigned to those roles.
- Existing roles receive the initial full baseline through an explicit operator
  command. Subsequent creation hooks grant only new permissions, not missing old
  assignments. Permission edits, seeding and no-op migrations do not restore removals.
- Backend enforcement, including object/queryset scope, is authoritative. Frontend
  navigation/action visibility mirrors access; it is not security enforcement.
- Preserve current per-resource deletion/integrity protections. Do not interpret
  broad permission assignment as permission to erase referenced records.

User/profile PUT and PATCH use model permissions consistently. Account-status
changes additionally require change_user; role assignment requires change_role
and possession of the assigned permissions. Only superusers may modify superuser
accounts. Role edits cannot grant permissions the actor does not hold. Existing
superuser-only permanent deletion of users/profiles/leadership is preserved.
Audit lists, summaries and AI insight require view_auditlog; role names grant no
access. See [rollout instructions](operations/EXECUTIVE_PERMISSIONS.md).

## Email/notification policy

Password-reset and initial-password emails use notifications.services.send_sensitive_email
and the shared Resend adapter. Accounts retains token creation/verification and
reset business rules. These links are email-only: no dashboard/WhatsApp fallback,
notification/outbox records, ordinary preferences, quiet hours or queued retries.
Raw links/bodies remain in memory for sending; provider error details and response
metadata are discarded. Accounts audits safe reset-record/provider identifiers.
Never send a real reset email as a test. Provider acceptance is not inbox receipt.

## Storage policy

Supabase private storage is the default. Google Drive remains available for old
records and an explicitly configured rollback, never an automatic fallback.
Downloads/deletions select each document's recorded provider. Supabase uploads
and downloads check bucket privacy first and fail closed if public or unverifiable.
Do not migrate files, change bucket visibility or delete provider credentials merely
to switch defaults. See [storage operations](operations/STORAGE.md).

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
