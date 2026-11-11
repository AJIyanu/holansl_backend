# Procurement redesign: requests and tracking

The first approved redesign scope consists of Item Requests and Item Trackers.
Analytics is navigation-only until separately designed.

One request groups items received from one CRM client, while every requested item
has an independent identity, lifecycle, images, status, and append-only activity
history. Items entered together may proceed, suspend, deliver, or close at
different times.

Client identity and relationships remain CRM-owned. Procurement stores immutable
display-name snapshots and its own three-letter client code without duplicating
the CRM party. Accounts supplies staff identity and permissions. No Ledger,
quotation, sourcing, purchase-order, delivery, invoice, task, or notification
dependency is introduced in this phase.

The tracker is an extensible activity stream. Later approved features attach
their records through reserved source fields rather than rewriting the basic item
request model.
