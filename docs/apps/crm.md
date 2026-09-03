# CRM application

Status: implemented Party-centred design. Last reviewed: 2026-09-03.

## Purpose and model boundaries

CRM owns external identity and relationship master data. Party kinds are
ORGANISATION, INDIVIDUAL and TRADING_NAME; active roles can include CLIENT,
SUPPLIER, PROSPECT, LOGISTICS_PROVIDER, SERVICE_PROVIDER and OTHER.
One party can be both client and supplier. Person/organisation profiles,
affiliations and configurable contact roles keep human contacts separate from
organisations. Contacts, addresses, sources, notes, interactions, identifiers,
bank accounts, documents, status history and merge records attach to parties.

Sources: [models](../../crm/models.py), [views](../../crm/views.py),
[serializers](../../crm/serializers.py), [permissions](../../crm/permissions.py),
[services](../../crm/services.py), [integration helpers](../../crm/integrations.py),
[document service](../../crm/document_services.py), [filters](../../crm/filters.py).

## Registered endpoints

Verified from Django's URL resolver and allowed HTTP methods on 2026-09-03.
Paths are relative to the backend origin; `{id}` is a resource identifier.
HEAD/OPTIONS and router discovery/format-suffix variants are omitted.
Permission classes below include action-decorator overrides; object checks,
serializer validation and service rules can impose additional restrictions.

| Methods | Path | Handler / operation | Permission classes |
| --- | --- | --- | --- |
| GET, POST | `/crm/parties/` | `PartyViewSet`: list, create | PartyActionPermission |
| POST | `/crm/parties/duplicate-check/` | `PartyViewSet`: duplicate_check | PartyActionPermission |
| POST | `/crm/parties/quick-create/` | `PartyViewSet`: quick_create | PartyActionPermission |
| GET, PUT, PATCH, DELETE | `/crm/parties/{id}/` | `PartyViewSet`: retrieve, update, partial_update, destroy | PartyActionPermission |
| POST | `/crm/parties/{id}/archive/` | `PartyViewSet`: archive | PartyActionPermission |
| POST | `/crm/parties/{id}/block/` | `PartyViewSet`: block | PartyActionPermission |
| POST | `/crm/parties/{id}/deactivate/` | `PartyViewSet`: deactivate | PartyActionPermission |
| GET | `/crm/parties/{id}/history/` | `PartyViewSet`: history | PartyActionPermission |
| POST | `/crm/parties/{id}/merge/` | `PartyViewSet`: merge | PartyActionPermission |
| PATCH | `/crm/parties/{id}/profile/` | `PartyViewSet`: profile | PartyActionPermission |
| POST | `/crm/parties/{id}/reactivate/` | `PartyViewSet`: reactivate | PartyActionPermission |
| POST | `/crm/parties/{id}/restore/` | `PartyViewSet`: restore | PartyActionPermission |
| POST | `/crm/parties/{id}/suspend/` | `PartyViewSet`: suspend | PartyActionPermission |
| GET, POST | `/crm/party-roles/` | `PartyRoleViewSet`: list, create | StrictDjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/crm/party-roles/{id}/` | `PartyRoleViewSet`: retrieve, update, partial_update, destroy | StrictDjangoModelPermissions |
| GET, POST | `/crm/contact-methods/` | `ContactMethodViewSet`: list, create | StrictDjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/crm/contact-methods/{id}/` | `ContactMethodViewSet`: retrieve, update, partial_update, destroy | StrictDjangoModelPermissions |
| GET, POST | `/crm/addresses/` | `AddressViewSet`: list, create | StrictDjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/crm/addresses/{id}/` | `AddressViewSet`: retrieve, update, partial_update, destroy | StrictDjangoModelPermissions |
| GET, POST | `/crm/sources/` | `PartySourceViewSet`: list, create | StrictDjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/crm/sources/{id}/` | `PartySourceViewSet`: retrieve, update, partial_update, destroy | StrictDjangoModelPermissions |
| GET, POST | `/crm/contact-roles/` | `ContactRoleViewSet`: list, create | StrictDjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/crm/contact-roles/{id}/` | `ContactRoleViewSet`: retrieve, update, partial_update, destroy | StrictDjangoModelPermissions |
| GET, POST | `/crm/affiliations/` | `PartyAffiliationViewSet`: list, create | StrictDjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/crm/affiliations/{id}/` | `PartyAffiliationViewSet`: retrieve, update, partial_update, destroy | StrictDjangoModelPermissions |
| GET, POST | `/crm/notes/` | `PartyNoteViewSet`: list, create | StrictDjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/crm/notes/{id}/` | `PartyNoteViewSet`: retrieve, update, partial_update, destroy | StrictDjangoModelPermissions |
| GET, POST | `/crm/interactions/` | `PartyInteractionViewSet`: list, create | StrictDjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/crm/interactions/{id}/` | `PartyInteractionViewSet`: retrieve, update, partial_update, destroy | StrictDjangoModelPermissions |
| GET | `/crm/status-history/` | `PartyStatusHistoryViewSet`: list | StrictDjangoModelPermissions |
| GET | `/crm/status-history/{id}/` | `PartyStatusHistoryViewSet`: retrieve | StrictDjangoModelPermissions |
| GET | `/crm/merge-history/` | `PartyMergeRecordViewSet`: list | StrictDjangoModelPermissions |
| GET | `/crm/merge-history/{id}/` | `PartyMergeRecordViewSet`: retrieve | StrictDjangoModelPermissions |
| GET, POST | `/crm/identifiers/` | `PartyIdentifierViewSet`: list, create | SensitiveIdentifierPermission |
| GET, PUT, PATCH, DELETE | `/crm/identifiers/{id}/` | `PartyIdentifierViewSet`: retrieve, update, partial_update, destroy | SensitiveIdentifierPermission |
| GET | `/crm/identifiers/{id}/reveal/` | `PartyIdentifierViewSet`: reveal | SensitiveIdentifierPermission |
| GET, POST | `/crm/bank-accounts/` | `PartyBankAccountViewSet`: list, create | SensitiveBankAccountPermission |
| GET, PUT, PATCH, DELETE | `/crm/bank-accounts/{id}/` | `PartyBankAccountViewSet`: retrieve, update, partial_update, destroy | SensitiveBankAccountPermission |
| GET | `/crm/bank-accounts/{id}/reveal/` | `PartyBankAccountViewSet`: reveal | SensitiveBankAccountPermission |
| GET, POST | `/crm/documents/` | `PartyDocumentViewSet`: list, create | PartyDocumentPermission |
| GET, PUT, PATCH, DELETE | `/crm/documents/{id}/` | `PartyDocumentViewSet`: retrieve, update, partial_update, destroy | PartyDocumentPermission |
| GET | `/crm/documents/{id}/download/` | `PartyDocumentViewSet`: download | PartyDocumentPermission |

