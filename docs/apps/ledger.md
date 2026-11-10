# Ledger — cash book, party ledgers and reconciliation

## Itemized supplier bills and payments — 2026-11-08

This section supersedes earlier immediate bill posting and basic Add payment UI.
Purchase Ledger now has supplier invoice documents and itemized payment records.
Both reuse the CRM supplier UUID; there is no per-supplier database table.

### Bill and payment rules

- Bills capture supplier invoice reference/date/currency, description and ordered
  items (description, quantity, unit and unit price). Tax switch defaults off with
  an adjustable 7.5% rate. Discount switch accepts a fixed amount or percentage.
  Lines round half-up; document discount is applied before tax and apportioned
  across line values using cumulative rounding to preserve the exact total.
- Undelivered bills are outside supplier debt and can be edited with version checks.
  Delivered-on-create or Mark delivered posts the net goods subtotal once as
  purchase clearing debit / supplier payable credit. No cash moves on delivery.
  Delivered bills are immutable. Every bill change/delivery retains actor and snapshot.
- Payment items can be entered manually or copied from a same-supplier invoice into
  an independently editable draft. Confirmation requires a matching active cash/bank
  account and currency. Noncash methods require the paying bank name. Only payments
  already made can be confirmed; future bill/payment/delivery dates are rejected.
- Payment tax switch/rate and discount are editable before confirmation. Withholding
  defaults to 2% when enabled and is adjustable below 100%. Both tax and withholding
  use the goods value after discount, before tax. Cash out = goods + tax − withholding.
  The purchase ledger/allocations remain goods-only. Tax is recorded in the general
  journal and Tax Ledger. Migration0028 adds payment-or-delivery recognition,
  remittances and tax-aware reconciliation; statutory filing remains outside scope.
- Example: goods 100,000, tax 7,500, withholding 2,000 gives debit supplier payable/
  advance 100,000 and input tax 7,500; credit cash/bank 105,500 and withholding tax
  payable 2,000. The paired journal stores payable/cash 98,000, payable/WHT payable
  2,000 and input tax/cash 7,500 under the payment cash entry.
- Payments before delivery remain supplier advances. When their linked bill is
  delivered, available advances are applied automatically without another cash entry.
  Allocation dates respect both source dates and existing settlement/correction dates.
  Payments after delivery allocate up to the bill's outstanding goods amount.
  Linked payments cannot exceed the remaining document value; unrelated advances
  are recorded separately. Reversals and refunds preserve actual cash and tax legs.
- Confirmation is atomic across financial rows, evidence metadata and allocations;
  UUID/canonical-payload retry checks prevent duplicate payments or deliveries.
  Supplier receipt number is external evidence, distinct from the immutable internal
  posting reference. Existing historical financial records are not rewritten.

### Supplier receipt evidence

The optional supplier receipt scan may be uploaded on payment confirmation or later.
Supported types: validated PDF (up to 100 pages), JPEG, PNG and WebP; maximum 10 MB
and 25 million image pixels. Filenames are sanitized; content is parsed/verified,
not accepted solely on the claimed MIME. Checksums are retained for retry protection.

After confirmation, PATCH permits only the supplier receipt number and scan, with
version/UUID checks. It appends an immutable evidence revision; prior scans/numbers
remain available to authorized staff. Omitting a new file keeps the current scan.
Payment items, totals, rates, date, currency and account cannot be edited or deleted.

New files use the existing configured private document-storage service (Supabase by
default). Downloads route by the recorded provider and never expose storage paths,
credentials or public URLs. Downloads require explicit permission and use attachment,
private/no-store and nosniff headers. No supplier-facing documents or emails are
created. Upload failures roll back financial/evidence writes; failed DB commits
attempt remote-upload compensation and safely log compensation failures for review.

### API and permissions

- GET/POST `/ledger/purchase-bills/parties/{UUID}/`: paginated bills / create.
- GET/PATCH `/ledger/purchase-bills/{UUID}/`: detail/history / edit undelivered bill.
- POST `/ledger/purchase-bills/{UUID}/deliver/`: version, request_key, delivery_date.
- GET/POST `/ledger/purchase-payments/parties/{UUID}/`: history / confirm payment.
- GET/PATCH `/ledger/purchase-payments/{UUID}/`: detail / receipt evidence only.
- GET `/ledger/purchase-payments/{UUID}/evidence/{UUID}/download/`: private scan.

Bill create/edit uses items, reference, date, currency, tax_enabled/tax_percent,
discount_enabled/discount_type/discount_value, delivered and optional delivery_date.
Payment also uses account_id, payment_method, bank_name, receipt_number, optional
bill_id/bill_version, withholding_enabled/withholding_percent. Confirmation accepts
JSON or multipart with JSON `payload` plus optional `file`. Evidence PATCH uses
multipart/JSON payload {request_key, version, receipt_number} plus optional file.
History supports search and pagination; bills additionally filter delivery status.

Staff require view_purchaseledger and view_purchasebill/view_purchasepayment plus
the matching add/change permission. Delivery requires deliver_purchasebill and
add_purchasetransaction; payment creation additionally requires add_purchasetransaction
and add_cashbookentry. File access requires download_purchasepayment. Frontend uses
existing formatted permissions (ledger.purchasebill.create/edit/deliver and
ledger.purchasepayment.create/edit/download). New permissions follow the existing
additive executive hooks; other staff must receive explicit grants.

Legacy raw BILL POST is rejected so it cannot bypass delivery. Procurement bill
imports now create undelivered bill documents, retain their source and disappear
from the import inbox. Existing already-posted source imports remain unchanged.
Procurement source-item details remain fixed; delivery links the resulting posting.

Migration **0026_purchasebill_purchasebillevent_purchasepayment_and_more** adds
bills/events, immutable payments and evidence revisions, constraints/triggers and
Input Tax / WHT Payable journal account codes. No application migration was run.
No dependencies were added. Generated migrations were tested only on disposable
local PostgreSQL databases; storage providers were mocked, with no live uploads.

## Payment receipts — 2026-11-08

