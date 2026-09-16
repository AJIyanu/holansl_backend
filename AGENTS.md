# HolanSL project and backend instructions

This is the primary instruction file for both HolanSL admin repositories.
Keep exactly one repository-root AGENTS.md in each repository; supporting Markdown
documents are standards/specifications, not additional agent entry points.

## Required reading

- Read [development rules](docs/DEVELOPMENT.md), [architecture](docs/ARCHITECTURE.md),
  [security](docs/SECURITY.md) and [logging rules](docs/DEVELOPMENT_LOGGING.md).
- For an app change, read its [application/API documentation](docs/apps/README.md).
- For procurement or ledger planning, read the relevant [design specification](docs/design/README.md).
- For frontend work, read [frontend AGENTS.md](../holansladmininterface/AGENTS.md)
  and the frontend standards it links. The frontend points back here for shared
  rules; after reading both entry points once, follow topic links, not a read loop.
- [TODO actions](docs/TODO.md) records deferred work. A TODO is not permission to implement it.

## Shared working rules

- Present a plan and wait for explicit user approval before editing files.
  Once approved, write files directly; no manual-copy code or Bash scaffolding is
  required. Stage-by-stage pauses are not mandatory within the approved scope.
- Implement backend before dependent frontend work.
- Inspect both repositories as needed, but modify only the repository authorized
  for the task unless the user explicitly permits changes to both.
- Preserve unrelated changes. Ask about material conflicts; do not turn an old
  example, assumption or document into a new business rule.
- Use client, not customer. A request means products a client wants HolanSL to
  supply. This is a Nigerian procurement company's staff-only dashboard, not the
  UK public marine website.
- Accounts, Party-based CRM, tasks and notifications are built. Preserve their
  actual routes/contracts. Procurement and ledger backends are built and migrated; consult their app docs
  for current contracts, configuration and frontend work.
- New features, feature changes and corrections must be recorded in the affected
  repository's Git-ignored DEVELOPMENT_LOG.local.md with timestamp, functions,
  reason when known and verification. Do not reset or commit local history.

## Backend-specific rules

- Follow Django/DRF, custom UUID users, service-layer and transaction conventions.
  Use settings.AUTH_USER_MODEL/get_user_model(), not the default Django User.
- Enforce permissions and object/queryset scope in Django. Preserve formatted
  frontend permissions through accounts.serializers.format_permission.
- Reuse accounts, CRM master data and notifications; avoid duplicate business
  records and circular model dependencies. Keep CRM nested-create versus
  dedicated-related-update contracts.
- Treat databases as production. Generate and inspect required migrations, but
  request approval before running them against application databases.
- Use Ruff. Write and run tests for backend behaviour changes using isolated local
  test settings; never fall back to online DATABASE_URL.
- Declare added Python dependencies in requirements.txt and report what/why.
- Keep app/API documentation current and report validation honestly.
- Password-reset delivery uses notifications' sensitive email-only service;
  never route reset links through persisted notifications or other channels.
- Supabase is the default for new documents; use recorded providers for existing
  documents and never silently fall back to Drive. Preserve private-bucket checks.
- CEO/CTO use assigned permissions, not role-name authorization. Grant newly
  created permissions additively; never restore revoked grants during seeding or
  routine synchronization. Existing-role baseline provisioning is an explicit
  operator action; see docs/operations/EXECUTIVE_PERMISSIONS.md and docs/TODO.md.
