> Ledger reset, 2026-11-03: all earlier ledger build/configuration checklists below
> are historical and superseded. No previous ledger feature is authorized for
> rebuilding automatically. Start one explicitly agreed feature at a time.

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

## A02 — DEFERRED: administrative staff efficiency

- [ ] Add **Staff Efficiency** to the administrative section of the dashboard
  side menu, visible only to users with an approved reporting permission.
- [ ] Show each staff member's weekly activities by combining relevant existing
  task and operational activity records without duplicating their source data.
- [ ] Define approved, explainable performance measures across selectable time
  periods before assigning scores or rankings. Account for workload, task type,
  quality, completion, timeliness and periods with incomplete data.
- [ ] Provide team and individual trends, filters and drill-down evidence so that
  managers can understand how every metric was calculated.
- [ ] Decide whether to include AI-assisted analysis. If approved, label it as
  advisory, show the evidence and time range used, protect staff data, avoid
  unsupported employment conclusions and retain appropriate auditability.
- [ ] Confirm authorized viewers, staff visibility/appeal rules, retention,
  privacy safeguards and whether metrics may influence formal performance reviews.
- [ ] Plan and approve the backend reporting contract and permissions before the
  dependent administrative frontend, then add tests and operational documentation.

## Future domain work

- Procurement: backend implemented; see P01/P02 below for migration rollout,
  frontend, remaining approval decisions and ledger integration.
- Ledger: first rebuild feature, basic NGN cash book; see [current status](apps/ledger.md).
- WhatsApp: select/implement/configure a provider; current adapter cannot send.
- Reports: define scope after operational/financial data is stable.
- CRM multi-record creation: plan recovery/atomicity across current sequential calls.
- Onboarding: verify redirect/reset UX with the existing login block and routes.
- UI follow-up: migrate remaining module-specific hard-coded colours only through
  approved scoped changes; token configuration does not imply every page is redesigned.


## P01 — Procurement backend rollout and companion work

- [x] Applied `procurement.0003_procurement_item_lifecycle` with explicit user
  approval on 2026-09-04. Verified all 27 tables and 113 procurement permissions;
  CEO and CTO each hold the 113 permissions. Only this migration was in the plan.
- [ ] Build the frontend against [the implemented API](apps/procurement.md),
  including milestone/history toggle, replacement attempts and explicit finance
  placeholders. Backend was implemented first as requested.
- [ ] Schedule `expire_procurement_quotations` after deployment; reason: expiry
  history/notifications need an operator-managed scheduled command. Timestamp
  validity remains available on reads without running a job.
- [x] Implement approved company numbering and sample-based quotation/waybill
  PDF/JPG generation. Configure the current office address before official use;
  the obsolete sample addresses are never defaults.
- [ ] Agree cancellation/reversal/correction rules for committed quantities and
  draft delivery reservations before adding such write actions. Reason: these
  can invalidate purchase/delivery evidence; raw history edits are prohibited.
- [ ] Decide product catalogue ownership and any approval thresholds. Current
  requested items carry description/unit snapshots and use assigned action
  permissions; no role-name or monetary approval policy is assumed.

## Ledger — one feature at a time

- Previous ledger implementation and roadmap retired at the user's request on
  2026-11-03. The agreed basic cash book is built; agree each additional feature
  before extending it.
- Source removal is complete; verify execution of ledger migration 0013 against
  the intended application database before declaring its old records deleted.
- [ ] Apply the reviewed cash-book migrations 0014/0015 after application rollout
  approval, assign staff permissions, and perform authenticated UI acceptance testing.

## Non-tax ledger completion — 2026-11-05

- Delivered source: separate NGN/USD/GBP accounts; advance allocation/reallocation;
  credit/refund/adjustment/reversal; frozen Reconciliation and Statement with reviews
  and print UI; scoped procurement decisions and source-linked invoice/bill posting;
  acceptance-based revenue and cancellation controls. See apps/ledger.md.
- Deployment: review/apply pending migrations with operator approval and assign the
  new permissions through existing staff administration; run authenticated UI UAT.
- Tax remains explicitly deferred for a separate ledger. No VAT/WHT behaviour is
  activated here. Exchange conversion, arbitrary currency registration, inventory
  valuation, period closing and invoice/receipt document generation remain outside
  this approved party-ledger completion.