The approved receipt workflow replaces Sales Ledger's Add payment button with
**Add Payment Receipt** (mobile: **Add Receipt**). This section supersedes earlier
statements that receipt documents or all tax-related journal accounts are unavailable.

### Confirmation and accounting

A receipt is confirmed directly from the reviewed item draft and posted immediately,
regardless of delivery. Items support add/remove/reorder, description, quantity, unit,
unit price and an optional percentage discount, matching invoice rounding. Confirmed
receipts are immutable: corrections use existing authorized ledger operations rather
than editing or deleting the original acknowledgement.

VAT-paid and withholding switches default off. VAT is 7.5% of the net goods subtotal;
withholding is 2% of that same subtotal, before VAT. Cash received = goods + VAT − WHT.
The sales payment and allocations remain goods-only. For goods of NGN 100,000 with
both switches enabled, journal totals are:

| Account | Debit | Credit |
| --- | ---: | ---: |
| Receiving cash/bank account | 105,500 | |
| Withholding Tax Receivable | 2,000 | |
| Client receivable/payment | | 100,000 |
| VAT Payable | | 7,500 |

The existing paired journal stores this as cash/receivable 98,000,
WHT receivable/client receivable 2,000 and cash/VAT payable 7,500. They share the
cash entry and document reference. Total debits equal credits. Receipt confirmation
never credits Sales again; acceptance-based revenue recognition is unchanged.
WHT is retained as an asset and VAT as a liability. Tax remittance, statutory returns,
certificate processing and a separate tax-ledger UI remain outside this release.

Receipt creation, goods payment, cash entry, journal legs and allocation are one
transaction. A UUID request key plus canonical payload/actor protects retries.
An invoice link must belong to the same party/currency and retain the copied version.
Copying an invoice only copies its draft fields and does not modify the invoice.
If already posted and dated on/before payment, allocate up to outstanding goods value;
otherwise retain the payment as an advance. Delivery later does not silently allocate
this advance; the existing allocation action remains available.

Payment methods are Cash, Bank transfer, Card and Cleared cheque. An active account
in the chosen NGN/USD/GBP currency is mandatory. Cash uses a cash account; other
methods use a bank account and require the bank name. Only money already received
should be confirmed. Future receipt dates are rejected. Bank name/payment reference
are captured in the frozen document, not used to create another bank account.

Existing receipt reversal now reverses actual cash and every journal leg. Refunds
use cumulative proportional VAT/WHT rounding so a full refund restores all amounts
without penny drift. Refund input remains goods value; existing allocation-release
and permission rules apply. A refund rounding to zero cash is rejected for review.
Ordinary historical payments and purchase-ledger payment flows remain supported.

### Documents and API

Receipt preview adapts `src/templates/InvoiceTemplate.tsx` / `invoice.html` in the
frontend; PDF reuses the matching backend renderer with **RECEIPT** as the title.
It includes receiving account/method/bank, invoice link, item S/N, amounts, tax breakdown,
actual money received and currency-aware amount in words before client/Holan signatures.
Invoices and receipts share the locked daily sequence and client code, preventing
reference collisions: `HOL-{client code}-{YYMMDD}-{SN}`. Stored item references append
`.1`, `.2`, etc.; client documents display only S/N, consistent with invoice policy.

- GET/POST `/ledger/sales-receipts/parties/{party UUID}/`: paginated history / confirm.
- GET `/ledger/sales-receipts/{receipt UUID}/`: frozen detail and permitted email history.
- GET `/ledger/sales-receipts/{receipt UUID}/pdf/`: private, no-store PDF.
- POST `/ledger/sales-receipts/{receipt UUID}/email/`: custom/default professional
  message and PDF attachment through the existing notification provider.
- No PATCH/DELETE routes for confirmed receipts.

Creation takes invoice-style fields/items plus `request_key`, `account_id`,
`payment_method`, `bank_name`, `payment_reference`, `vat_paid`, `withholding_applied`,
and optional `invoice_id`/`invoice_version`. Values are validated and recalculated
server-side. Email takes `request_key`, `destination`, optional `subject`/`message`.
Same-key retries return the previous attempt without resending. Provider acceptance
is not proof of inbox delivery; unknown outcomes require checking before a new attempt.

Frontend detail: `/dashboard/ledger/sales/receipts/{UUID}`; protected PDF proxy:
`/api/ledger/receipts/{UUID}/pdf`. Backend permissions are staff +
`view_salesledger`, `view_salesreceipt`, and `add_salesreceipt`/`print_salesreceipt`/
`email_salesreceipt` as appropriate. Creation also requires `add_salestransaction`
and `add_cashbookentry`; copying an invoice requires `view_salesinvoice`.
Frontend uses the existing `ledger.salesreceipt.create/view/print/email` formatting.
New executive permissions follow the existing additive hooks; ordinary staff need grants.

Migration **0025_receiptemail_salesreceipt_and_more** adds frozen receipts/email
history, receipt immutability, amount constraints and the two allowed journal accounts.
It does not rewrite prior financial records. No dependencies were added. Application
migration execution and live email delivery are operator actions, not performed during
implementation verification.

## Itemized sales invoices — 2026-11-05

This section supersedes earlier immediate sales-invoice posting and document limitations.
Sales Ledger **Add invoice** creates an editable, undelivered invoice outside the
financial ledger. Confirmation allocates `HOL-{client code}-{YYMMDD}-{daily SN}`
using the Lagos confirmation date, a unique three-letter client code and a company-wide
daily sequence. Items append `.1`, `.2`, etc. Editing invalidates confirmation;
previous confirmed revisions remain immutable and printable. The reference is retained.

- Items support description, quantity, unit, unit price, removal and ordering.
  Server-side decimal arithmetic rounds each line half-up to two decimal places.
- Confirmed invoices can be printed/downloaded as PDF or emailed with a custom/default
  message and PDF attachment. Client-facing titles and messages say **Invoice**.
  Documents include billing details, date, references, delivery status, signature
  spaces, goods subtotal, 7.5% VAT and total. VAT is document-only, never journaled.
