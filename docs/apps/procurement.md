# Procurement application

Status: backend implemented; approved application migration applied and verified
on 2026-09-04. Frontend remains pending. Ledger integration was added on 2026-09-15 and its migrations were applied on 2026-09-15.

## Ownership

Procurement tracks each client-requested item through sourcing, independently
versioned supplier/client quotations, client PO lines, supplier purchases, Holan
receipts, deliveries, acceptance/rejection, and linked client reorders. Financial
requests and refund evidence are drafts/reports only; ledger remains responsible
for official invoices, receipts, payment allocations, refunds and accounting.

CRM owns clients, suppliers and receiving contacts. References use CRM validation
helpers and protected relationships. Names are snapshotted on requests, supplier
records and receiving records. Staff actors and actor-name snapshots accompany
business records and tracking events; automatic expiry events identify System.

## Data model

All records use UUIDs. Main relationships:

- `ProcurementRequest` → `RequestItem`: client, contact, owner, department,
  received date; item description/specification, quantity, unit and required date.
- `SupplierQuotation` → `SupplierQuotationLine`: supplier, currency, validity,
  optional evidence and previous revision; item/optional replacement attempt,
  quantity, unit price and charges. Revisions preserve earlier offers.
- `ClientQuotation` → `ClientQuotationRevision` → `ClientQuotationLine`: stable
  quotation identity, sequential immutable revisions, reason/validity/document;
  item, selected offer, Item Description on Quote, quantity, quoted unit price,
  fixed/percentage discount, tax rate (7.5% form/API default), logistics and
  additional charges. The stored calculated discount/tax amounts make revisions
  reproducible. Supplier/estimated cost is not collected or exposed by this
  client-quotation contract and never enters client document snapshots.
- `QuotationIssue`: revision, receiving contact, method, evidence and actual sent
  time. Recording a send does not send an email.
- `ClientPurchaseOrder` → `ClientPurchaseOrderLine`: client reference and verbal
  confirmation/document; exact quotation line, final ordered quantity and price.
  Multiple PO lines can reference the same quote item; ordered quantity may
  differ from quoted quantity. Price changes require a quote revision.
- `FulfilmentAttempt`: item, PO line, target quantity, original/replacement
  strategy and rejected outcome being replaced. Replacements do not increase
  the original ordered quantity.
- `SupplierPurchase` → `SupplierPurchaseLine`: supplier/currency/reference;
  attempt, supplier offer, quantity and purchase price. Several suppliers can
  fulfil one original attempt.
- `GoodsReceipt` → `GoodsReceiptLine`: purchase, receiving date/evidence;
  purchase-line quantities physically received by Holan.
- `Delivery` → `DeliveryLine`: destination, receiving contact, waybill document,
  logistics details, dispatch date; goods-receipt-line quantities.
- `DeliveryOutcome` → `DeliveryOutcomeLine`: actual client receiver and snapshot,
  time/signed waybill; accepted/rejected quantities and rejection reason.
- `RejectionAction`: returned quantity, supplier response, replacement strategy,
  closure or reported refund and evidence. A replacement action links its newly
  created attempt.
- `ProcurementDocument`: request-scoped private file, category, checksum,
  original filename, uploader and recorded storage provider. Storage keys are
  never serialized to API clients.
- `ProcurementCost`, `FinanceRequest`/`FinanceRequestLine`: unapproved expenditure
  and drafts linked to items and optionally PO/delivery lines. No posting API.
- `ProcurementTaskLink`: connects an existing accessible task to an item.
- `ItemTrackingEvent`: append-only business history with actor/time/reason,
  quantity, attempt and explicit source-record/document references.
- `Operation`: internal idempotency fingerprint/result, not exposed as an API.

No old procurement models or URLs are restored. Database UUIDs remain internal
identifiers. Staff responses include human display labels and stable six-character
display references; official documents use the approved Holan numbering below.

## Workflow and rejection resolution

Submit request → source supplier offers → create client quotation/revisions →
record quotation sent → record PO lines → purchase → receive at Holan → prepare
waybill delivery → dispatch → record client acceptance/rejection.

Optional quotation review, reissue and expiry events preserve their revisions.
Reissue is a new revision followed by `record-sent`; there is no separate
single-call reissue endpoint. Issued quotation revisions cannot be edited.
Expired quotation handling does not cancel existing PO commitments.

For rejection use `POST /procurement/outcome-lines/{id}/resolve/` with `kind`,
`quantity`, mandatory `reason` and optional document:

