# Ledger backend design

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