- Mark delivered requires a confirmation and delivery date. It atomically posts the
  discounted goods subtotal once to sales receivables/deferred revenue and its double-entry journal.
  Delivery does not imply acceptance: the existing acceptance flow recognizes revenue.
  No cash movement or payment condition is added. Delivered invoices have no edit action.
- UUID retry keys and optimistic version checks protect saves, confirmation, delivery
  and email requests. Email history distinguishes provider acceptance from unknown
  outcomes; retrying the same key never sends another email. Provider acceptance does
  not prove inbox delivery. No live email was sent during verification.
- Procurement invoice imports now create undelivered invoice documents. Their source
  item details remain linked and fixed; confirmation permits direct PO selection.
  Existing posted invoices and procurement links remain valid.

Backend routes are under `/ledger/sales-invoices/`: `parties/{party UUID}/`,
`{invoice UUID}/`, and `{invoice UUID}/{confirm,deliver,email,pdf}/`.
PDF accepts an optional `revision` UUID. Frontend detail is
`/dashboard/ledger/sales/invoices/{invoice UUID}`; PDF is relayed through the
protected `/api/ledger/invoices/{invoice UUID}/pdf` route.

Migration **0024** adds invoice documents/items, frozen revisions, client codes,
daily numbering, operation records and email history. It has been generated and tested
in disposable databases, but has NOT been applied to the application database.
Staff require `ledger.view_salesledger` and `ledger.view_salesinvoice`, plus the
relevant `add_salesinvoice`, `change_salesinvoice`, `confirm_salesinvoice`,
`deliver_salesinvoice`, `print_salesinvoice` or `email_salesinvoice` permission.
Delivery additionally requires `add_salestransaction`. Existing permission formatting
is preserved. Newly created permissions follow the existing executive grant hooks;
ordinary staff grants must be assigned explicitly. Payments, partial payments and tax
accounting changes are outside this release. No dependencies were added.

## Current scope — approved non-tax completion (2026-11-05)

The cash book, sales/purchase ledgers, settlement corrections, saved Reconciliation
and Statement, procurement approval/import, and NGN/USD/GBP accounts are implemented.
Tax is explicitly excluded: no VAT/WHT calculation, withholding settlement, tax
accounts or remittance workflow is added. Earlier stage descriptions below are
historical; this section supersedes their NGN-only and unavailable-feature limits.
Financial years remain January–December and seeded account balances start at zero.
All clients/suppliers share transaction tables linked to the CRM party UUID.

### Posting and corrections

- Existing transaction POST accepts NGN, USD or GBP. PAYMENT accepts account_id
  (defaults to the existing primary NGN cash holder); its active account must match
  the currency. Invoice/bill posting never creates a cash movement. Sales revenue
  remains deferred until acceptance. Supplier bills use purchase clearing.
- GET transactions/outstanding/choices supports currency. Transaction history is
  year/date filtered with continuous running balances; choices searches all years.
  Summary charges/payments represent increases/reductions, including corrections.
- POST `party-ledgers/{kind}/parties/{uuid}/operations/`: request_key UUID, action,
  date, currency, reason; transaction_id for source-based operations; amount where
  applicable. ALLOCATE replaces the selected payment's allocations with the supplied
  `{document_id, amount}` list (empty releases all). No new cash/journal is posted.
  An earlier advance can settle a later document on an effective date no earlier
  than either source. Corrections cannot precede existing settlement events.
- CREDIT reduces a document; REFUND returns unallocated payment value through its
  original cash/bank account; ADJUST_UP/DOWN posts a reasoned balance correction;
  REVERSE creates an opposite transaction/journal and opposite cash when applicable.
  Released allocation links and original records remain immutable. Reversal is
  supported for invoices, bills, payments and adjustments; linked credit/refund
  histories require explicit review/adjustment rather than deleting those records.
- Standalone sales invoices accept DISPATCHED/REJECTED/ACCEPTED evidence and goods
  value. Acceptance transfers deferred revenue to income without changing cash or
  the party balance. Accepted goods cannot be cancelled. Dispatched goods must have
  documented rejection before cancellation. Procurement-linked invoices use actual
  procurement delivery/outcome evidence, not manual acceptance overrides.
- Posting/correction services lock the party and serialize submission UUIDs. One
  database transaction includes related cash, journal, allocations and audit actor.
  Identical retries return existing results; changed submissions are rejected.

### Reconciliation and Statement

POST `party-ledgers/{kind}/parties/{uuid}/statements/` accepts request_key,
date_from/date_to and currency. A frozen snapshot contains company/party identity,
all selected transactions (not a page), prior balance, charges/reductions, payments,
running/closing balances and outstanding/advance schedules as of the end date.
Statement ranges may span financial years. No exchange conversion or mixed-currency
total is performed. GET that collection lists snapshots; GET `statements/{uuid}/`
returns the immutable snapshot and review history. POST the detail with request_key,
status AGREED/DISPUTED and notes records a separate actor/timestamp review.
Later postings cannot rewrite a saved snapshot. A new snapshot is required to
present revised figures. Reviews are staff-recorded evidence, not an external client
signature or automatic proof of agreement.

### Procurement and account settings

GET/POST `procurement-inbox/` requires approve_procurement plus existing procurement
request visibility. It lists pending quotation approvals, submitted finance requests
and unposted supplier purchases. APPROVE/REJECT decides a requested quotation revision.
APPROVE_REQUEST/REJECT_REQUEST reviews other submitted finance requests without moving
funds. IMPORT_INVOICE/IMPORT_BILL posts source line goods values once, using unique
source links; the normal sales/purchase view/create permissions remain required.
Finance submission is available again. An approved invoice line can originate a
procurement PO without a quote; the full line quantity/value is matched and cannot
be ordered twice. Delivery acceptance automatically recognizes linked invoice revenue,
including when the invoice is imported after acceptance. Invoice printing/sending
and supplier inventory/COGS valuation are not inferred from these posting operations.

