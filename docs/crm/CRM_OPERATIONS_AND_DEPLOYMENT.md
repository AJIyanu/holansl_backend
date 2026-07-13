# HolanSL CRM Operations and Deployment Guide

## Purpose

This guide covers migration order, configuration, document storage, sensitive-data keys, integrity checks and routine operational procedures for the CRM backend.

## Required migration order

The migration dependency graph enforces this order:

1. CRM foundation and operational migrations.
2. CRM sensitive-document migration.
3. Procurement CRM Party cutover.
4. Ledger CRM Party cutover.
5. Removal of the CRM compatibility bridge.

Inspect before applying:

```bash
python manage.py migrate --plan
```

Apply:

```bash
python manage.py migrate
```

Verify:

```bash
python manage.py showmigrations crm procurement ledger
python manage.py verify_crm_integrity
```

## Before production migration

Take a PostgreSQL or Supabase database backup.

The Stage 1 ZIP backup is useful for application-level inspection but is not a replacement for a complete database backup.

Run:

```bash
python manage.py prepare_crm_rebuild --dry-run
python manage.py prepare_crm_rebuild --backup-only
```

Do not use destructive purge mode after the final cutover. Protected Procurement and Ledger relationships are expected to prevent it.

## Required sensitive-data settings

```env
CRM_FIELD_ENCRYPTION_KEYS=<FERNET_KEY>
CRM_SENSITIVE_HASH_KEY=<LONG_RANDOM_SECRET>
```

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Generate a lookup-hash secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Never commit either key.

## Encryption-key rotation

`CRM_FIELD_ENCRYPTION_KEYS` accepts a comma-separated list.

The first key encrypts new values. Remaining keys allow old values to be decrypted.

Example:

```env
CRM_FIELD_ENCRYPTION_KEYS=<NEW_KEY>,<OLD_KEY>
```

Do not remove the old key until every old ciphertext has been re-encrypted.

Changing `CRM_SENSITIVE_HASH_KEY` invalidates deterministic lookup hashes. It must not be rotated without a controlled data migration.

## Google Drive configuration

Recommended settings:

```env
CRM_DOCUMENT_STORAGE_PROVIDER=google_drive
GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
GOOGLE_DRIVE_ROOT_FOLDER_ID=<ROOT_FOLDER_ID>
```

Use:

* A dedicated Google Cloud project.
* The Google Drive API.
* A service account.
* An organisation-controlled Shared Drive where available.
* A CRM-specific root folder.
* No public link-sharing permission.

The service account must have sufficient access to create folders, upload, download and delete files beneath the configured root.

## Supabase Storage fallback

```env
CRM_DOCUMENT_STORAGE_PROVIDER=supabase
SUPABASE_STORAGE_URL=https://PROJECT_REF.supabase.co
SUPABASE_STORAGE_SERVICE_ROLE_KEY=<SERVICE_ROLE_KEY>
SUPABASE_STORAGE_BUCKET=crm-documents
```

The bucket must be private.

The service-role key belongs only in the backend environment and must never be sent to the browser.

## Document restrictions

Configure allowed MIME types and maximum size:

```env
CRM_DOCUMENT_MAX_SIZE_BYTES=20971520
CRM_DOCUMENT_ALLOWED_MIME_TYPES=application/pdf,image/jpeg,image/png,text/plain,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
```

## Notifications

```env
CRM_NOTIFICATION_CHANNELS=DASHBOARD
CRM_DOCUMENT_EXPIRY_NOTICE_DAYS=30
CRM_NOTIFICATION_ACTION_URL=/dashboard/crm/parties
```

External channels such as email require the existing Notifications application provider settings.

## Deployment verification

Run:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py migrate
python manage.py verify_crm_integrity
python manage.py spectacular --file schema.yml --validate
```

Run tests:

```bash
python manage.py test \
  accounts.test_prepare_crm_rebuild \
  crm.test_models \
  crm.test_api \
  crm.test_sensitive_documents \
  crm.test_integrations \
  procurement.test_crm_integration \
  ledger.test_crm_integration \
  -v 2
```

## Integrity command

Human-readable output:

```bash
python manage.py verify_crm_integrity
```

JSON output:

```bash
python manage.py verify_crm_integrity --json
```

The command checks:

* Removal of legacy Party fields.
* Removal of `ContactPerson`.
* Procurement role consistency.
* Procurement contact identity.
* External references to merged Parties.
* Encrypted sensitive-record state.
* Active document metadata.

## Safe deletion policy

Use deactivation or archival for ordinary records.

Permanent Party deletion is allowed only when:

* The user is a superuser.
* The user has the required permission.
* The Party has no protected Procurement or Ledger references.
* The Party is not required by CRM history.

## Duplicate merging

Before merging:

1. Review both records.
2. Select the most complete record as the target.
3. Confirm that entity kinds are compatible.
4. Provide a clear merge reason.

The merge service:

* Locks both records.
* Moves CRM child records.
* Reassigns Procurement references.
* Reassigns Ledger references.
* Creates immutable merge history.
* Converts the source record into a merged tombstone.

Run the integrity command after bulk merges.

## Troubleshooting

### A Party cannot be deleted

This is normally correct. Check Procurement, Ledger, interaction, status-history or document references. Archive or deactivate the Party instead.

### A supplier cannot be selected

Confirm:

* Party status is `ACTIVE`.
* Party is not archived.
* Party has an active `SUPPLIER` role.
* Party has not been merged.

### A client contact is rejected

Confirm:

* The contact is an `INDIVIDUAL` Party.
* The contact has a current affiliation with the client organisation.
* The contact has not been merged.

### Sensitive values cannot be decrypted

Confirm that the key used to encrypt the value is still present in `CRM_FIELD_ENCRYPTION_KEYS`.

Do not overwrite or regenerate keys in production without a migration plan.

### Google Drive upload fails

Check:

* Drive API is enabled.
* Service-account JSON is valid.
* Root folder ID is correct.
* Service account has access to the root or Shared Drive.
* Render environment preserves the complete JSON secret.
* File size and MIME type are permitted.

### Supabase upload fails

Check:

* Storage URL.
* Service-role key.
* Private bucket name.
* Bucket file-size and MIME restrictions.
* Backend network access.

## Post-deployment monitoring

Monitor:

* Audit-log failures.
* Document-provider failures.
* Notification-delivery failures.
* Integrity-command failures.
* Duplicate creation patterns.
* Permission-denied patterns for sensitive endpoints.

---

# 27. Final migration and verification commands

```bash
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py migrate
python manage.py verify_crm_integrity
python manage.py check
```

Run the full CRM-related test suite:

```bash
python manage.py test \
  accounts.test_prepare_crm_rebuild \
  crm.test_models \
  crm.test_api \
  crm.test_sensitive_documents \
  crm.test_integrations \
  procurement.test_crm_integration \
  ledger.test_crm_integration \
  -v 2
```

Validate OpenAPI:

```bash
python manage.py spectacular \
  --file schema.yml \
  --validate
```

Search for remaining legacy references:

```bash
grep -RIn \
  --exclude-dir=migrations \
  --exclude-dir=.git \
  -E 'ContactPerson|party_type|party\.name|supplierquote_set|expectations' \
  crm procurement ledger
```

After the final cutover, that command should return no application-code references to:

* `ContactPerson`
* `party_type`
* `party.name`
* `supplierquote_set`
* `Transaction.expectations`

The CRM backend is then ready for frontend implementation.