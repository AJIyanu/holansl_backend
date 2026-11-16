# Procurement

Status: Item Requests, Item Trackers, Analytics, supplier quotes/prices, client
quotations, purchase orders, purchases, waybills and item verdicts are implemented.

`POST /procurement/item-requests/` registers one CRM client request containing one
or more items. The party must have an active CRM `CLIENT` role. The client is the
requester; Procurement does not duplicate that fact in separate requester fields.

Every item is independent and immediately receives a unique
`Item-CCC-yymmddhhmm-NN` code, its own open tracker, and an append-only `Request
received` system activity attributed to the staff user. `CCC` is an immutable
Procurement-owned three-letter client code. `NN` is allocated atomically in Lagos
calendar time, begins at `00`, and resets daily. Items submitted together consume
consecutive numbers and never share a tracker.

Optional sample images are uploaded per item through
`POST /procurement/items/{id}/images/`. Private Supabase storage accepts JPEG,
PNG, and WebP, with a limit of 5 pictures of 5 MB each per item. Staff may add
several pictures in one operation or remove selected pictures while the item is
open or suspended. Each operation appends one attributed activity with affected
picture IDs and filenames; delivered and closed items are locked. Additional
uploads append after existing pictures, including when only position zero remains.

Items begin `OPEN` and may become `SUSPENDED`, `DELIVERED`, or `CLOSED`. Only
delivered or closed items are complete. Suggested or custom activities and
comments record author and occurrence time. History is append-only and completed
items accept no new activity. Reserved source fields allow later approved
supplier quote, client quote, sourcing, purchase order, waybill, and invoice
features to attach records without redesigning the activity stream.

Open or suspended items may be edited through
`PATCH /procurement/items/{id}/edit/`. Each transaction stores only changed
properties and appends an attributed system activity containing readable field
names plus previous and new values. Unchanged submissions and edits to delivered
or closed items are rejected.

The API requires staff status plus model/action permissions. Custom permissions
are `view_itemtracker`, `add_itemactivity`, `change_itemstatus`, and
`download_itemimage`. New permissions are granted additively to CEO/CTO by the
existing post-migration mechanism. Existing non-executive roles require an
explicit administrator update; future default provisioning includes Procurement
for Procurement Officer and management baselines.

## Analytics

`GET /procurement/analytics/summary/?range=...` returns six period cards, the
current open-item count compared with the snapshot seven days earlier, current-
year monthly deliveries, selected-period client demand and current-year monthly
received-to-delivered ratios per client. Calendar week, month, quarter and year
ranges use Africa/Lagos time. Active means `OPEN` only; not delivered includes
open, suspended and closed items.

`GET /procurement/analytics/ai-insight/?range=...` sends aggregate, anonymised
operational figures to the configured Google AI provider. Rules and AI results use
separate cache entries with the same age/new-activity threshold invalidation model
as Accounts security summaries. AI failures do not prevent normal analytics.

## Supplier quotes and prices

`GET|POST /procurement/supplier-quotes/` lists or records a response from one active
CRM supplier. One response contains one or more distinct open/suspended requested
items, with quantity, unit price, optional 7.5%-default tax and a line note. The
same supplier may provide later responses for the same item. Staff record whether
the supplier response arrived by email, WhatsApp, call, physical copy, portal or a
described custom channel. An optional PDF/JPEG/PNG/WebP attachment is stored in
private Supabase storage, limited to 10 MB, and downloaded through
`GET /procurement/supplier-quotes/{id}/attachment/` with a dedicated permission.
`PATCH /procurement/supplier-quotes/{id}/` edits the header, line prices and optional
attachment with the model's change permission. Existing lines remain editable after
their tracker item has progressed; newly added lines must still be open/suspended.
Every affected item receives an attributed `SUPPLIER_QUOTE_EDITED` activity stating
the previous and current values. A replacement attachment is stored before the old
provider object is removed.

Every line appends an attributed `SUPPLIER_QUOTE_ADDED` item activity. Its source
is the supplier-quote line and metadata identifies the parent quote so the
dashboard can open the relevant detail page.

## Client quotations

`GET|POST /procurement/client-quotations/` lists or creates Procurement-only
quotations for an active CRM client. Selected items must be open/suspended;
items requested by other clients may also be included. References are transactionally allocated by Lagos calendar
date as `HOL-QTN-YYMMDD-SN`, with the serial resetting to `01` each day.