GET/POST/PATCH `accounts/` lists, adds, renames and activates/deactivates cash/bank
accounts. Currency and account type are fixed once created; use a separate account
for another currency. NGN is primary, with USD and GBP available. No external bank
account is opened. Six zero-balance Primary cash/bank records are seeded; existing
NGN cash entries are attached to Primary NGN cash without changing amounts. Cash-book
and journal list filters accept currency and optional account UUID. Currency
registration/exchange conversion and configurable nominal-account charts are separate
future extensions; current supported currencies are centralized in ledger.models.

### Additional assigned permissions

| Django permission | Frontend permission | Purpose |
|---|---|---|
| ledger.correct_salesledger | ledger.salesledger.correct | Sales allocations/corrections |
| ledger.correct_purchaseledger | ledger.purchaseledger.correct | Purchase allocations/corrections |
| ledger.recognize_salesledger | ledger.salesledger.recognize | Standalone delivery/acceptance evidence |
| ledger.approve_procurement | ledger.procurement.approve | Scoped procurement decisions/imports |
| ledger.view_reconciliation | ledger.reconciliation.view | Read/print saved statements |
| ledger.add_reconciliation | ledger.reconciliation.create | Create statement snapshots |
| ledger.confirm_reconciliation | ledger.reconciliation.confirm | Record statement reviews |
| ledger.view/add/change_ledgeraccount | ledger.ledgeraccount.view/create/edit | Account settings |

Ledger view permission is additionally required for its operations/statements.
Refund/reversal actions also require cash-book create permission. Preparation and
approval may be performed by the same authorized staff member; actor/time is retained.
CEO/CTO access comes from assigned permissions and the existing additive provisioning,
not role-name bypasses.

### Rollout and verification

New migrations 0021–0023 add the accounts, signed effects, dated allocation releases,
corrections, immutable snapshots/reviews, acceptance and procurement links/decisions.
0021 seeds accounts before adding the FK and backfills old PAYMENT effects and
allocation dates, temporarily disabling only their immutability triggers inside the
migration transaction. It does not erase transactions. PostgreSQL schema locks are
required: run migrations in an appropriate deployment window after backup.
No application migrations are executed by this build. Older 0013 remains a destructive
historical reset; inspect pending migration plans if upgrading a database that has
not already passed that reset. Do not roll back the new models after live use without
preserving the new financial/audit records.

The remaining sections document the earlier rebuild stages and basic cash-book
correction contract (standalone cash revisions keep their original accounting dates).

## Behaviour

- Financial years run January 1–December 31. Initial opening balance is zero;
  existing transactions carry forward into later periods and years.
- RECEIPT: debit Cash, credit Cash income. EXPENSE: debit Expenses, credit Cash.
  CAPITAL: debit Cash, credit Capital introduced, so owner funding is not income.
- Amounts are positive fixed-point NGN values with two decimal places. Each
  journal row stores one amount, a debit account and a credit account; therefore
  debit and credit totals are equal by construction. The initial account names
  are fixed for this feature, not a configurable chart of accounts.
- Date, description and amount are required; reference is optional. Negative
  cash balances are permitted and highlighted in the UI, allowing missing earlier
  receipts to be recorded later. No approval stage or tax calculation is included.
- Create writes cash entry and journal atomically. An optional UUID request_key
  makes a repeated identical submission idempotent; the frontend always sends it.
- Edit requires the current version and a nonblank reason. It locks the entry,
  reverses the old journal and posts the replacement atomically. Concurrent stale
  updates are rejected. Delete requires version/reason and voids the entry with a
  reversal; no financial history is physically deleted.
- Reversals use the original accounting date, including corrections to previous
  years. created_at records when the correction was actually entered. Years are
  not yet closable/locked; historical period totals reflect subsequent corrections.
- Original creator, last editor, timestamps, reversal reason and journal actor
  remain recorded. Journal UPDATE/DELETE are also blocked in PostgreSQL.

## Permissions

Active staff or superusers only. CEO/CTO names provide no access bypass. Use the
existing staff/role permission editor and additive executive provisioning hooks.

| Operation | Django permission | Frontend permission |
| --- | --- | --- |
| Read cash book | ledger.view_cashbookentry | ledger.cashbookentry.view |
| Create | ledger.add_cashbookentry | ledger.cashbookentry.create |
| Edit | ledger.change_cashbookentry | ledger.cashbookentry.edit |
| Delete/void | ledger.delete_cashbookentry | ledger.cashbookentry.delete |
| Read journal | ledger.view_cashbookjournal | ledger.cashbookjournal.view |

Mutations require cash-book view permission as well as their action permission.
Journal access is independently assignable and read-only. HEAD/OPTIONS are also
permission checked. Backend enforcement is authoritative; frontend pages,
navigation and Server Actions mirror it. No additional journal permission is
needed to record an authorized cash transaction.

## API

| Method | Path | Behaviour |
| --- | --- | --- |
| GET/POST | /ledger/cash-book/ | List active entries / record entry |
| GET/PATCH/DELETE | /ledger/cash-book/{uuid}/ | Inspect / correct / void |
| GET | /ledger/cash-book-journal/ | Paginated journal including reversals |
| GET | /ledger/cash-book-journal/{uuid}/ | Inspect immutable journal pair |

Create body: date (ISO), kind (RECEIPT/EXPENSE/CAPITAL), amount (decimal string),
description, optional reference and request_key. PATCH accepts any editable
create fields except request_key and requires version plus reason. DELETE body
contains version and reason. Voided entries remain available by detail ID but
are excluded from the active cash-book list. Unrelated old ledger routes remain
removed. PUT and journal mutation endpoints are not supported.

List filters: year (defaults to current year), date_from, date_to, page, page_size.
Dates must fall within the selected year. Standard pagination applies. Records
sort by accounting date, creation time and UUID. Cash-book results include a
running_balance calculated across the entire period, not restarted on each page.
The response summary contains currency, year, date_from/date_to, opening_balance,
debit, credit and closing_balance; amounts are decimal strings. Opening balance
includes active entries before the selected start date. Journal entries contain
amount, debit_account, credit_account, source entry ID and optional reverses ID.

## Implementation and rollout