## Payload and response contracts

- PartyWriteSerializer handles create/update; PartyListSerializer and
  PartyDetailSerializer represent directory/detail data.
- Creation supports allowed nested profile, roles, contact methods, addresses and
  sources. Ordinary Party PUT/PATCH rejects nested related records; use dedicated
  endpoints after creation.
- Existing PATCH /crm/parties/{id}/profile/ updates the person profile for an
  individual or organisation profile for organisation/trading name. Payload
  validation uses the existing profile input serializers; it requires party edit
  permission. It is not a missing endpoint.
- PartyLifecycleSerializer requires a reason for lifecycle actions. Merge uses
  PartyMergeSerializer and protected service rules, not arbitrary foreign-key edits.
- PartyAffiliationSerializer governs person/organisation, job title, department,
  dates, current/primary-contact flags and contact_role_ids.
- ContactMethodSerializer, AddressSerializer and PartySourceSerializer govern
  their dedicated writes. PartyIdentifierSerializer and PartyBankAccountSerializer
  deliberately distinguish protected writes from masked reads/reveals.
- PartyDocumentUploadSerializer accepts multipart party and file, with category,
  description, confidentiality, verification status and expiry fields.
  Metadata updates use PartyDocumentUpdateSerializer; downloads return protected
  file content rather than public storage links.

Example profile update (illustrative fields, not live client data):

```http
PATCH /crm/parties/{id}/profile/
Content-Type: application/json

{"first_name": "Example", "last_name": "Contact"}
```

Example lifecycle request:

```json
{"reason": "Relationship temporarily inactive"}
```

## Permissions, lifecycle and data integrity

PartyActionPermission maps ordinary and lifecycle actions to explicit native
permissions; blocked/suspended reactivation additionally depends on block access.
Safe party deletion is superuser-only and rejects protected business references.
Do not generalize that policy to every related-resource DELETE.

Related records generally use StrictDjangoModelPermissions, including view access
for GET. Identifiers/bank accounts require extra sensitive management permissions
for writes and reveal permissions for plaintext. Documents require download
permission; confidential documents add view/manage permissions and queryset checks.

Normal sensitive reads remain masked. Reveals and important lifecycle/record
changes are audited. Merged/inactive records retain historical meaning;
integration helpers validate parties and client contacts for new business.
Manual interactions do not automatically capture WhatsApp/email conversations.

## Filtering, errors and integrations

Directory filtering/search/ordering is in PartyFilter and PartyViewSet.
Other resources use their own filters; lists follow standard pagination.
Expect field validation failures for invalid relationships/dates, duplicates or
unsupported nested updates, and authorization failures on sensitive operations.
Storage errors are translated by document services/views; do not expose provider
secrets or raw exceptions.

Private document storage adapters support Google Drive and Supabase. The source
default CRM_DOCUMENT_STORAGE_PROVIDER is supabase. Downloads/deletions use the
document's recorded storage_provider, preserving legacy Drive access; no automatic
fallback occurs. Supabase uploads/downloads require a verified private bucket.
See [storage operations](../operations/STORAGE.md). Default CRM_DOCUMENT_MAX_SIZE_BYTES is 20 MiB.
Allowed MIME types cover PDF, JPEG, PNG, TXT, DOC/DOCX and XLS/XLSX.
Configure encryption/hash keys and provider credentials server-side only.
Current Next.js Server Action body limit is 25mb, independently of backend/storage
limits. Preview/download must remain mediated and authorized.

CRM does not create invoices or ledger transactions. Future apps should reuse
validate_party_for_new_business and validate_client_contact where appropriate.
CRM notification integration is in [notifications.py](../../crm/notifications.py).

## Verification

Run manage.py test crm --keepdb with isolated local test settings.
Existing test modules cover models, API, integrations and sensitive documents.
Preserve regression coverage for nested-update rejection, profile PATCH,
affiliations, masking/reveal, confidential download, MIME/size enforcement,
duplicates, safe deletion and lifecycle/merge rules.
See also existing frontend guides under ../holansladmininterface/docs/crm-frontend
(relative to the backend repository root).
