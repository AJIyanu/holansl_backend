# Ledger application

Status: core backend migrated on 2026-09-15; Accounts frontend implemented on
2026-09-16. New client-rate and cash-holder migrations 0010–0012 await application
rollout approval. The report name is **Reconciliation and Statement**.
This page describes implemented contracts and pending deployment requirements.
Source: [models](../../ledger/models/),
[services](../../ledger/services/), [serializers](../../ledger/serializers.py).

## Accounting and access

Ledger owns financial authority and reuses staff identities, assigned permissions,
CRM parties, procurement sources, document numbering, tasks and notifications.
NGN is primary; migrations seed NGN, USD and GBP (pound sterling). Additional
currencies are configurable. Balances remain separate; there is no automatic FX
conversion. Years run January–December, divided into twelve monthly periods.

Money uses Decimal with HALF_UP rounding per invoice line to currency minor units.
Journals require at least two one-sided entries and exactly equal debits/credits
in one currency. PostgreSQL constraints/triggers protect posted journals,
submitted contents, revisions and histories. Transactions lock periods and currency
balances. Source events cannot post twice. No financial DELETE API exists.

An invoice debits receivables and credits deferred sales plus configured tax
liability, appearing immediately in the client's statement. Finance recognizes
sales only against client acceptance. A receipt debits bank/cash and credits
receivables once; allocating it to invoices never receives the money again.

Every `/ledger/` endpoint requires active staff and `ledger.view_financialrecord`.
Financial objects also require the corresponding type's view permission. Scope
defaults to the preparer's records. `viewdepartment_financialrecord` includes their
department; `viewall_financialrecord` supplies full scope. Complete statements,
tax reports, journals and the central approval queue require full scope.

Permissions include `add_invoice`, `change_invoice`, `approve_invoice`,
`post_invoice` and equivalents for receipts/payments/expenses/advances/credits.
Self-approval is allowed with both permissions; exact version/hash, approver,
timestamp and reason are retained. Role names supply no authorization. Existing
`format_permission` and additive executive-role provisioning are reused.
For example `ledger.approve_invoice` formats as `ledger.invoice.approve`.

All mutations require an `Idempotency-Key` UUID header. Same actor/path/payload/key
replays the saved result; conflicting reuse returns 409. Permissions and financial
scope are rechecked on replay. Send decimals as strings and dates in ISO format.
Unknown/server-owned fields are rejected. Accounting dates cannot be in the future
in Africa/Lagos. Lists use standard `count/next/previous/results` pagination.
Errors: 400 validation, 403 permission, 404 absent/inaccessible object, 409 stale
version/integrity conflict, 503 missing configuration/private storage unavailable.

## Financial APIs

Collections below expose GET list/detail, POST create and draft-only PATCH of
`description`/`reference` with `expected_version`. Lists are summaries and accept
`client`, `request`, `status`. Detail adds typed `content`, events, and applicable
outstanding invoice, settlement-source/unallocated or advance balances.

| Collection under `/ledger/` | Type-specific creation fields |
| --- | --- |
| `invoices/` | `lines`, optional `due_date` |
| `receipts/`, `payments/` | `amount`, `cash_account`, `purpose`, optional `target` |
| `supplier-bills/`, `expenses/` | `cost_lines` |
| `advances/` | `amount`, staff `custodian` |
| `advance-clearances/` | `advance`, `cost_lines` |
| `credit-notes/` | `invoice` (typed Invoice UUID), `credit_lines` |
| `withholdings/` | `line`, `authorization`, `certificate_reference`, `amount` |
| `tax-remittances/` | `tax_policy`, `period_from`, `period_to`, `amount` |
| `manual-journals/`, `opening-balances/` | `journal_lines` |

Common fields: `currency` (code), `policy` (posting-policy UUID), `date`, optional
CRM `party`, procurement `request`, private `evidence`, `description`, `reference`.
Party/evidence are mandatory where the operation requires them. Invoice lines use
`description`, `unit`, `quantity`, `unit_price`, `discount`, optional `item`,
`quote_line`, `po_line`, `tax_policy` UUIDs. Cost lines use `account`, `amount`,
`description`, `evidence`, optional `procurement_cost`. Credit lines use `line`,
`quantity`. Django calculates totals. Manual lines use `account` (AccountCurrency
UUID), `debit`, `credit`, optional `party` for permitted opening balances.

