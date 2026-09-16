# Ledger design specification

Status: approved design; backend implementation added 2026-09-15. See the
[implemented API and rollout boundaries](../apps/ledger.md). This document retains
product requirements and planning discussions; it does not establish statutory tax
liability/rates or imply that every future reporting capability is delivered.

The user chose client acceptance for earned revenue and permitted self-approval
with assigned permissions and recorded approver/time/reason. The implementation
uses native currencies, monthly periods, per-line HALF_UP rounding and explicit
cash-tax attribution. Application migrations were approved and applied on 2026-09-15; Accounts configuration remains pending.

## Ownership and agreed foundation

Ledger is the Accounts department's workspace for official financial records:
chart of accounts, HolanSL bank/cash accounts, financial periods, journals,
client invoices, supplier bills, expenses/salaries/logistics costs, revenue,
advances, payments/receipts, allocations, tax records, credits, adjustments,
reconciliation, reporting and approval/posting. Finance decides whether
procurement requests become official records.

- Use double-entry accounting: every posted journal has equal debits and credits.
  Ordinary operational forms generate entries; manual journals need permission.
- Financial years run from 1 January through 31 December.
- NGN is the primary currency. Include USD and GBP (pound sterling) initially;
  additional currencies must be configurable without redesigning the domain.
- Each bank/cash account has one currency and an independent balance. Do not add
  different currencies together without an explicit conversion policy/rate.
- Separate tax ledger accounts are required; no new physical tax bank account
  is required. Tax and sales can arrive in the same bank account while remaining
  separately classified in accounting.
- Reuse accounts identity/permissions and CRM Party master data. CRM counterparty
  bank details are not HolanSL bank/cash ledger accounts.

## Permissions and approval

Authority is assigned through permissions, not department or role names alone.
Provide separately assignable capabilities for preparing financial records,
approving quotations/invoices, recording receipts/payments, approving expenses
and advances, posting, reversing/cancelling, managing client withholding
eligibility, recording/approving withholding, recording remittances, reporting,
and managing/closing periods. Exact permission codenames remain to be designed.

Client withholding authorization is intended for the Accounts manager through a
specific assigned permission. Record the actor, date, reason and evidence for
eligibility changes; preserve authorization history for earlier transactions.
Eligibility alone never automatically applies a deduction to every payment.
Define effective dates/revocation behaviour before implementation.

Enforce permissions and record scope in Django. Preserve formatted frontend
permissions and additive provisioning of newly created executive permissions;
never restore revoked permissions through routine synchronization. Self-approval
and monetary thresholds remain open decisions, not assumed restrictions.

## Flexible invoices and procurement integration

Support invoices before purchase, before/with delivery, and after delivery:

1. Direct invoice: client request → invoice → client purchase approval (which may
   be payment) → Holan purchase → delivery with waybill and receipt for money paid.
   A quotation is optional in this workflow.
2. Delivery invoice: prepare/approve an invoice in advance to accompany the waybill.
3. Post-delivery invoice: issue after delivery/acceptance.

Receipt issuance follows actual payment, including payment before delivery;
providing its copy at delivery must not create another receipt/payment.
Keep invoice issuance, cash receipt and revenue recognition separate events.
Implemented recognition policy: invoices post to deferred revenue; earned sales
require recorded client acceptance and a finance recognition action.

Keep invoice lines traceable to client, request/item, PO, quote and delivery lines
where applicable. Missing quotation references are legitimate for direct invoices;
do not fabricate quotations to satisfy old model requirements. Current procurement
PO lines require quote references, so this needs an explicit compatible backend
change, including direct client approval evidence and agreed price snapshots.
Existing quotation-led flows and historical references must continue to work.

Finance creates/approves official invoices; authorized procurement staff may
export/send approved documents. Reuse the existing document numbering/register
and private generation/storage conventions rather than introduce a competing
sequence. Review registry ownership and dependencies before implementation.

## Invoice cancellation and corrections

