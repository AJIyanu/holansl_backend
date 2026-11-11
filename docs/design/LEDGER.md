# Ledger rebuild — feature 1

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
- Procurement invoice imports and source links were retired on 2026-11-10. No
  linked Ledger records existed in the application database at retirement.

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

The user approved a basic cash book: receipts/debits, expense payments/credits,
automatic double entry, existing project permissions, January–December financial
years and a zero initial opening balance. NGN remains the primary currency.

This feature uses a single NGN cash book, fixed Cash/Income/Expenses/Capital
accounts and an immutable paired journal. Corrections and deletion are reversals
with reasons and staff attribution. No previous ledger feature is reinstated
implicitly. Build subsequent features individually after user agreement.

See [the current API and behaviour](../apps/ledger.md). The older comprehensive
ledger design remains retired; historic migrations exist only for upgrade safety.

## Party ledger stage 1

Approved sales/purchase workspaces use separate navigation and view permissions,
search existing CRM clients/suppliers, and remember the last selected party per
staff member and ledger. Selection storage and minimal lookups are delivered.
Transactions, posting, monetary summaries and reconciliation printing are reserved
for subsequent stages. See the API document for current paths and permissions.

## Approved completion — 2026-11-05

The user approved the remaining non-tax party-ledger stages together: advance
allocation/correction, credit/refund/reversal, full dated reconciliation, printable
saved snapshots/reviews and separate NGN/USD/GBP cash/bank accounts. The former
Procurement bridge is retired. See [current contracts](../apps/ledger.md). Tax is
explicitly deferred to a separate ledger; prior VAT/WHT plans are not enabled.