| Kind | Validation and result |
| --- | --- |
| `RETURNED_TO_SUPPLIER` | Total returned cannot exceed rejected quantity. |
| `SUPPLIER_ACCEPTED_RETURN` | Accepted/refused return quantities together cannot exceed returned quantity. |
| `SUPPLIER_REFUSED_RETURN` | Records refusal; another supplier may still be chosen. |
| `SAME_SUPPLIER_REPLACEMENT` | Requires sufficient accepted-return quantity; creates a replacement attempt under the same PO line. |
| `DIFFERENT_SUPPLIER_CHOSEN` | Opens sourcing under a replacement attempt; its purchase must use a different supplier from the rejected delivery. A return is not required. |
| `REFUND_REPORTED` | Requires positive `refund_amount`, `currency` and document; supports partial/whole reported refunds. Does not verify or post money. |
| `CLOSED_REJECTED` | Closes the specified rejected quantity without a replacement. |

Replacement plus closed quantity cannot exceed the rejected quantity. Returns
and refund reports are separate supplier-side facts, so a refund can be recorded
alongside closure or replacement. Replacement goods can themselves be rejected
and replaced again. Each attempt retains its own purchases, receipts, deliveries
and outcomes. A client reorder instead creates a new submitted request/item for
the same client linked through `previous_item`; it does not reset the old cycle
or require the earlier order to be paid.

## Quantity, evidence and transaction rules

Positive quantities and nonnegative prices/amounts are validated, with database
constraints on line records. Decimal precision is four places. A quotation discount
is either an amount or a percentage up to 100%. Tax is a percentage up to 100%,
defaulting to 7.5% for new lines, calculated on item subtotal after discount and
before logistics/additional charges. Staff may set zero or another applicable rate.
Supplier offer/quote/purchase currencies must match where an offer is selected.

Per-attempt purchases cannot exceed target quantity; goods receipts cannot exceed
purchases. Draft deliveries reserve received quantities. Outcomes cannot exceed
dispatched quantities, including earlier outcomes. Accepted quantities require a
signed waybill. Rejected quantities require a nonblank reason. The actual receiver
must be a CRM contact of the client (an individual client may receive directly).

Occurrence dates cannot be in the future; sending precedes PO receipt, receipt
follows recorded purchase, and acceptance/rejection follows dispatch. Cross-request
items, evidence and unrelated line references are rejected. New client/supplier
selections must satisfy CRM active-role rules.

Services lock the parent request inside a transaction, validate remaining
quantities and write business records/events together. API writes require a UUID
`Idempotency-Key` header. Identical retries return the stored result; reuse for a
different payload/path/user returns 409. Failed operations roll back their key.
Current request visibility is checked before returning a cached result.

There is no public arbitrary status PATCH or history DELETE. Request PATCH only
edits draft notes with `expected_version`. Other business records use explicit
create/action APIs; lifecycle reversal/correction workflows require a separate
agreed business policy and must not be simulated by editing history.

## API inventory

All paths below are relative to `/procurement/`. Lists use StandardPagination.
See [serializers](../../procurement/serializers.py) for exact nested input fields.
Create responses return the created business record with nested lines; quotation
creation returns its first revision. Detail GETs are available for each resource
below except capabilities. Generic update/delete operations are not exposed.

| Methods | Path | Purpose |
| --- | --- | --- |
| GET, POST | `requests/` | Directory / create draft with nonempty nested items. |
| GET, PATCH | `requests/{id}/` | Read / edit draft notes with expected version. |
| POST | `requests/{id}/submit/` | Create request-received events. |
| GET | `items/` | Item directory. |
| GET | `items/{id}/tracker/` | Milestones, attempt quantities, recent history and reorder links. |
| GET | `items/{id}/available-actions/` | Permission/readiness hints; writes still validate all details. |
| POST | `items/{id}/reorder/` | Quantity, reason and optional required date. |
| GET, POST | `supplier-quotations/` | Supplier quotation and nested item offers. |
| POST | `supplier-quotations/{id}/revise/` | Full replacement revision with reason, same supplier. |
| GET, POST | `quotations/` | Client quotations; POST creates first revision. |
| POST | `quotations/{id}/revise/` | Create next revision with mandatory reason. |
| GET | `quotation-revisions/` | Revision detail/list with lines and issue history. |
| POST | `quotation-revisions/{id}/record-sent/` | Recipient, method, evidence and optional occurrence date. |
| GET, POST | `purchase-orders/` | Client POs with final quantities and quote-line references. |
| GET, POST | `supplier-purchases/` | Supplier purchases against attempts/offers. |
| GET, POST | `goods-receipts/` | Physical receipts against purchase lines. |
| GET, POST | `deliveries/` | Prepare delivery and reserve received quantities. |
| POST | `deliveries/{id}/dispatch/` | Record dispatch and waybill milestones. |
| POST | `deliveries/{id}/outcomes/` | Actual receiver, accepted/rejected quantities and evidence. |
| GET | `delivery-outcomes/`, `outcome-lines/` | Read delivery results and rejected quantities. |
| POST | `outcome-lines/{id}/resolve/` | Record supplier response, replacement, refund report or closure. |
| GET | `rejection-actions/`, `attempts/`, `events/` | Resolution records, attempts and paginated event history. |
| GET, POST | `documents/` | Safe metadata / multipart private upload. |
| GET | `documents/{id}/download/` | Authorized attachment stream, private/no-store. |
| GET, POST | `costs/` | Record unapproved actual costs and evidence. |
| GET, POST | `finance-requests/` | Prepare financial drafts with nested lines. |
| POST | `finance-requests/{id}/submit/` | Create/reuse a ledger finance submission snapshot; no journal or money movement. |
| GET, POST | `task-links/` | Link existing tasks accessible to the staff member. |
| GET | `capabilities/` | Ledger unavailability and implemented capabilities. |