Models: CashBookEntry, CashBookJournal. Domain writes: ledger/services.py.
HTTP validation/access: ledger/serializers.py, views.py and urls.py.
Next.js: /dashboard/ledger and /dashboard/ledger/journal.

0014_initial adds the two new tables/constraints; 0015_cash_journal_immutable adds
journal protection. They depend on the prior, destructive 0013_reset_ledger_to_blank
migration. Historical migrations are retained. Applying the chain can remove the
retired ledger data if 0013 has not run; review the target migration plan and backup
before an explicitly approved application rollout. No application migration was
performed during this feature build. Test migrations use local isolated settings.

Tests cover permissions/role grants, validation, duplicate submissions, atomic
posting, reversals, stale updates, immutable history, year/date filtering, carried
balances and pagination. No new dependencies or external notifications are needed.

## Party ledgers — stage 1

Sales and purchase ledger workspaces now provide party lookup and remembered
selection only. Financial transactions, balances, invoice/bill/payment entry,
printing and reconciliation remain future stages.

- GET /ledger/party-ledgers/{sales|purchase}/ returns kind and selected_party
  (null if unset or no longer eligible).
- PUT on the same path takes {"party_id": "UUID"} or null to clear. Selection is
  always scoped to request.user; callers cannot choose another user's settings.
  Repeating the same selection updates one row, with no financial side effects.
- GET /ledger/party-ledgers/{sales|purchase}/parties/?search=... provides standard
  pagination and case-insensitive name search (maximum 100 characters).
- Only id, display_name, entity_kind and status are exposed. Sales uses active
  CLIENT roles; purchase uses active SUPPLIER roles. A party can appear in both.
  Archived/merged parties are excluded, following CRM directory conventions.
  Other CRM statuses remain visible with their labels for viewing purposes.
- Access requires active staff (or superuser) and ledger.view_salesledger or
  ledger.view_purchaseledger respectively. Frontend strings are
  ledger.salesledger.view and ledger.purchaseledger.view. These grant minimal
  ledger lookup access, not full CRM editing or sensitive CRM data access.
- Personal selection uses the matching view permission; it is not a financial
  mutation. No invoice/payment permissions are introduced at this stage.
- PartyLedgerSelection has a unique user/kind key and references existing CRM
  parties; it does not duplicate client/supplier records. Deleting a referenced
  party clears the preference. Eligibility is rechecked on every read/save.

Migration 0016_partyledgerselection adds the preference table and two custom view
permissions through normal Django permission creation. Existing executive hooks
handle new permissions additively. No live migration was run during development.

## Party ledgers — stage 2 posting foundation

Stage 2 adds immutable PartyLedgerTransaction records, a protected internal posting
service, transaction history and NGN balance summaries. Stage 3 now exposes
invoice/bill/payment entry through the protected posting endpoint described below. No live transactions were created during testing.

`ledger.party_posting.post_party_transaction(actor=..., payload=...)` validates
request_key (required UUID), party_id, ledger_kind, transaction_type, currency,
date, amount, description and optional reference. NGN is currently the only
accepted currency, matching the existing cash book; unsupported currencies fail
explicitly. Adding currencies and separate currency cash accounts remains future
work. Amounts are positive decimals with at most two decimal places.

The internal account mapping is:

| Transaction | Debit | Credit | Cash book |
| --- | --- | --- | --- |
| Sales invoice | Receivable | Deferred revenue | None |
| Supplier bill | Purchase clearing | Payable | None |
| Client payment | Cash | Receivable | Money in |
| Supplier payment | Payable | Cash | Money out |

An invoice does not recognize earned sales before acceptance. Supplier bills use
a clearing account pending the later document/classification workflow. This is
posting infrastructure, not complete VAT, delivery acceptance, inventory costing,
invoice issuance or settlement allocation. A payment without charges produces an
advance/credit balance. Payments do not recognize a second sale or expense.

The existing paired journal supports both cash and noncash postings. Every party
transaction has exactly one journal pair when created through the service. Cash
payments create one linked cash entry in the same database transaction. Any
failure rolls back all writes. A party row lock serializes concurrent posting;
a unique request UUID and full normalized-payload/actor comparison make retries
idempotent. Reusing the key for changed input fails; concurrent same-input retries
return the same transaction. Unique-key conflict handling also rolls back any
cash entry prepared for a conflicting request.

Posting requires active staff/superuser plus ledger.view_salesledger and
ledger.add_salestransaction, or the purchase equivalents. Payments additionally
require ledger.add_cashbookentry. Frontend formatted posting permissions are
ledger.salestransaction.create and ledger.purchasetransaction.create; these are
used by the stage 3 forms. Posting only accepts an active party with the
matching active CRM role. Existing transactions can still be read when that role
or status changes, and historical parties remain available in ledger selection.

GET /ledger/party-ledgers/{sales|purchase}/parties/{uuid}/transactions/ is protected
by the matching ledger view permission and scoped to an eligible/historical party.
It supports year/date_from/date_to/page/page_size and currency=NGN. Results contain
posting details, the journal accounts, author and running_balance. The summary
contains opening_balance, charges, payments and closing_balance. Balances are
amounts due (client owes Holan for sales; Holan owes supplier for purchases).
Negative means advance/credit. Opening includes transactions before the selected
start date. Pagination does not restart running balances. POST is available as described in stage 3; PATCH/DELETE remain unavailable.

Migrations 0017 and 0018 add transaction storage, journal references/account-pair
support, permissions and database guards. Journal records remain immutable;
party transactions cannot be updated/deleted. Cash-book edits/deletions of linked
payments are denied by the service and database, and hidden in the UI. Standalone
cash entries retain their current correction workflow. Later corrections/refunds
must use a dedicated party-ledger workflow rather than breaking these links.

No application migration was run. These migrations preserve existing cash-book
records; the earlier reset migration remains destructive if still unapplied.

## Party ledgers — stage 3 entry and payment allocation

The sales/purchase transaction collection now accepts POST. The selected route
party UUID and ledger kind are authoritative; payload values cannot redirect a
posting into another party's ledger. View permission and the corresponding
add_salestransaction/add_purchasetransaction permission are required. Payments
also require add_cashbookentry. Nonstaff/inactive users are denied. PATCH/DELETE
remain unavailable pending the dedicated correction workflow.

