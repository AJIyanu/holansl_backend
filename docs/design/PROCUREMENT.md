# Procurement design specification

Status: procurement backend implemented and application migration applied; frontend pending.
Do not restore the removed legacy domain.

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

Implemented item transitions, partial quantities, rejection attempts and
idempotency are specified in the current application documentation. Remaining
decisions include approval thresholds, product/catalogue ownership, substitutions
outside rejected-item replacement, cancellation and corrective quantity adjustments,
and ledger request integration. Flexible invoice timing is agreed in the ledger
design; its posting and direct-order contracts still need implementation.
Use [ledger design](LEDGER.md) for advances and financial processing.

## Planned direct-invoice integration (2026-09-15)

The quotation-led flow above describes the current implementation. The agreed
ledger direction also permits a direct invoice before purchasing or delivery,
without a quotation. The [backend integration design](LEDGER_BACKEND.md#5-direct-invoices-and-document-ownership)
proposes explicit item/price snapshots on PO lines and ledger-owned invoice/order
links, preserving old quotation references. Payment can be evidence of client
approval, but the authorized quantities and partial-payment rule must be defined.
This section does not enable the direct path in current procurement endpoints.

## Item tracking and rejected-item attempts (2026-09-04)

Each requested item retains actor/date/reason/evidence history and separate
quantity progress. Rejected goods can be returned to the supplier, accepted or
refused by that supplier, replaced by the same supplier, sourced from a different
supplier, or closed at rejection. Supplier refunds may be partial or whole;
ledger owns their verification and accounting.

Replacement attempts stay under the original PO line and retain each rejected
attempt. They may cycle through sourcing, purchase, receipt and delivery again.
A genuine client reorder creates a new linked request/item instead. Never count
a replacement as another client order or overwrite rejected delivery evidence.

Backend contracts now live in [the application documentation](../apps/procurement.md).
Ledger integration and frontend implementation remain pending.

## Approved document policy (2026-09-04)

Use `Hol-[CLIENTCODE]-[QT/WB/IN/RC]-[YEAR]-[ANNUAL SERIAL]` with a stable
3–4 uppercase-letter client code. Quotations and invoices require trusted ledger
approval; receipts originate only in ledger. Waybills do not require ledger
approval. Sample-based layouts use configurable company contact details and
office address; obsolete sample addresses must never be defaults. Keep saved
files immutable when company details change. See the application documentation
for preparation, generation, private previews and current ledger placeholders.
