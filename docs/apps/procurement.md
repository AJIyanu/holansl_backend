# Procurement

Status: Item Requests and Item Trackers implemented; Analytics pending design.

`POST /procurement/item-requests/` registers one CRM client request containing one
or more items. The party must have an active CRM `CLIENT` role. The requester may
be the client itself (omit `requester`) or a current CRM contact affiliated with
that client.

Every item is independent and immediately receives a unique
`Item-CCC-yymmddhhmm-NN` code, its own open tracker, and an append-only `Request
received` system activity attributed to the staff user. `CCC` is an immutable
Procurement-owned three-letter client code. `NN` is allocated atomically in Lagos
calendar time, begins at `00`, and resets daily. Items submitted together consume
consecutive numbers and never share a tracker.

Optional sample images are uploaded per item through
`POST /procurement/items/{id}/images/`. Private Supabase storage accepts JPEG,
PNG, and WebP, with a limit of 10 images of 10 MB each per item.

Items begin `OPEN` and may become `SUSPENDED`, `DELIVERED`, or `CLOSED`. Only
delivered or closed items are complete. Suggested or custom activities and
comments record author and occurrence time. History is append-only and completed
items accept no new activity. Reserved source fields allow later approved
supplier quote, client quote, sourcing, purchase order, waybill, and invoice
features to attach records without redesigning the activity stream.

The API requires staff status plus model/action permissions. Custom permissions
are `view_itemtracker`, `add_itemactivity`, `change_itemstatus`, and
`download_itemimage`. New permissions are granted additively to CEO/CTO by the
existing post-migration mechanism. Existing non-executive roles require an
explicit administrator update; future default provisioning includes Procurement
for Procurement Officer and management baselines.

Analytics remains under development until its measures are separately approved.