User rule: only Accounts personnel with the cancellation permission can cancel,
with a mandatory reason. Goods never delivered, or goods rejected by the client
with recorded reasons, may qualify. Accepted goods must not be cancelled merely
because the client asks. Delivery without an acceptance/rejection outcome must
not be silently treated as rejection.

Proposed detailed strategy, still requiring confirmation: evaluate eligibility
per invoice line/quantity for mixed deliveries and preserve accepted portions;
retain posted originals and use linked credits/reversals rather than deletion.
Existing payments remain visible as credit/refund obligations or valid allocations.
Cancellation must never erase receipt evidence or imply a refund was paid.
Agree handling of dispatched goods, returns, replacements, partial rejection,
already-remitted tax and already-settled invoices before writing transitions.

## Receipts, allocations and reconciliation

Receipts prove money received; allocations explain what it settles. Support
many-to-many allocations, bulk/partial payments, overpayments, unallocated money
and receipts before invoice identification. Reallocation preserves receipt evidence.

Reconcile opening balances, invoices, actual receipts, allocations, approved
withholding/credits, outstanding balances and unallocated payments. Keep estimates
historical; use approved actual records for accounting. Procurement payment
milestones must follow ledger settlement, including reallocations and reversals.

## Reconciliation and Statement

Approved display name: **Reconciliation and Statement**, used in navigation,
screen headings and printable/exported document titles. Show client identity,
currency and selected date range with the title.

Agreed requirement: generate a statement of account for one client over a selected
inclusive start/end date range. This supports client reconciliation and must show:

- Opening balance immediately before the start date, carrying forward all relevant
  earlier posted activity, including unpaid older invoices and client credits.
- Invoices issued to the client within the selected range and their total.
- Actual money received from the client within the range and its total, including
  receipts allocated to older invoices and money not yet allocated to an invoice.
- Authorized/documented withholding, credit notes, reversals, refunds and other
  approved balance adjustments within the range, separately identified.
- Closing balance at the end date: opening balance plus invoices and other debit
  adjustments, less receipts, approved withholding and credit adjustments, with
  refunds/reversals affecting the balance according to their accounting direction.
- A dated transaction listing with document references, description, debit/credit
  amounts and running balance; clearly identify client debt versus credit held.

Receipt allocations must not count as new receipts or reduce the balance twice.
Show invoice-level outstanding amounts and unallocated client credits separately
so a net balance does not conceal unpaid invoices or overpayments. Cash received
must stay distinct from withholding even when both settle an invoice. Include
relevant pre-range transactions in the opening balance, not in period totals.

Generate statements per currency (NGN, USD, GBP or subsequently configured currency);
never sum unlike currencies. Use an explicit accounting/effective-date basis and
record the generation timestamp. Statements are based on posted official records,
not procurement estimates or draft invoices. Finalize date/cutoff rules for invoice
issuance versus posting, backdated entries and corrections before implementation;
activity after the end date must not change historical end-date settlement by
being included through current allocation/status fields.

Provide a permission-checked statement view and printable/exportable document,
with client identity, selected dates, currency, opening balance, period totals and
closing balance. Reuse private document access and existing rendering conventions.
Generating a statement does not send it to the client or alter accounting records.
Test opening balances, boundary dates, older-invoice payments, partial/unallocated
receipts, withholding, credits/refunds, historical allocations and currency isolation.

## Client withholding and tax remittances

### Agreed operational requirement

Only clients authorized by Accounts may settle an invoice partly through documented
withholding. For an eligible client, Accounts records the actual deduction, its tax
type and evidence. Once accepted as authorized/documented withholding, that portion
is settled: it is neither cash received nor client debt. Unauthorized or undocumented
shortfalls remain unresolved/outstanding pending an authorized resolution.

Example (illustrative amounts, not a rate): a total invoice of NGN 100,000 settled
with NGN 98,000 cash and NGN 2,000 authorized/documented withholding has zero client
balance. Cash reporting shows only NGN 98,000; withholding reporting shows NGN 2,000.

