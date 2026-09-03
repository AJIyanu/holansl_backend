# Private document storage operations

New uploads use CRM_DOCUMENT_STORAGE_PROVIDER, default `supabase`. An explicit
environment override remains supported. SUPABASE_STORAGE_URL, SUPABASE_STORAGE_SECRET_KEY
(legacy SUPABASE_STORAGE_SERVICE_ROLE_KEY alias), SUPABASE_STORAGE_BUCKET and timeout
settings configure the existing HTTP adapter; no new dependency is required.

The configured bucket was read-only checked on 2026-09-03: accessible and private.
No production file was uploaded, downloaded or deleted as part of that check.
Future configuration changes require revalidation; this is not ongoing monitoring.

Uploads and downloads check bucket metadata for explicit `public: false` before
accessing objects. Missing/invalid metadata and network failures stop the operation.
No bucket is created or made private automatically. Authentication headers remain
server-side, redirects are disabled and raw provider response bodies are excluded
from errors. Protected Django/Next.js download paths and confidential-record
permissions remain authoritative.

Existing records select their stored provider for download and deletion. Keep
Drive credentials while Drive records exist. New uploads do not fall back to Drive
on Supabase errors. A deliberate `google_drive` override is a rollback for future
uploads, not a migration of existing files. Restart workers after configuration
changes because provider instances are cached. Do not change the Supabase bucket
for existing records without a separate file-migration plan.

No schema or data migration accompanies this change. Existing validation, size
limits, checksum checks, upload compensation and soft-deleted metadata remain.
Provider failure and routing tests use mocks; live write acceptance needs separate
authorization. Tests: crm.test_storage_routing and crm.test_sensitive_documents.