All parties still share one PartyLedgerTransaction table with a CRM Party UUID
foreign key. PartyPaymentAllocation is one shared relationship table linking
payments to invoice/bill records; no per-client or per-supplier tables are created.

Entry fields: required request_key UUID, transaction_type (INVOICE for sales,
BILL for purchases, PAYMENT for either), date, amount, description, currency=NGN
and reference. Reference is required for public invoice/bill entry and optional
for payments. The reference is checked case-insensitively within party/ledger/
document type under the party posting lock. Reusing the same submission ID with
identical normalized input returns its existing posting; changed input fails.
The posting API records document totals; it does not generate itemized invoice
PDFs or calculate tax, withholding, inventory cost or delivery acceptance.

Payments optionally include allocations: a list of {document_id, amount}, maximum
100 distinct documents. Each amount must be positive, cannot exceed the document's
current outstanding amount, and total allocations cannot exceed the payment.
Documents must match party, ledger and currency and be dated no later than the
payment. Allocation order does not affect duplicate-submission detection. A
payment may be partially allocated or entirely unallocated as an advance. Applying
an already-posted advance to a later document is not yet exposed by this API;
no duplicate cash payment should be created to achieve that future operation.

GET /ledger/party-ledgers/{sales|purchase}/parties/{uuid}/outstanding/ lists unpaid
NGN invoice/bill records across years, with name/reference search and standard
pagination. Returns id, date, reference, description, amount and outstanding.
Transaction history includes remaining_amount: current outstanding on invoices/
bills or currently unallocated on payments. These current settlement values are
separate from the selected-period running account balance.

Party transaction, cash entry, paired journal and allocation rows are written in
one atomic operation. The existing party row lock serializes allocation checks.
Database insertion guards independently enforce matching source/type/currency/
date and payment/document limits; allocation records are immutable. Any failure
rolls back the entire posting. Allocating a payment does not add another cash
movement or change the party account balance a second time.

Migrations 0019/0020 add the normalized allocation request snapshot, shared link
table/constraints and database guards. No existing financial records are rewritten;
existing payments start unallocated. Application migrations were not run during
development. Tests include cross-party rejection, duplicate references, permission
checks, partial/multiple allocations, advances, rollback and competing payments.

## Invoice document presentation

Invoice previews and confirmed PDFs use the supplied invoice layout: company
logo/contact details at the upper right, billing metadata, S/N/items/price/quantity/
amount columns, subtotal, VAT, total and separate client (left) and Holan Mgt
(right) signature/date blocks. Billing name and address each start below the
Billed to label. Company contacts come from `PROCUREMENT_DOCUMENT_COMPANY`; blank
phone values use the supplied WhatsApp numbers 2348073033767 and 2348090964567
(including historical snapshots without rewriting them). Invoice detail GET returns
that public `company` object for the frontend preview.

Item references such as `HOL-ABC-261107-001.1` remain internal identifiers in
immutable snapshots and staff reference details. Client documents display only
item S/N (`1`, `2`, …); the invoice number remains in document metadata. Historical
snapshots render using the same S/N rule without rewriting stored data. Downloaded
PDFs and emailed attachments share `render_invoice_pdf`.


## Invoice units and percentage discounts

Invoice forms offer common SI/count/packaging/time units and a custom short-form
option. Existing item quantity/unit columns are reused. Documents display compact
quantities (4 pcs, 1.25 kg) without padding to four decimal places. Legacy long
unit names are abbreviated for display; custom values are preserved.

Invoice create/edit accepts optional `discount_percent` (default zero, up to two
decimal places, 0–100). The discounted goods total must remain positive and within
the existing monetary limit, so a 100% discount is rejected. For each line, quantity
× original unit price is rounded half-up to cents, then its percentage discount
is rounded half-up to cents and deducted. Item `amount` and invoice `subtotal`
store the resulting net values; original unit prices remain unchanged. The
invoice-wide discount amount is the sum of the per-line discounts, avoiding
rounding drift. VAT (7.5%) is calculated on the net subtotal and remains document-only.

No discount columns or unit tables are added. Nonzero discount percentages are
retained in the existing immutable CREATE/EDIT `InvoiceOperation.payload`; confirmed
`InvoiceRevision.snapshot` records `discount_percent`, `gross_subtotal`,
`discount_amount`, per-item `gross_amount`, net `amount`, VAT and total. Missing
legacy discount metadata means zero. Invoice detail GET additionally returns a
`pricing` object with discount_percent, gross_subtotal, discount_amount, vat and
total for editing and preview. Retrying a historical zero-discount submission
remains compatible; changed discount input requires a new submission key.

Previews/PDFs show gross line values, gross subtotal, a discount deduction and net
subtotal when the rate is positive; zero-discount documents omit the discount row.
Downloads and emailed attachments use confirmed snapshots. Delivery posts only
net goods amounts to the existing sales/journal flow; procurement-linked item
identities, quantities and source prices remain protected while net values flow
into ProcurementInvoiceLine and acceptance/recognition. No cash movement is added.

## Staff Ledger

Staff Ledger uses existing UUID user accounts (staff users, users with a staff
profile, and historical ledger staff). It does not create CRM parties or tables
per staff member. The last selected staff member is saved per operator. Inactive
staff remain available for settling historical balances. Only active staff actors
with assigned permissions, or Django superusers, can access the ledger.

Financial year defaults to January–December; opening balance is zero until money
is recorded. NGN, USD and GBP are separate balances and accounts. No conversion,
payroll tax, salary accrual or payroll run is introduced.

### Actions and posting

- **Pay Staff** records date, category, description, positive amount, currency,
  active cash/bank account, optional reference and optional supporting document.
  Salaries, allowances, transportation and ordinary staff expenses debit EXPENSE
  and credit CASH. They never create staff debt. Loans and advances debit
  STAFF_RECEIVABLE and credit CASH.