Common filters: `request`, `client`, `owner` UUIDs. `item` filters item-owned
resources and items; `kind` filters events/actions/costs/finance drafts. Search is
supported for request number/client name and item description. Unsupported
business-state filters are not advertised as implemented.

The tracker returns up to 100 recent events and `event_count`/`history_url` for the
full paginated history. It distinguishes original ordered quantity from replacement
quantities; cumulative accepted/closed quantities do not double-count rejected
attempts. Historical milestones are labelled RECORDED, not falsely interpreted
as the current state of every quantity. Attempt summaries show current fulfilment
state; invoice/payment milestones show INTEGRATION_UNAVAILABLE.

## Access and integrations

Every endpoint requires active authenticated staff (or superuser),
`view_procurementrequest` and the resource's native `view_*` permission for reads.
Writes require `operate_procurementrequest` plus the resource's `add_*` or
`change_*` permission. Reorder requires `add_procurementrequest`; delivery outcomes
require `add_deliveryoutcome`; rejection resolution requires
`resolve_procurementrequest`. Download additionally requires
`download_procurementdocument`.

Default scope is owned requests. `view_department_procurementrequest` adds the
staff member's department; `view_all_procurementrequest` adds all requests and
permits assigning another owner. Role names do not bypass checks. New permissions
use the existing additive executive provisioning hooks; revoked grants are not
restored. Inaccessible related records/documents remain inaccessible on retries.

Uploads explicitly select private Supabase. Downloads use each document's recorded
provider, with no fallback. Existing CRM MIME/size validation and storage privacy
checks are reused. API document metadata never includes storage identifiers.
Quotes, waybills and signed waybills are uploaded evidence in this backend version;
company-branded automatic PDF generation is not claimed as implemented.

Rejection, closure and quotation expiry create dashboard notifications for an
active, authorized request owner other than the actor. No external email or
WhatsApp is sent by procurement operations. Existing tasks can be linked; no
parallel assignment/reminder system is created.

Schedule `expire_procurement_quotations` through the deployment operator when the
migration is deployed. `--dry-run` only reports due revisions. Read endpoints do
not mutate expiry state; they also calculate expired validity from the timestamp.

## Migration and verification

`0003_procurement_item_lifecycle` creates new procurement tables, indexes and
constraints; it does not restore legacy models or rewrite existing business data.
Applied to the configured application database with explicit user approval on
2026-09-04; all 27 tables and 113 procurement permissions were verified. CEO and
CTO each hold those 113 permissions through the existing provisioning hooks.
For other environments, applying it requires approval. Inspect the target's migration plan
first: pending old removal migrations must never be approved implicitly.

Backend tests cover complete/partial fulfilment, several suppliers, rejection
and repeated replacements, refusal/refund/closure, reorders, quantities, evidence,
receiver validation, expiry, access scope, private storage, idempotency and
concurrent purchase capacity. Use only `holansladminservices.test_settings` and
the disposable local PostgreSQL test database. Storage is mocked; live delivery
or document upload was not tested. See the ignored local development log for
exact commands and results.

### Numbered documents and supplied layouts

Document preparation uses `POST /procurement/document-versions/` with a request,
kind `QT` and quotation revision, or kind `WB` and delivery. Assign a stable
3–4 uppercase-letter client code through the request's `client-code` action.
Numbers are `Hol-[CLIENTCODE]-[QT/WB/IN/RC]-[YEAR]-[SERIAL]`; the serial is shared
across clients and types for each Lagos calendar year. Preparation retries reuse
the source document identity. Migration `0004_professional_document_registry`
adds the register, immutable source versions, assets and annual sequence.