| POST action | Payload and effect |
| --- | --- |
| `{id}/submit/` | `expected_version`; freezes content and requests approval |
| `{id}/approve/`, `{id}/reject/` | `expected_version`, `reason`; exact-revision decision |
| `{id}/post/` | `expected_version`; validates approval and posts atomically |
| `{id}/revise/` | Complete new creation payload, `expected_version`, `reason`; new draft linked by `supersedes`, preserves original and supersedes its approval/task |
| `{id}/reverse/` | `date`, `reason`; opposite journal for supported types |
| invoice `{id}/cancel/` | `expected_version`, `date`, `reason`, `lines: [{line, quantity}]`; eligible credit draft, still needing approval/posting |
| invoice/receipt `{id}/generate/` | `format: PDF` or `JPG`; private official files |
| invoice `{id}/record-sent/` | CRM `recipient`, `method`, `occurred_at`, textual `evidence`; records actual sending, does not send externally |
| withholding `{id}/remittance-evidence/` | private `document`, `reference`, `verified_on`; retained client-remittance proof, no cash movement |

Inappropriate actions are rejected. Revised records always require fresh approval.
Receipt `reference` preserves the verified payment reference separately from its
assigned official receipt number. Posted invoices use credits, never generic reversal.

## Procurement and cancellation

Invoices can precede purchase/delivery or follow delivery; quotation is optional.
Associate goods lines with request items. A PO line references either `quote_line`
or `invoice_line`, with existing client-approval/evidence requirements. Source
prices and remaining quantities are validated. The procurement migration preserves
existing quote-based orders and backfills explicit item/description links.

POST `recognize-revenue/` takes `line`, `quantity`, `date`. This explicit finance
action uses recorded accepted quantities and cannot reuse acceptance across invoices
sharing an order. Dispatch alone does not earn revenue. Standalone invoices without
procurement acceptance links remain deferred; use linked request items/PO lines
for the goods-delivery lifecycle.

Only undelivered or documented rejected quantities can be credited. Accepted goods
and dispatched goods without an outcome cannot be cancelled. Cancellation requires
`cancel_invoice` and the applicable credit preparation/approval/posting permissions.
Receipts remain intact. A credit against a paid invoice becomes client credit;
refund payment needs separate evidence. Resolve collected/withheld tax through
its allocation/reversal workflow before crediting that tax component.

Procurement `finance-requests/{id}/submit/` now creates an Accounts submission
snapshot without posting money. GET `finance-submissions/` lists snapshots. POST
`finance-submissions/{id}/prepare/` links a matching draft `record` with a `reason`.
Accounts needs existing procurement scope to prepare request/item/cost references.
Actual source costs cannot post twice.

Quotation approval uses a real decision against the exact revision/hash. Procurement
register/download APIs exclude ledger IN/RC records; financial files use ledger
authorization. Tracking includes invoices, sending evidence and allocation/releases.

## Money and settlement

Receipt purposes: `CLIENT`, `ADVANCE_RETURN`, `SUPPLIER_REFUND`. Return/refund targets
are posted advance/bill FinancialRecord UUIDs. Payment purposes: `BILL`, `EXPENSE`,
`ADVANCE`, `CLIENT_REFUND`, `TAX_REMITTANCE`. Advance/remittance documents authorize
money; linked payments post actual bank movements. Expense/bill posting credits
payables; payment clears it. Clearance credits the staff advance. Funding 50,000,
clearing 42,000 and returning 8,000 leaves zero outstanding without double costs.
Supplier refund receipts record returned cash and reopen the related payable;
cancelling the supplier cost remains a separate correction.

POST `allocations/`: `source` (SettlementSource UUID), `invoice` (typed Invoice UUID),
`amount`, `date`, optional `tax_lines: [{line, amount}]`. Invoice detail returns
`invoice_id`; receipt/credit/withholding detail returns `settlement_source`.
POST `allocations/{id}/release/`: `amount`, `date`, `reason`. Events are append-only.
Partial/bulk receipts, overpayments and unallocated money are retained. Cross-client
or cross-currency allocations fail. Dates cannot precede source/invoice or the
latest allocation event for that client/currency. Reversals unwind allocations
without deleting their history.

