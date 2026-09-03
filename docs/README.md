# HolanSL engineering documentation

Start at the repository-root [AGENTS.md](../AGENTS.md).

| Document                                        | Purpose                                                         |
| ----------------------------------------------- | --------------------------------------------------------------- |
| [Development](DEVELOPMENT.md)                   | Approval, implementation, tests and database safety             |
| [Architecture](ARCHITECTURE.md)                 | Project identity, stack, domain ownership and contracts         |
| [Security](SECURITY.md)                         | Authentication, permissions, sensitive data and target policies |
| [Design specifications](design/README.md)       | Procurement and ledger requirements, not implemented APIs       |
| [Application/API documentation](apps/README.md) | Current application contracts                                   |
| [Development logging](DEVELOPMENT_LOGGING.md)   | Local feature inventory and chronological change history        |
| [TODO actions](TODO.md)                         | Explicitly deferred implementation and open decisions           |

Frontend standards live in the [frontend docs](../../holansladmininterface/docs/README.md).
Operational rollout: [private storage](operations/STORAGE.md) and
[executive permission provisioning](operations/EXECUTIVE_PERMISSIONS.md).
Documents describe either current behaviour or approved target requirements;
never treat a target requirement as an implemented feature.
