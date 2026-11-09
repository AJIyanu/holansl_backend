# Domain design specifications

These are approved design directions, not implemented API contracts or permission
to start development. Current behaviour is in [app docs](../apps/README.md).

- [Procurement](PROCUREMENT.md): client requests through purchasing and delivery.
- [Tax Ledger](TAX_LEDGER.md): VAT/WHT recognition, approvals, CIT offsets and historical rollout.
- [Ledger rebuild](LEDGER.md): basic cash book; previous designs retired.
- [Cash-book backend](LEDGER_BACKEND.md): journal, permissions and migration boundary.

Shared ownership is in [architecture](../ARCHITECTURE.md). Resolve material open
decisions with the user before models, migrations or dependent frontend work.
Reuse existing accounts, CRM, tasks and notifications. Design backend contracts
before frontend work; integrate notifications with each relevant feature.
