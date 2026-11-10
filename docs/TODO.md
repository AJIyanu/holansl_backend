> Ledger reset, 2026-11-03: all earlier ledger build/configuration checklists below
> are historical and superseded. No previous ledger feature is authorized for
> rebuilding automatically. Start one explicitly agreed feature at a time.

# TODO actions

Original inventory recorded 2026-09-03; ledger inventory reconciled 2026-11-10
against both development logs and current source. This file describes deferred work,
not permission to execute it. Each item requires a plan, approval, implementation and tests before
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
- Ledger: cash book, sales/purchase/staff/tax ledgers, documents and reconciliation
  are built in source; see the reconciled ledger inventory below and [API details](apps/ledger.md).
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

## Ledger — reconciled inventory, 2026-11-10

This inventory supersedes the outdated ledger checklist and the November 5
non-tax-only status. Compared both repositories' local development logs with
current routes/services/pages and app documentation. **Built** means implemented
in source, not independently verified as migrated/deployed or accepted in live use.
The November 3 reset remains historical; retired features are not automatically
approved for rebuilding. No application database was queried during this review.

### L01 — Built in backend and frontend

- [x] Cash book: receipts/debits, payments/expenses/credits, capital, account/currency
  and date filters, carried balances, audited corrections and void/reversal flows.
- [x] Automatic double-entry journal with permissioned viewing, date/account filters
  and source/audit information. Related financial writes are atomic and retry-safe.
- [x] NGN primary plus separate USD/GBP cash/bank accounts; add, rename and
  activate/deactivate accounts, switch accounts/currencies. January–December year,
  zero initial balances. Supported currency codes and nominal accounts remain fixed.
- [x] Sales and purchase party ledgers: searchable remembered party selection,
  transaction history, balances, advances/allocations and authorized corrections.
- [x] Sales invoices: ordered item descriptions/quantities/UOM/prices, discounts,
  automatic numbering, confirmation, delivery posting, editable undelivered invoices,
  PDF/print and custom/default email. Delivered invoices have no edit action.
- [x] Sales payment receipts: itemized invoice-copy drafts, currency/account/method/
  bank details, immediate actual-cash recording, VAT/WHT breakdown, amount in words,
  print/PDF/email. Corrections retain original records.
- [x] Supplier bills/payments: itemized forms, adjustable tax, fixed/percentage
  discount, delivery-only bill debt, pre/post-delivery payments, bill-copy drafts,
  supplier receipt number and private image/PDF evidence; evidence-only later edits.
  Supplier document generation/email is intentionally not part of this workflow.
- [x] Procurement approval inbox and linked invoice/bill imports; acceptance-based
  sales revenue recognition, cancellation/correction controls and source identity.
- [x] Staff Ledger for authorized account personnel: pay/receive, salaries,
  allowances, transport, loans, advances, repayments/change and reimbursements.
  Ordered expense retirement with evidence, approval/rejection and separate balances.
- [x] Tax Ledger: VAT timing at payment or delivery first; input/output VAT,
  client WHT assets, supplier WHT liabilities, input-credit classification, remittance
  approvals, manual CIT assessments/WHT offsets, versioned policies and CSV export.
  Historical backfill command and reviewed delivery-opening workflow are built.
- [x] Printable frozen Reconciliation and Statements for parties/staff, selected
  ranges/currencies, prior/running/closing balances. New party statements include
  goods, VAT, WHT and actual cash. Existing snapshots remain unchanged.
- [x] Existing permission format enforced in pages/actions and backend; actor/reason
  history, private supporting documents and idempotent financial submissions.

### L02 — Queued: staff self-service advance retirement from Overview

Requested 2026-11-10. **TODO only; not implemented by this review.** Reuse the
existing Staff Ledger retirement/approval engine; do not duplicate financial tables.

- [ ] Add a Retire Advance / Account for Expenses form reachable from the dashboard
  **Overview**, without requiring access to the Accounts or Staff Ledger pages.
- [ ] Show only advances belonging to the logged-in staff member. Derive identity
  from authentication on the backend; reject another staff/advance/document UUID
  even if supplied directly. No selectable different staff member in this form.
- [ ] Reuse ordered expense items, dates, descriptions, categories, amounts,
  automatic totals and supporting receipts; show remaining advance/change or
  reimbursement due using the existing rules.
- [ ] Submit for approval by personnel holding the appropriate approval permission.
  Submission alone must not post expenses, retire the balance or create cash.
  Preserve preparation/approval identities, reasons and existing approval policy;
  access to the self-service form must not grant approval authority.
- [ ] Let the staff member view only their own submissions, status and rejection
  reasons, and resubmit corrected expenses through the existing rejected-record flow.
- [ ] Add narrowly scoped self-service permissions using the project's format;
  do not grant broad access to everyone's Staff Ledger to enable this feature.
- [ ] Keep change actually returned as a separately confirmed cash movement under
  existing accounting permissions; expense submission must not imply cash received.
- [ ] Test cross-staff access, private evidence ownership, pending/approved/rejected
  states, duplicate submits, stale balances and atomic posting on approval.
- [ ] Plan/approve the endpoint and permission contract before implementation in
  backend and frontend. Adding this TODO is not approval to build it yet.

### L03 — Requested features still awaiting implementation/design

- [ ] Permissioned analytical graphs and a consolidated ledger analytics dashboard.
  Balance cards and filtered totals exist; dedicated graphs/analytics are not built.
- [ ] Register/edit additional currencies beyond NGN/USD/GBP. Existing currency/account
  switching is built; arbitrary currency configuration is not. Define precision,
  validation and protection for currencies already used before implementing.
- [ ] Configurable nominal chart of accounts beyond the fixed journal accounts.
  Cash/bank account management is already built; this is a separate extension.
- [ ] Controlled editing/correction workflow for delivered/posted sales invoice
  document details, explicitly deferred by the user. Existing credit/refund/reversal
  operations are available; direct invoice editing is not.

### L04 — Explicitly deferred / separate future scope

- [ ] WHT certificate tracking and electronic invoicing (explicitly deferred by user).
- [ ] Exchange-rate conversion/revaluation; balances currently stay per currency.
- [ ] Financial-period closing/locking and year-end carry-forward procedures.
- [ ] Inventory valuation/COGS and full financial reporting (trial balance, profit
  and loss, balance sheet). These are future scope, not approved build commitments.
- [ ] Automated payroll, PAYE and CIT calculation/filing. Staff payments and manual
  CIT assessment/settlement exist; they are not payroll or tax-filing engines.

### L05 — Rollout and acceptance: status to verify, not missing source features

- [ ] Confirm application migrations through ledger 0028 and dependencies on the
  intended environment; logs document generation/test execution, not live rollout.
  Do not infer pending/applied status from source or rerun the historical reset.
- [ ] Verify relevant staff permissions through existing account administration.
- [ ] Preview historical tax links; review discrepancies, apply authorized backfill
  and approve necessary delivery VAT openings. Command availability is implemented;
  production execution was not confirmed by this review.
- [ ] Complete authenticated desktop/mobile/print acceptance testing, especially
  approval boundaries, receipts, financial corrections and reconciliation totals.
- [ ] Separately verify real document-email delivery and private evidence uploads
  when authorized; mocked tests/build success do not prove external delivery.

Evidence: [ledger API/current behaviour](apps/ledger.md),
[Tax Ledger design](design/TAX_LEDGER.md), ledger services/API routes and companion
frontend pages, compared with both local development logs through November 9.
No new application tests or migrations were run for this documentation-only review.