Each line snapshots its tracker item name, optional custom quotation name, quantity,
unit and price. A blank custom name defaults to the tracked item name. Optional VAT
defaults to 7.5%; discount may be absent, percentage
or amount. `POST /procurement/client-quotations/{id}/issue/` freezes an immutable
snapshot, renders the invoice-derived layout as a quotation PDF, stores it privately
and records attributed issue activities. `GET .../{id}/download/` downloads the
stored PDF; `GET .../{id}/print/` opens it inline; and `POST .../{id}/email/` sends
that exact PDF through the shared document email provider. Only successful file
retrieval or provider acceptance records the corresponding Download, Print or Email
communication and appends a detailed linked activity to every quotation item.

Quotation detail and action routes use the human-readable reference, for example
`/procurement/client-quotations/HOL-QTN-261113-01/`. Drafts may be edited in place.
Editing an issued quotation preserves its immutable snapshot/PDF as a numbered
revision and advances the quotation to the next Draft revision. Issued files are
named `{reference}-R{version}.pdf`. Removing the current document deletes only its
provider object, marks the revision as removed, retains snapshot/checksum/pricing,
and appends a detailed removal activity to every affected item.

System-generated tracker statements include the actor, workflow action, relevant
quantity and unit, unit price, subtotal, allocated discount, VAT, line total and
previous/current state where applicable. Structured metadata remains available for
links and exact historical values.

These models and services have no Ledger foreign key and do not post cash-book,
journal, invoice, payment or other accounting records.

## Purchasing

`GET|POST /procurement/purchase-orders/` lists and records client purchase orders.
Each order receives a Lagos-date reference `HOL-PO-YYMMDD-SN`; the daily serial
starts at `01`. A PO belongs to an active CRM client, but any open tracker item may
be included regardless of the client that originally requested it. Lines snapshot
quantity, unit and an optional price. Status begins `QUEUED` and may be explicitly
changed through `POST /procurement/purchase-orders/{id}/status/` to `ACTIVE`,
`FULFILLED`, `ATTENTION_REQUIRED` or `SUSPENDED`, with an optional reason. PO
creation and each status change append detailed activity to every linked item.
Optional PDF/image attachments are privately stored and permission-protected.
Attachments accept PDF, JPEG, PNG or WebP up to 5 MB. Status-change reasons are
optional; when omitted, the attributed old/new state remains in tracker history.

`GET|POST /procurement/purchases/` records one CRM party as the source, optional
PO linkage, supplier document reference, currency, logistics method/cost and one
or more tracked item quantities and prices. The source need not carry a supplier
role. Each line appends a priced, attributed purchase activity. This operational
record does not create a Ledger or cash-book posting.
Recording a purchase linked to a non-active, unfulfilled PO atomically changes that
PO to Active and appends the transition to each purchased item's tracker.
Each purchase line begins `PAID` and may be changed through the purchase item-status
action to `TRANSIT`, `RECEIVED`, `RETURNED` or `REFUNDED`. Each change records the
actor, old/new state, quantity, value and optional reason in the item tracker.

## Waybills and item verdicts

`GET|POST /procurement/waybills/` generates and permanently records waybills with
references `HOL-WYB-YYMMDD-SN`. Lines snapshot item description, quantity and unit.
When no description is submitted, the latest saved client-quotation item name is
used, then the tracked item name if no quotation exists.
The immutable issue snapshot also stores the sample template's delivery personnel,
delivered-to party, address, logistics, driver/vehicle, signatory, received time and
notes. The generated PDF uses the existing Holan document header and private
Supabase storage. Download, print and email actions have dedicated routes and append
communication activities to each item.
Waybills may link to an unfulfilled Holan PO. The PDF displays Delivered to once,
prints both operations WhatsApp numbers and formats quantities without trailing
decimal zeros.

`GET|POST /procurement/item-verdicts/` records `ACCEPTED`, `REJECTED`, `RETURNED`
or `DISPOSED` quantities against a specific waybill line. For each line, cumulative
accepted plus rejected quantity cannot exceed the waybilled quantity. Cumulative
returned plus disposed quantity cannot exceed rejected quantity. Rejection, return
and disposal require reasons; returns also require the receiving CRM supplier, and
disposal requires a method and authorising staff member. Verdicts are append-only
business history and append a complete attributed tracker activity.
Any accepted verdict atomically marks the tracker item Delivered with the reason
`Item was Accepted`. A linked PO becomes Fulfilled
once accepted quantities satisfy every PO line. A rejected verdict atomically
moves the linked PO to Attention required. Fulfilled-PO waybill lines cannot accept
new verdicts and fulfilled POs are excluded from new operational selections.
