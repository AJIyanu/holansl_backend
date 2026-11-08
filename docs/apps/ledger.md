# Ledger — cash book, party ledgers and reconciliation

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