The user's intended tax-collection workflow is: if tax charged is NGN 5,000,
Holan receives NGN 3,000 of that tax and an authorized client withholds NGN 2,000,
Holan records/remits its collected NGN 3,000 and records NGN 2,000 as the client's
remittance responsibility. It must not schedule that NGN 2,000 as another Holan
cash remittance under this workflow. Preserve the client's identity and evidence
for presentation to authorities. A deduction is not proof the client remitted it.

This is a requested product workflow, not a verified conclusion that client
accreditation transfers statutory liability. Before implementing tax posting,
verify which tax types permit this treatment and required evidence. Do not apply
this collection split indiscriminately to all withholding taxes, or automatically
net income-tax withholding against invoice-tax liabilities. Client authorization
is an internal control, not legal accreditation by an authority.

### Separate records and reports

Maintain separately identifiable tax charged, tax actually collected, tax withheld
by clients, and Holan remittances. Do not classify tax collected for remittance as
sales revenue/profit or unrestricted company funds. Retain tax type, applicable
base/rate/amount, currency, client, invoice/line, receipt allocation, deduction
date, authorization reference, recording/approval actors, certificate/reference,
evidence and verification status. Support invoices with no payment and eligible
clients who pay in full without withholding.

Record each Holan remittance with tax type, amount/currency, payment account,
actual date, covered period(s), authority/reference and evidence. Monthly reports
must distinguish charged versus collected tax, deductions attributed to each
client, Holan remittances and remaining balances; do not imply all billed tax was
collected. Show client remittance evidence/status separately when available.
Partial-payment tax attribution, statutory due dates, reversals and certificate
requirements remain to be specified. Retain evidence privately and audit changes.

## Advances / imprest

An advance is not immediately a final expense. Record issue, funding account,
custodian, authorization, purpose/procurement link and evidence. Procurement
submits actual supplier/logistics/other expenditure and returned cash; finance
approves and clears the advance without counting the original outflow twice.
Track accounted, returned, additionally requested and outstanding amounts.

Candidate states: requested, approved, disbursed, partially accounted, fully
accounted, balance returned, additional funding requested, closed. Exact
transitions/approval rules still require design.

## Build strategy

1. Finalize posting mappings and remaining policies below; define backend models,
   action contracts, permissions, scope and accounting invariants.
2. Build currencies, chart of accounts, bank/cash accounts, financial years/periods,
   journals, audit, evidence and posting services with isolated backend tests.
3. Integrate procurement approvals and flexible invoices, including the optional
   quotation path and existing official document register.
4. Implement receipts/payments, allocations, tax authorization/records/remittances
   and reliable settlement milestones under verified tax-specific mappings.
5. Implement supplier bills, company expenses, advances/clearance and supplier refunds.
6. Add reconciliation and reports; implement dependent Accounts frontend only after
   backend contracts and explicit companion-repository authorization.

Use transactions, database constraints and idempotent financial actions; test
concurrent posting/allocation, permissions, balanced journals, currency separation,
partial settlement, withholding eligibility, tax totals, cancellations/reversals
and advance clearance. Generate/inspect migrations; application database execution
requires separate approval. Reuse existing notifications/tasks for approval queues;
manager-authorized task acceptance remains distinct from financial approval and
its standing-policy versus per-task authorization decision remains open.

## Remaining decisions

- NGN reporting/FX source, rate timing, transfers, revaluation and precision/rounding.
- Period granularity, closing/reopening, opening balances, immutable posted records
  and reversal policy; Jan–Dec financial year itself is agreed.
- Pre-delivery invoice posting/revenue recognition and direct-order approval contract.
- Mixed/partial invoice cancellation, return evidence and refund/credit handling.
- Tax-specific legal/accounting mappings, rates, evidence, partial collection
  attribution and remittance obligations; examples above are not statutory rules.
- Self-approval/segregation, scope, thresholds and authorization effective dates.
- Document numbering integration, storage/retention and reconciliation rules.

Documentation approval does not implement these features or authorize live money
operations, migrations or changes to the frontend repository.
