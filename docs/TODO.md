# TODO actions

Status recorded 2026-09-03. This file describes deferred work, not permission to
execute it. Each item requires a plan, approval, implementation and tests before
being marked complete. Log actual delivery/corrections locally with timestamps.

## N01 — DEFERRED: notification-based password-reset email

Latest user instruction: do not implement notification changes in the current task.

- Inspect notification Resend provider configuration and accounts' current sender.
- Keep reset token generation, expiry, single use and neutral responses in accounts.
- Route reset delivery through the existing notification service (not necessarily
  an HTTP call back into Django). Do not duplicate transport or leak raw tokens
  into durable notification/audit metadata.
- Define safe sensitive-link lifetime/retention, retry, failure and onboarding UX.
- Test mocked provider success/failure and onboarding/password-reset flows.
- Preserve current routes; do not send real emails during tests.

## S01 — DEFERRED: Supabase-default private document storage

Latest user instruction: do not implement storage changes in the current task.

- Verify configuration safely; existence/health is not yet confirmed.
- Make Supabase the default in a separately approved change.
- Keep Google Drive dormant; do not delete adapters, migrate files or enable
  fallback without an explicit plan.
- Preserve access to existing protected documents and rollback capability.
- Test upload validation, confidentiality, protected download and provider failure.

## A01 — DEFERRED: executive permission policy and provisioning

- Make CEO/CTO rely on assigned permissions, not role-name bypasses.
- Automatically assign all existing/new permissions to both roles by an agreed,
  idempotent provisioning strategy. Decide how deliberate revocations interact
  with synchronization before implementation.
- Keep superuser bypass independent of role membership.
- Reconcile current user/profile update/PATCH checks and executive security checks.
- Test ordinary users, CEO/CTO with/without permission, superuser without groups,
  new permission creation and database-integrity protections.
- Do not mutate live groups or permission assignments in this task.

## Future domain work

- Procurement: [design](design/PROCUREMENT.md); confirm detailed states/approvals.
- Ledger: [design](design/LEDGER.md); confirm posting, FX, tax and reconciliation.
- WhatsApp: select/implement/configure a provider; current adapter cannot send.
- Reports: define scope after operational/financial data is stable.
- CRM multi-record creation: plan recovery/atomicity across current sequential calls.
- Onboarding: verify redirect/reset UX with the existing login block and routes.
- UI follow-up: migrate remaining module-specific hard-coded colours only through
  approved scoped changes; token configuration does not imply every page is redesigned.
