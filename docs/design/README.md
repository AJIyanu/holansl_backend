# Domain design specifications

These are approved design directions, not implemented API contracts or permission
to start development. Current behaviour is in [app docs](../apps/README.md).

- [Procurement](PROCUREMENT.md): client requests through purchasing and delivery.
- [Ledger](LEDGER.md): double-entry foundation, flexible invoices, client withholding, tax records,
  advances, reconciliation and build strategy.
- [Ledger backend specification](LEDGER_BACKEND.md): approved backend design for models, accounting
  invariants, permissions, API contracts, integration changes and acceptance checks.
  Includes the client **Reconciliation and Statement** feature. Backend implementation
  is documented in [ledger app contracts](../apps/ledger.md); rollout is pending.

Shared ownership is in [architecture](../ARCHITECTURE.md). Resolve material open
decisions with the user before models, migrations or dependent frontend work.
Reuse existing accounts, CRM, tasks and notifications. Design backend contracts
before frontend work; integrate notifications with each relevant feature.