## Tax

Tax uses ledger control accounts, not an additional physical bank account. No
statutory rates/exemptions are seeded. Accounts configures the tax type, verified
policy reference, effective dates, rate and mappings. `CLIENT_REMITTANCE` implements
the agreed split: collected tax is Holan-remittable, authorized withholding settles
client debt and is recorded for client remittance. `TAX_CREDIT` uses a separate
asset and does not automatically reduce invoice-tax liability. Configuration is
not a legal determination; verify applicability before activation.

GET/POST `withholding-authorizations/`, POST `{id}/revoke/` manage client/tax-type
eligibility, evidence, dates and reasons. Assign
`authorize_clientwithholdingauthorization` to the authorized manager. Revocation
cannot invalidate posted deductions. Cash allocation explicitly identifies tax
collected; final settlement must account for all remaining invoice tax. Withholding
posts separately and never counts as cash. Remittance payment cannot exceed tax
collected less prior remittances. Later client proof does not change the deduction.

GET `tax-reports/` with `currency`, `date_from`, `date_to` returns charged, credited,
net, collected, withheld, remitted, collected-unremitted and client deduction/reversal
references by policy. All references and original evidence remain available.

## Reconciliation and Statement

POST `client-statements/` with `client`, `currency`, `date_from`, `date_to` creates
an immutable snapshot and returns ID/summary. GET `{id}/` returns paginated rows,
summary and `page_opening_balance`. POST `{id}/export/` accepts PDF/JPG format.

Summary: opening/prior balance, period `invoice_total`, `cash_received_total`,
`withheld_total`, debit/credit adjustments, closing balance, `balance_due`,
`credit_held`, as-of invoice outstanding and unallocated sources. Receipt/withholding
totals show original events; reversals appear separately as adjustments. Rows show
date, reference, description, debit, credit and running balance. Allocation/reversal
events after the cutoff cannot rewrite earlier snapshots. A newly generated snapshot
includes later-entered records dated in its range; saved snapshots remain unchanged.

## Configuration, reports and rollout

GET list/detail and POST create: `currencies/`, `accounts/`, `account-currencies/`,
`cash-accounts/`, `posting-policies/`, `tax-types/`, `tax-policies/`. PATCH changes
only descriptive fields/active status on currencies/accounts/cash accounts/tax
types, with `reason`. Accounting identities and policy versions cannot be rewritten.
POST `financial-years/` takes `year`. GET `periods/`; POST `{id}/close/` or
`{id}/reopen/` takes `expected_version`, `reason`.

GET `journals/` and `{id}/` exposes posted journals. GET `trial-balance/` takes
`currency`, `date_from`, `date_to` and reports period account activity and balanced
totals, not statutory closing. Manual journals cannot bypass client/supplier/
advance/tax controls. Opening journals must be the currency's first posting and
support client receivable starting balances. Detailed legacy supplier/advance
imports and allocations to legacy opening debts remain outside this implementation.

POST `documents/` accepts multipart `file`, optional `party`, `record`. GET
`documents/{id}/download/` streams private/no-store authorized content. New uploads
use private Supabase only; existing documents use their recorded provider without
fallback. Exports reuse fingerprints. Multi-page failures roll back incomplete
metadata and attempt uploaded-page cleanup; failed provider cleanup needs operator
review because storage cannot join the database transaction.

Before use:

1. Application rollout completed on 2026-09-15. Applied migrations: ledger `0003`–`0009`,
   procurement `0006`. Historical ledger `0002` was verified already applied.
   Future migration plans still require review; fresh isolated tests can run the
   full history. These migrations
   seed currencies, add tables/constraints/triggers and backfill PO/document links.
   They create no business balances.
2. Configure the existing Accounts department (`LEDGER_ACCOUNTS_DEPARTMENT_ID`, or
   the department named Accounts), chart, currency/cash accounts, posting mappings,
   year and verified tax policies. Set `HOLAN_DOCUMENT_OFFICE_ADDRESS` for official
   files. POST `client-codes/` with `party`, `code` assigns stable document codes.
