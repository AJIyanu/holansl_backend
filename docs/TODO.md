# TODO actions

Status recorded 2026-09-03. This file describes deferred work, not permission to
execute it. Each item requires a plan, approval, implementation and tests before
being marked complete. Log actual delivery/corrections locally with timestamps.

## N01 — COMPLETE: ~~notification-based password-reset email~~

Implemented and locally verified 2026-09-03 after plan approval. Both reset flows
use notifications' immediate sensitive-email service and shared Resend provider.
No dashboard/WhatsApp deliveries or persisted secret bodies. Full suite: 179 tests
passed; final focused suite: 11 tests passed (including two additional configuration
checks added after full-suite discovery). Live delivery was not exercised.
See [notification documentation](apps/notifications.md) for configuration/operations.

- [x] Reuse existing Resend configuration/provider and remove accounts transport duplication.
- [x] Preserve account token lifecycle, login block, response contracts and safe audits.
- [x] Email-only, immediate delivery; no preference/quiet-hour suppression or queued retry.
- [x] Redact provider errors; omit raw reset links from results and persistent records.
- [x] Mock provider success/failure, test reset/onboarding and configuration failures.
- [ ] Optional operational follow-up: separately authorize a live Resend smoke test;
  configuration presence and mocked acceptance do not prove inbox receipt.

## Q01 — DEFERRED: remaining frontend ESLint findings

- Refactor JSX inside try/catch in the three task page/list components (93 errors).
- Review synchronous state updates inside effects (10 errors), preserving forms,
  filters, loading states and security behaviours.
- Review notification icon component identity in both copies (2 errors).
- Review React Hook Form watch/compiler compatibility (2 warnings).
- Review the unused local labelClass helper (not an import) in CRM related records.
- Unused frontend imports were removed separately; do not disable rules to hide
  remaining findings. Plan and approve behavioural fixes, then test and log them.

## S01 — COMPLETE: ~~Supabase-default private document storage~~

Implemented and verified 2026-09-03. Source defaults now match the existing
Supabase environment selection; a read-only check confirmed an accessible private
bucket. New uploads use the default, existing document downloads/deletions use
their recorded provider. Drive is retained for legacy records/explicit rollback,
never automatic fallback. Privacy and failure checks are tested; no live file
write, file migration or schema migration occurred.
See [storage operations](operations/STORAGE.md).

## A01 — COMPLETE: ~~executive permission policy and provisioning~~

- [x] Replace role-name bypasses with assigned permissions; preserve superuser bypass.
- [x] Consistent PUT/PATCH and security checks, role-grant escalation protections,
  existing permanent-delete safeguards.
- [x] Grant only new permissions through creation/migration hooks. No routine
  restoration of removed permissions; default-account seeding preserves removals.
- [x] Add a preview-first command for the one-time complete CEO/CTO baseline.
- [x] Full suite passed 203 tests; final focused suite passed 23 tests, including
  the role-rename regression added after full-suite discovery.
- [x] Approved baseline applied and verified on 2026-09-04: CEO and CTO each
  have all 191 current permissions (65 added to each). Other groups unchanged.

No schema migration is needed. See [rollout instructions](operations/EXECUTIVE_PERMISSIONS.md).
Baseline rollout is complete; no migration was run. Frontend delegated
role-assignment controls may need a separately approved change_role visibility check.

## Future domain work

- Procurement: [design](design/PROCUREMENT.md); confirm detailed states/approvals.
- Ledger: [design](design/LEDGER.md); confirm posting, FX, tax and reconciliation.
- WhatsApp: select/implement/configure a provider; current adapter cannot send.
- Reports: define scope after operational/financial data is stable.
- CRM multi-record creation: plan recovery/atomicity across current sequential calls.
- Onboarding: verify redirect/reset UX with the existing login block and routes.
- UI follow-up: migrate remaining module-specific hard-coded colours only through
  approved scoped changes; token configuration does not imply every page is redesigned.
