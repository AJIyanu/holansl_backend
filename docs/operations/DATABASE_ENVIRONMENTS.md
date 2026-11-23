# Database environments and fresh bootstrap

The backend uses three explicit PostgreSQL environments. There is no SQLite
fallback and no environment may silently inherit another environment's database.

## Production (Supabase)

Render must set `APP_ENV=production` and `DATABASE_URL` to the Supabase PostgreSQL
connection. `build.sh` applies packaged migrations, creates the database cache table
when Redis is absent, provisions idempotent baseline records and the configured
initial superuser, then verifies that the Supabase document bucket exists and is
private.

This is an **empty-database bootstrap**, not a reset command. It makes a previously
emptied Supabase project usable and remains safe on later deploys. It never drops
tables, truncates records, recreates the database, creates a storage bucket or
changes bucket visibility. Emptying the production database is a separate,
destructive operator action requiring a verified backup, exact project selection
and explicit approval immediately before execution.

Required Render variables include `APP_ENV`, `DATABASE_URL`, `DJANGO_SECRET_KEY`,
the `DJANGO_SU_*` initial administrator values, and `SUPABASE_STORAGE_URL`,
`SUPABASE_STORAGE_SECRET_KEY` and `SUPABASE_STORAGE_BUCKET`. The bucket must be
created as private in Supabase before the build can pass.

Production also requires `AUTH_CONTEXT_SIGNING_KEY`. Configure the exact same
high-entropy value in Render and the Vercel frontend, for example a separately
generated 32-byte random secret. It is a server-only secret and must never use a
`NEXT_PUBLIC_` name. Optional `SECURITY_ALERT_EMAILS` is a comma-separated list of
internal recipients for account-attack and session-risk alerts.

## Local development

Development must use a separate local PostgreSQL database:

```bash
export APP_ENV=development
export LOCAL_DEV_DATABASE_URL=postgresql://USER:PASSWORD@localhost:5432/holan_dev
./scripts/setup_dev_database.sh
python manage.py runserver
```

The setup script creates the named database only when absent, applies migrations
and provisions the same baseline records as production. It refuses remote hosts,
the test database name and non-development environments. It does not erase or
replace an existing development database.

Tests continue to use `holansladminservices.test_settings` and
`LOCAL_TEST_DATABASE_URL`, normally `holan_test`. Settings reject a development
URL whose database name matches the test database.

## Production-like development data

No current production CSV snapshot exists in this repository. The old
`crm_rebuild_backups` archives are historical CRM-operation files, not a complete
production database export, and must not be loaded into development.

A future production snapshot must be sanitized before local use. Because arbitrary
CSV files do not encode table order, foreign keys, sequences or migration state,
an importer must be designed against the actual exported manifest and columns.
Do not point development at production and do not load an unreviewed CSV directory.
Prefer a sanitized PostgreSQL custom-format snapshot when the export process is
defined; add its restore command only after inspecting and documenting that format.