- **Receive from Staff** records the same money details. Linked loan repayments
  or advance/change returns debit CASH and credit STAFF_RECEIVABLE. Staff levies,
  contributions and other income debit CASH and credit INCOME.
- **Retire Advance** submits an ordered expense list linked to one staff advance:
  date, description, and 1–100 items containing expense date, category, description,
  positive amount and optional supporting receipt. The order is preserved as
  one-based `position`. Submission does not post money or expenses. One pending
  or approved submission is permitted per advance. A rejected submission remains
  in history; a corrected breakdown can be submitted as a new record.
- **Approve/reject retirement** requires its own permission and a dated reason.
  The supplied `expected_remaining` protects against approval based on an outdated
  advance balance. Approval debits EXPENSE per ordered item and credits the remaining
  STAFF_RECEIVABLE; any excess credits STAFF_PAYABLE. It creates no cash entry.
  Self-approval is permitted; preparer, approver, reason and timestamps remain recorded.
- **Advance reimbursement** is a linked Pay Staff category: debit STAFF_PAYABLE,
  credit CASH. Payments cannot exceed the approved reimbursement due. Repayments
  and returns cannot exceed the linked recoverable balance. Linked records must
  belong to the same staff member/currency and cannot precede their source or any
  existing financial settlements.

Example: pay an advance of NGN100,000, approve expenses of NGN75,000, then receive
NGN25,000 change. There are two cash movements and one noncash expense posting.
For NGN120,000 expenses against NGN100,000 remaining, NGN20,000 becomes reimbursement
due to staff. Any return made before approval reduces the available advance first.
Loans, unretired advances, change due and reimbursements are reported separately;
salaries and allowances are shown as payments without being treated as debt.

### Staff API

All paths start `/ledger/staff-ledger/`. Lists use standard DRF pagination.

| Path | Methods | Purpose |
| --- | --- | --- |
| `/` | GET, PUT | Read/save last selection; PUT `{staff_id}` |
| `staff/` | GET | Search names/usernames with `search`, `page`; minimal identity only |
| `staff/{uuid}/entries/` | GET, POST | Filter transactions by `date_from`, `date_to`, `currency`, `page`; post cash movement |
| `entries/{uuid}/` | GET | Posted record, actor, linked advance and safe document metadata |
| `staff/{uuid}/choices/` | GET | All-year source lookup: `mode=advance|loan|reimbursement|retire`, `currency`, `search`, `page`; current balances |
| `staff/{uuid}/retirements/` | GET, POST | All-date/all-currency submission history; submit expense breakdown |
| `retirements/{uuid}/` | GET | Ordered expenses, evidence, preparer, decision and current remaining advance |
| `retirements/{uuid}/decision/` | POST | Approve/reject with `{request_key, approved, date, reason, expected_remaining}` |
| `documents/{uuid}/download/` | GET | Permission-checked private attachment download |
| `staff/{uuid}/statements/` | GET, POST | Saved statement history / generate immutable snapshot |
| `statements/{uuid}/` | GET | Complete printable snapshot |

Money POST accepts `{request_key, kind: PAY|RECEIVE, category, date, currency,
amount, account_id, description, reference?, related_entry?}`. Related entry is
required for ADVANCE_RETURN, LOAN_REPAYMENT and ADVANCE_REIMBURSEMENT only.
Retirement POST accepts `{request_key, advance_id, date, description, items}`.
Items are `{date, category, description, amount}`; expense categories are TRANSPORT,
ACCOMMODATION, MEALS, PURCHASES, COMMUNICATION and OTHER.

For uploads, send multipart `payload` containing the JSON body. Money forms accept
`file`; retirement forms accept `item_0`, `item_1`, etc., aligned with the final item
order. Documents must be genuine PNG/JPEG/WebP or PDF, at most 10 MB each and 20 MB
combined. Existing content validation limits also apply. New documents use private
Supabase storage; downloads use their recorded provider. Storage failures roll back
financial changes and compensate earlier successful uploads. Private storage paths
are never returned. Staff UUIDs are opaque storage owner keys under the `staff_ledger`
category; no artificial CRM Party is created.

Statement POST accepts `{request_key, date_from, date_to, currency}`. The frozen
**Reconciliation and Statement** includes every transaction in the range (not merely
the displayed page), prior receivable/payable balances, money paid/received,
approved advance expenses, running balances, closing balances and the four separate
outstanding categories. The company identity comes from PROCUREMENT_DOCUMENT_COMPANY.
Historical categorization uses dated financial approvals, not today's submission state.
Pending/rejected expense submissions are excluded from financial statements.

### Staff permissions, integrity and deployment

All access requires `ledger.view_staffledger`. Actions additionally require
`pay_staffledger`, `receive_staffledger`, `retire_staffledger`, `approve_staffledger`,
`download_staffledger` or `print_staffledger`. Cash actions also require
`ledger.add_cashbookentry`. Frontend format is `ledger.staffledger.view|pay|receive|
retire|approve|download|print` and `ledger.cashbookentry.create`, using the existing
permission formatter. Executive permission creation hooks remain additive; no role
name bypass is introduced. Staff directory access reveals only UUID, name, username
and active status, not private profile or bank data.

Each financial action is atomic and idempotent using a request UUID and normalized
payload. Staff-row locks serialize settlements; advisory locks serialize retries.
Posted staff entries, expense submissions/decisions, evidence and statements are
append-only with database UPDATE/DELETE guards. Staff-linked cash entries cannot
be independently edited or voided from Cash Book. The paired journal supports
STAFF_RECEIVABLE and STAFF_PAYABLE, maintaining equal debit/credit amounts.

Migration **0027_staff_ledger** adds shared staff models, indexes/constraints,
permissions, a journal source link, two nominal account codes and immutable guards.
It depends on migration0026 and preserves existing data. It was generated for operator
review; application migrations are not executed as part of feature development.
No new dependencies. Tests cover posting, permission gates, ordered item evidence,
rollback/cleanup, prior balances, currency separation, full statement snapshots,
idempotency and concurrent settlement/approval.


## Tax Ledger (migration 0028)

