# Domain design specifications

These are approved design directions, not implemented API contracts or permission
to start development. Current behaviour is in [app docs](../apps/README.md).

- [Procurement](PROCUREMENT.md): client requests through purchasing and delivery.
- [Ledger](LEDGER.md): official money records, advances and reconciliation.

Shared ownership is in [architecture](../ARCHITECTURE.md). Resolve material open
decisions with the user before models, migrations or dependent frontend work.
Reuse existing accounts, CRM, tasks and notifications. Design backend contracts
before frontend work; integrate notifications with each relevant feature.
