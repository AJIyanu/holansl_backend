# HolanSL CRM Developer Reference

## CRM source files

### `crm/models.py`

Defines the persistent CRM domain.

Important models:

* `Party`
* `PartyRole`
* `OrganisationProfile`
* `PersonProfile`
* `PartyAffiliation`
* `ContactRole`
* `AffiliationContactRole`
* `ContactMethod`
* `Address`
* `PartySource`
* `PartyNote`
* `PartyInteraction`
* `PartyStatusHistory`
* `PartyMergeRecord`
* `PartyIdentifier`
* `PartyBankAccount`
* `PartyDocument`

### `crm/normalizers.py`

Contains text and contact-data normalisation.

Important functions:

* `normalize_text`
* `normalize_party_name`
* `normalize_email`
* `normalize_phone`
* `normalize_url`
* `normalize_contact_value`

### `crm/crypto.py`

Contains sensitive-field encryption and keyed lookup helpers.

Important functions:

* `get_sensitive_cipher`
* `encrypt_sensitive_value`
* `decrypt_sensitive_value`
* `normalize_sensitive_value`
* `hash_sensitive_value`
* `sensitive_value_last_four`
* `mask_sensitive_value`

### `crm/services.py`

Contains transactional CRM business operations.

Important functions:

* `sanitize_audit_metadata`
* `log_crm_event`
* `find_party_duplicates`
* `change_party_status`
* `set_party_archive_state`
* `get_external_party_references`
* `delete_unused_party`
* `merge_parties`

### `crm/serializers.py`

Defines CRM request validation and response structures.

Important serializers:

* `PartyListSerializer`
* `PartyDetailSerializer`
* `PartyWriteSerializer`
* `QuickSupplierCreateSerializer`
* `DuplicateCheckSerializer`
* `PartyAffiliationSerializer`
* `PartyNoteSerializer`
* `PartyInteractionSerializer`
* `PartyIdentifierSerializer`
* `PartyBankAccountSerializer`
* `PartyDocumentSerializer`
* `PartyDocumentUploadSerializer`

### `crm/views.py`

Contains the CRM REST API viewsets.

Important classes:

* `PartyViewSet`
* `PartyRoleViewSet`
* `ContactMethodViewSet`
* `AddressViewSet`
* `PartySourceViewSet`
* `ContactRoleViewSet`
* `PartyAffiliationViewSet`
* `PartyNoteViewSet`
* `PartyInteractionViewSet`
* `PartyIdentifierViewSet`
* `PartyBankAccountViewSet`
* `PartyDocumentViewSet`

### `crm/filters.py`

Contains server-side directory and activity filters.

Important classes:

* `PartyFilter`
* `PartyNoteFilter`
* `PartyInteractionFilter`

### `crm/permissions.py`

Contains CRM action and sensitive-resource permissions.

Important classes:

* `PartyActionPermission`
* `SensitiveIdentifierPermission`
* `SensitiveBankAccountPermission`
* `PartyDocumentPermission`

### `crm/document_services.py`

Coordinates file validation, external upload, download and deletion.

Important functions:

* `sanitize_document_filename`
* `calculate_document_checksum`
* `validate_document_upload`
* `upload_party_document`
* `download_party_document`
* `delete_party_document`

### `crm/storage/base.py`

Defines the storage-provider interface.

Important classes:

* `DocumentStorageBackend`
* `StoredDocument`
* `DownloadedDocument`
* `DocumentStorageError`

### `crm/storage/google_drive.py`

Implements Google Drive document storage.

Important class:

* `GoogleDriveDocumentStorage`

### `crm/storage/supabase.py`

Implements Supabase Storage.

Important class:

* `SupabaseDocumentStorage`

### `crm/storage/factory.py`

Returns the configured storage backend.

Important functions:

* `get_document_storage`
* `clear_document_storage_cache`

### `crm/notifications.py`

Connects CRM to the shared Notifications application.

Important functions:

* `users_with_permission`
* `dispatch_bank_account_change`
* `schedule_document_expiry_notification`
* `cancel_document_expiry_notification`

### `crm/management/commands/verify_crm_integrity.py`

Checks that:

* Procurement has no registered models.
* Ledger has no registered models.
* Old Procurement and Ledger tables are gone.
* `Party.name` is gone.
* `Party.party_type` is gone.
* `ContactPerson` is gone.

Run:

```bash
python manage.py verify_crm_integrity
```

## Empty application placeholders

### `procurement/`

Procurement currently has no models, serializers, views, URLs, signals or admin registrations.

Its migration history is retained for the database reset and future redesign.

### `ledger/`

Ledger currently has no models, serializers, views, URLs or admin registrations.

Its migration history is retained for the database reset and future redesign.

## Adding future application dependencies

A future application may reference `crm.Party`, but CRM should not import that application.

The preferred dependency direction is:

```text
Other application -> CRM
```

Avoid:

```text
CRM -> Other application
```

Generic notifications and audit records may reference CRM objects without introducing model imports.

## Adding a Party field

1. Update `crm/models.py`.
2. Update the relevant serializers.
3. Update filters and search where appropriate.
4. Update Django admin.
5. Create a migration.
6. Add tests.
7. Update this reference.

## Adding a storage provider

1. Implement `DocumentStorageBackend`.
2. Register the backend in `crm/storage/factory.py`.
3. Add provider settings.
4. Add mocked tests.
5. Update the application guide.

## Adding a new CRM action

1. Add the permission to the model.
2. Add transactional logic to `crm/services.py`.
3. Map the permission in `crm/permissions.py`.
4. Add the viewset action.
5. Add audit logging.
6. Add API and service tests.

---