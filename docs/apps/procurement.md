# Procurement

Status: Item Requests and Item Trackers implemented; Analytics pending design.

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