`/ledger/tax/` returns paginated immutable movements with filtered opening/closing
and period totals; `balances` is explicitly company-wide for the selected currency
as at `date_to`. Filters: `date_from`, `date_to`, `currency`, optional party UUID,
`tax_type=ALL|VAT|WHT|CIT`. VAT and WHT are never offset against one another.

Posting hooks in invoice delivery, supplier delivery, sales receipts, purchase
payments and corrections run inside their original atomic financial transaction.
VAT is recognised at payment or delivery, whichever comes first, once per document.
Later receipts/payments settle previously recognised VAT. Issuance alone does not
post VAT. Input VAT defaults eligible for taxable resale; approved classification
can move it to pending review or non-recoverable expense. WHT held by clients is an
asset; WHT withheld from suppliers is a liability. Raw goods allocations cannot
reassign VAT-bearing payments to a different tax source.

`POST /ledger/tax/requests/` prepares an immutable request (JSON or multipart with
JSON `payload` and optional `file`, 10 MB JPEG/PNG/WebP/PDF). Required base fields:
UUID `request_key`, `action`, completed `date`, `period` YYYY-MM, `currency`, and
`description`. Supported actions:

- `REMIT`: `tax_type` VAT/WHT/CIT, positive `amount`, `authority`, payment `reference`
  and active matching-currency `account_id`. Records an already-made payment;
  creates cash and journal entries only on approval. VAT settlement also records
  eligible input-credit offset; excess input credit carries forward.
- `VAT_SETTLE`: delivered `document_key` (`sales:UUID` or `purchase:UUID`), account
  and amount. Settles outstanding document VAT without a duplicate goods payment.
- `CLASSIFY`: purchase tax `document_key`, `classification` AVAILABLE/PENDING/
  NONRECOVERABLE. Existing used credits must be released before reclassification.
- `CIT_ASSESS`: manual accountant-entered liability amount; no automatic tax engine.
- `CIT_CREDIT`: applies unused client WHT to outstanding assessed CIT without cash;
  application/remittance is capped by the assessment year selected by `period`.
- `ADJUST`: audited positive amount and `adjust_field`, optionally `decrease=true`.
- `OPENING`: reviewed historical delivery document, applying only missing VAT and
  its party-control balance; existing historical payments must first be linked.
- `REVERSE`: `source_entry` UUID for a remittance, CIT assessment/credit or manual
  adjustment; appends inverse tax/journal/cash records using the original period.
  An already-applied credit or settled liability cannot be invalidated silently.

`GET /ledger/tax/requests/` supports `status=all|pending|approved|rejected` and
pagination; `GET /ledger/tax/requests/{id}/` includes preparation, evidence and
decision audit. `POST /ledger/tax/requests/{id}/decision/` accepts UUID request key,
`approved` boolean and `reason`. Self-approval is allowed with both actors retained.
Both approver and operation permission are required. Cash operations additionally
require `ledger.add_cashbookentry`. Replays use the original idempotency key;
changed payloads or competing over-settlements are rejected.

Permissions use the existing formatting: `ledger.taxledger.view`, `.remit`,
`.approve`, `.adjust`, `.configure`, `.apply`, `.export` (Django codenames
`view_taxledger`, etc.). Migration creates permission definitions; staff grants
remain explicit. Existing revoked executive permissions are not restored.

Additional endpoints:

- `GET /ledger/tax/sources/`: searchable, currency-specific paginated tax sources.
- `GET /ledger/tax/parties/`: minimal permissioned party names/IDs for filters.
- `GET/POST /ledger/tax/policies/`: immutable future/current effective VAT/WHT
  policy versions, optional client-specific WHT override, rate, enabled and reason.
  VAT defaults 7.5%; selected WHT defaults 2%. Existing rates remain frozen.
- `GET /ledger/tax/rates/`: date/party and optional invoice for permissioned sales
  forms. Review submits expected rates; changed rates require another review.
- `GET /ledger/tax/documents/{id}/download/`: private recorded-provider evidence.
- `GET /ledger/tax/export/`: permissioned CSV with filters and formula protection.
- `GET /ledger/tax/history/`: read-only missing-link/discrepancy/delivery-gap review.

New reconciliation snapshots include `tax` summaries and row-level VAT recognised,
VAT cash, WHT, actual cash and combined balances. Original goods-only balances are
retained for compatibility. Previously saved snapshots remain unchanged. Tax-linked
cash entries cannot be edited/voided independently.

### Rollout and historical records

Review and run migrations through 0028 explicitly on the application database.
Then preview `python manage.py backfill_tax_ledger`. After review, an operator may
run `python manage.py backfill_tax_ledger --apply`. Apply links historical payments
and corrections, checks immutable journals for discrepancies and is repeatable;
it does not repost cash, alter old journals or auto-approve delivery gaps. Use
approved OPENING requests for those gaps after historical links are complete.
Backfill discrepancy errors roll back the entire link operation. Run during a
maintenance window because it serialises against financial posting.

No electronic invoicing, WHT certificates or live authority submission is included.
The user-selected VAT timing is an operational policy, not a declaration of full
statutory compliance; see [approved design](../design/TAX_LEDGER.md).


## Compact printed reconciliation — 2026-11-10

New party snapshots include `print_version: 2`, per-row `document_vat` (full
signed delivery VAT, distinct from VAT first recognised on this event), and
`document_reference`. Supplier payment reference is the latest saved supplier
receipt number at snapshot generation; missing external numbers remain blank,
not replaced by the internal PAY-UUID. Other invoice/receipt references retain
the posted document number. These are additive snapshot fields; no migration or
financial posting change. Previous immutable snapshots remain unchanged.

The companion TSX print template uses the supplied HTML's compact coloured summary
and five-column table, with right-aligned title/currency and short-month dates.
Invoice totals use delivery VAT, cash totals use actual cash, WHT remains separate,
and credits/adjustments remain visible when needed to reconcile the saved closing
balance. Staff statements adopt the same visual template with staff-specific labels
and independent due-from/due-to balances. Internal audit/review information is
screen-only. Generate a new statement to obtain the additional snapshot fields.
