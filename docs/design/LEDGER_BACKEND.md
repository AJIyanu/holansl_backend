# Ledger backend design

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
  goods subtotal once to sales receivables/deferred revenue and its double-entry journal.
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

Current non-tax implementation is specified in [ledger API documentation](../apps/ledger.md).
All party records share UUID-linked tables. Sales and purchase are ledger kinds, not
per-client tables. Currency is explicit on party transactions, cash accounts, cash
entries, journals and statement snapshots.

Financial services own atomic mutations. Party row locks serialize settlements and
corrections; transaction-scoped advisory locks serialize UUID retries across parties.
Procurement operations lock request then party to keep delivery decisions consistent
with cancellation and acceptance. Source uniqueness prevents duplicate imports.
Journal pairs have one positive amount on each side and PostgreSQL immutability guards.

Allocations are dated, immutable links; changing them creates dated release records.
Credits, refunds and reversals append transactions and balanced journals. Cash only
changes for actual payments/refunds/reversed payments. Sales invoices credit deferred
revenue; separate acceptance events transfer that value to income. Reconciliation
uses dated allocations/releases and stores the complete result as immutable JSON.
Reviews append staff attribution independently of the frozen statement.

Permissions use the existing Django-to-frontend formatter. Active staff and the
corresponding ledger view permission are required. Account, correction, acceptance,
procurement approval and statement review permissions are separately assignable.
User-approved self-approval is allowed with actor/time recorded.

Tax is excluded and will be designed as a separate ledger. Currency conversion,
inventory valuation, nominal-account configuration and year closing are not silently
introduced. January–December remains the financial year; account opening defaults
are zero. Migration history is retained and application migration execution is an
operator deployment action, not part of automated testing.
