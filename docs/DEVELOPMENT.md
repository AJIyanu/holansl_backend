# Development standards

## Approval and scope

Present a plan and wait for user approval before file edits. After approval,
implement directly in the workspace; no copyable-code delivery or Bash
scaffolding is needed. Work can complete in one task without artificial stages.
Ask again when scope materially changes or a design conflict needs a decision.

Backend implementation precedes dependent frontend implementation. Either
repository can be inspected, but modifying both requires explicit permission.
Do not overwrite unrelated changes or replace functioning domains with older
designs. Do not perform live business actions, deploy, send real emails, purge
data or apply migrations merely to test a feature.

## Code and contracts

Use existing framework versions and project conventions rather than automatically
upgrading to “latest.” Follow services, serializers, validators, permissions,
querysets, filters, pagination and transaction boundaries. Avoid N+1 queries.
Keep domain rules in Django; frontend validation improves UX but does not replace it.
Audit important changes, not every UI click. Update affected app/API docs.

Preserve implemented API compatibility unless the approved task requires a change.
Old procurement/ledger contracts are not binding; redesigned contracts still need
agreement before dependent frontend work.

## Dependencies and formatting

Dependencies may be added for a clear benefit within the approved task. Prefer
existing libraries/framework functionality where adequate. Declare Python
dependencies in requirements.txt; frontend packages in package.json and pnpm-lock.yaml.
Report every added/upgraded dependency and its purpose.

Use Ruff for Python linting/formatting and Prettier for Next.js. Avoid unrelated
whole-repository formatting. Use the installed project toolchain; if missing,
report that rather than pretending a check passed.

Formatting commands (from the repository root):

```bash
./venv/bin/ruff format .
./venv/bin/ruff format --check .
./venv/bin/ruff check .
```

Ruff is pinned in requirements.txt and configured in ruff.toml for Python 3.12.
Migration history, dependencies, collected static files and backups are excluded.
Formatting does not apply lint fixes; address behaviour-affecting lint findings
in a separately approved task. Whole-repository formatting requires explicit scope.

## Database safety

Treat all application databases as production, despite development status.
A future launch reset is not authority to erase data now or automatically later.
Generate and inspect required migrations; obtain approval before executing them.
Explain destructive operations, data transformations, lock risks and reversibility.
Do not rewrite/squash/remove migration history without approval.
Prefer staged, reversible changes preserving current records.

## Tests

Write/update and run tests for backend behaviour changes, including permissions,
validation, regressions, sensitive operations and failure paths. Mock outbound
providers and never test with live staff/client recipients.

Use isolated local PostgreSQL settings for both focused and full suites:

```bash
DJANGO_SETTINGS_MODULE=holansladminservices.test_settings ./venv/bin/python manage.py check
DJANGO_SETTINGS_MODULE=holansladminservices.test_settings ./venv/bin/python manage.py test crm --keepdb
DJANGO_SETTINGS_MODULE=holansladminservices.test_settings ./venv/bin/python manage.py test --keepdb
```

Replace crm with the affected app for focused tests. Confirm LOCAL_TEST_DATABASE_URL
points to a disposable local test database. Never inherit the normal online
DATABASE_URL. Test-runner setup of that isolated database does not authorize
migrating an application database. Run the broadest practical suite.

Documentation-only changes need link/contract/format validation, not invented
application tests. Frontend commands and theme checks are documented in the
frontend development guide.

## Completion

Update the affected repository's ignored feature log for additions, changes and
corrections, using [the logging standard](DEVELOPMENT_LOGGING.md). State behaviour
changed, files, migrations, dependency changes, exact checks/results, limitations
and required companion-repository work. Never report a proposal as delivered.