`POST /procurement/document-versions/{id}/generate/` accepts `format: PDF|JPG`
and `draft: boolean` (default true). The supplied quotation, waybill and invoice
samples define the logo placement, navy table headers, totals and signature areas.
PDFs include all pages; JPG exports save one file per page. Descriptions wrap and
table headers repeat. Snapshots retain discount type/value and calculated amount plus tax rate and
calculated amount. Existing pre-0005 lines retain their historical absolute values.

Set `HOLAN_DOCUMENT_OFFICE_ADDRESS` to the current office address. There is no
fallback to either obsolete sample address. Optional contact configuration:
`HOLAN_DOCUMENT_WEBSITE`, `HOLAN_DOCUMENT_EMAIL`, `HOLAN_DOCUMENT_PHONE`.
Drafts visibly identify themselves and show an unconfigured address notice when
needed. Official generation requires a configured address. Configuration is
fingerprinted into asset versions so address changes never overwrite old files.
This configuration is server-managed, not a client-writable approval field.

Generated assets use the existing verified private Supabase provider under
client/year/type/number/revision paths. Filenames retain the assigned number,
revision, draft marker, template identity and JPG page. Repeating generation with
the same version, format, draft state and configuration reuses saved assets.
`GET /procurement/documents/{id}/download/?preview=true` displays generated assets
inline with the same scope and download permission; ordinary uploads remain
attachments. All responses are private/no-store.

Quotes require ledger approval for official generation and recording as sent.
The request-approval action creates a ledger approval request. Official generation
and sending require the recorded approval of that exact quotation revision.
Invoices and receipts are created and generated through ledger; procurement
register/asset queries exclude those finance-owned document kinds. Waybills do not require
ledger approval; a generated official PDF attaches to a prepared delivery when
it has no waybill. Existing delivery evidence is retained.

Rendering dependencies: ReportLab creates PDFs, bundled DejaVu fonts support the
naira glyph, pypdfium2 converts PDF pages to JPG, and Pillow encodes JPG images.
Provider tests use mocks and never upload live client data.

Generation also accepts `show_logistics` and `show_additional_charges` (both
false by default). Each controls its printed totals-section row independently,
for both PDF and JPG. These are presentation options only: recorded charges and
the final total do not change. Options are included in the asset fingerprint;
changing them generates a separate saved variant without overwriting old files.

### Registration ownership and journey details

New requests always belong to the active staff member creating them and the
existing Procurement department. The request-create API no longer accepts owner
or department fields; the service sets these values authoritatively. Department
resolution uses the existing Procurement record (the deployed code is HOLPR),
with the standard PROC code as fallback. Missing configuration produces a clear
validation error rather than silently assigning another department.

REQUEST_RECEIVED is recorded for every item on request registration. Submission
still activates the operational workflow, but does not duplicate that receipt
event. Historical registered drafts without the event appear as received using
their retained registration actor/time; read operations do not rewrite audit data.
Milestones now include event_count and latest_event, with operational context
such as supplier, quotation revision, purchase reference, recipient and destination.

`GET /procurement/events/?item={id}&milestone={code}` returns paginated history
for that milestone, including related purchase/sourcing/rejection resolution
activity. Existing event permissions and request scope remain enforced. This
supports full milestone popups beyond the tracker's latest-100 event summary.

### Draft item updates and submission review

`PATCH /procurement/items/{id}/` updates draft description, specification,
quantity, unit and required_date. Supply description/quantity/unit and the
parent request's `expected_version`, plus the usual Idempotency-Key header.
It requires change_requestitem, operational access and request scope. Updates
lock the parent request, reject stale versions or submitted requests, increment
the parent version and record DRAFT_ITEM_UPDATED with before/after values.

The submit action accepts an optional expected_version for compatibility with
existing clients; the in-page review UI supplies it to prevent submitting a stale
summary. Submission is a distinct primary panel on request details; related-record
actions are grouped separately. Item cards offer a prefilled edit modal for drafts.

Submission currently acts as the immutable operational handoff. The rule protects
supplier offers, quote revisions, PO quantities and delivery/accounting traceability.
It is a workflow choice, not a technical requirement that every submitted request
remain forever unchangeable. Controlled amendments or audited edits before any
downstream dependency would need an explicit workflow; neither is silently enabled
by the new draft-edit endpoint.

### Ledger integration — 2026-09-15

The [ledger API](ledger.md) now owns quotation decisions, finance submissions,
posted invoices, receipts, allocations, tax and acceptance-based revenue recognition.
PO line input accepts exactly one of `quote_line` or `invoice_line` (ledger line UUID);
existing quotation orders remain compatible. Direct invoice orders retain explicit
item, description, price, quantity and client approval evidence. Item finance tracking
now reports invoice references, outstanding amounts, cash allocation and sent state.
Procurement reports of supplier refunds remain evidence; verified ledger receipts
post actual money. Application migrations were approved and applied on 2026-09-15; frontend work remains pending.