3. Assign action/scope permissions. POST `approval-routing/` with `enabled`,
   `channels`, `reason` requires existing permission to assign tasks to Accounts.
   System tasks record that standing authorization; task completion is not approval.
4. Schedule `process_ledger_integrations` to retry durable approval intents.
   Notifications reuse dashboard/email; WhatsApp remains unavailable. Missing task
   routing stays queued for retry. GET `capabilities/` exposes configuration readiness.

No new Python dependency was added for ledger. Existing Django/DRF/PostgreSQL,
ReportLab, pypdfium2, Pillow and CRM storage adapters are reused. Ledger tests cover
accounting, permissions, histories, procurement, mocked private exports and competing
writes. Live storage/email delivery and application databases are not test targets.

## Verified migration rollout — 2026-09-15

The user explicitly approved final validation, migration-plan review and application
migration execution. Applied procurement `0006` and ledger `0003`–`0009` successfully.
Ledger `0001`/`0002` were already applied; no historical removal was run. The PO and
registry backfills each had zero existing rows. No pending migrations remain.
NGN/USD/GBP are seeded; 221 new permissions were granted to CEO/CTO, preserving
existing grants. Financial-record and journal counts remain zero.

Validation: the isolated fresh-database suite ran 283 tests, with one existing
logout assertion expecting 401 instead of the configured 403. That test now also
requires the `token_revoked` error. A final isolated run of ledger plus logout
passed all 30 tests, including three added edge-case tests. Ledger/procurement
Ruff checks, formatting, Django system checks, migration consistency and local
Markdown links passed. Global schema generation still reports existing serializer
inference diagnostics in accounts/notifications; none originate in ledger. The
whole-project Ruff run also found pre-existing issues outside changed feature code.
Accounts setup, frontend work and live provider delivery remain separate steps.


## Accounts frontend and client rates — 2026-09-16

The companion frontend now implements `/dashboard/ledger/[[...segments]]` with
server access checks and the authenticated `/api/ledger/[...path]` proxy. Screens
include approvals/procurement submissions, invoices, receipts, payments, expenses,
advances, withholding, journals, account books, Reconciliation and Statement,
analytics, document generation/upload and settings. Existing staff/role screens
remain the permission-management authority.

New migrations 0010–0012 are additive and must be applied before this frontend is
used with these backend changes. They add client withholding treatment/rate/credit
account, deduction rate/net-base snapshots and optional cash-account custodian.
They preserve existing VAT authorizations and do not post balances or approve clients.

Client authorization accepts `treatment` (`VAT` for compatibility, or `INCOME_WHT`),
`rate` (business default **2.5**, not a statutory claim), and `credit_account` (required
active asset for income WHT). Managers use the existing authorize permission and
full scope. Rate changes use revoke/new authorization with dates, evidence and
reason; old posted rates stay attached to their deductions. For `INCOME_WHT`, a
withholding write optionally supplies `taxable_base` (defaults to invoice line net),
and `amount` must equal that base times the approved percentage, currency-rounded.
Posting rejects expired/revoked authorization, changed rates, duplicate/excess bases
and deductions above invoice outstanding. Income WHT debits the credit asset, never
VAT payable. Tax reports expose income credits separately in `income_wht`.

`GET journals/` accepts `date_from`, `date_to`, currency code, account-currency UUID,
source `kind` and reference `search`; results include account names/codes.
Financial lists also accept dates/currency and receipts/payments accept `cash_account`.
`GET account-book/` requires account-currency UUID, currency and date range and returns
paginated rows with running debit-positive balances plus exact opening/closing totals.
`GET analytics/` requires currency/date range and aggregates all posted journal lines,
not list pages. Both require journal view and full financial scope. Monthly income
reflects acceptance recognition; currencies are never summed or converted.

`GET lookups/?kind=...` provides paginated, permission-checked form choices, including
scoped procurement references. Cash-account setup/edit accepts optional active staff
`custodian`. Accounting identifiers and currency precision remain immutable after
creation; descriptive currency fields and active status can be edited with a reason.

Outstanding operational step: review/apply pending migrations, configure the income
WHT credit account/type, and verify the rendered authenticated workflows with staff.
No application migration or live financial posting is implied by source-code checks.
