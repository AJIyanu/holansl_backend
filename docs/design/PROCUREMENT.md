# Procurement design specification

Status: not implemented. Do not restore the removed legacy domain.

## Ownership and workflow

Procurement owns client requests/items, supplier sourcing/quotations, client
quotations, independent revisions, markup/profit/discount/logistics estimates,
client purchase orders/lines, purchases, delivery notes, waybills and proof.
It submits invoice, expense/supplier/logistics cost, advance and breakdown requests
to finance; ledger approves and creates official financial records.

Client requests supply → source suppliers from CRM → record supplier prices →
prepare client quote → record verbal/documented client PO → purchase (advance
if needed) → record actual cost breakdown → deliver/prove delivery → request
invoice → finance approves → authorized staff export/send approved invoice.
Payment/receipt/allocation/reconciliation are ledger responsibilities.

## Identity and relationships

Reference CRM client/supplier parties and applicable individual contact parties.
Preserve historical organisation/contact identity when relationships change.
Do not create duplicate client/supplier tables.

Supplier quotations and client quotations are separate and independently versioned.
PO lines reference quotation items; do not attach a PO only to a quote header.
Support multiple quoted items per PO and one quoted item ordered through multiple
POs, partial/later purchase and final quantities above/below quoted quantity.
PO lines store final ordered quantity.

## Costs and delivery evidence

Preserve quotation estimates/revisions for expected-versus-actual analysis.
Approved actual costs replace estimates for actual financial reporting.
Quote lines may include description, quantity, price, discount, tax, logistics,
additional charges, validity and markup information.

Delivery notes/waybills contain no money values and are distinct from invoices.
Record delivered items/quantities, date, destination, receiver/status, proof and
relevant logistics/vehicle details. Invoice traceability should connect request,
quote item, PO line and delivery where practical.

## Open decisions

Exact state transitions, approvals/thresholds, product/catalogue ownership,
document numbering, substitutions, returns, cancellations, partial deliveries,
invoice timing, request-layer ownership and idempotency need confirmation.
Do not turn the narrative into an assumed state machine.
Use [ledger design](LEDGER.md) for advances and financial processing.
