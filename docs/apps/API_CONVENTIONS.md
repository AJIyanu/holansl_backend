# Shared API conventions

Verified against local source on 2026-09-03.

## Base URLs and authentication

Backend namespaces are /account/, /crm/, /tasks/ and /notifications/.
There is no universal /api/ prefix on Django business endpoints.
Next.js /api/* paths are a different application layer.

Use the configured deployment origin; do not hard-code or publish environment
secrets. Authentication classes in [settings](../../holansladminservices/settings.py)
include SessionAuthentication, TokenAuthentication and JWTAuthentication.
Existing Next.js server helpers send both Authorization: Bearer TOKEN and
X-Auth-Token: Bearer TOKEN; [middleware](../../holansladminservices/middleware.py)
provides the fallback. Session-authenticated mutations also require CSRF handling.

Normal operational endpoints require authenticated access and app-specific
permissions. Public authentication/reset/health endpoints and the separate
secret-protected notification scheduler are documented exceptions. Do not infer
that every authenticated user is authorized to every resource.

## Permission representation

Django checks native codenames; the frontend gets formatted permissions from
[accounts/serializers.py](../../accounts/serializers.py):

| Django | Frontend |
| --- | --- |
| crm.add_party | crm.party.create |
| crm.change_party | crm.party.edit |
| accounts.view_auditlog | accounts.auditlog.view |
| crm.view_sensitive_partyidentifier | crm.sensitive_partyidentifier.view |

The app label is accounts even though the URL prefix is /account/.
Action-specific rules and queryset filtering remain authoritative. Do not
replace native backend permission checks with formatted frontend strings.

## Requests, responses and failures

Use trailing-slash routes. JSON is used unless an endpoint accepts multipart file
upload or streams a protected document. Dates/times follow serializer ISO formats.
A list using StandardPagination returns:

```json
{"count": 0, "next": null, "previous": null, "results": []}
```

Pagination uses page and page_size, default 20 and maximum 100.
Search, ordering and resource filters are opt-in per view/filter class; do not
assume all handover query parameters are implemented everywhere.

Typical statuses: 200 read/update/action, 201 create, 204 successful deletion,
400 invalid input, 401/403 authentication/authorization, 404 missing or inaccessible
record, 405 unsupported method. Authentication failures may be 403 because of
authentication-class ordering; clients must preserve existing refresh/error handling.
Deletion can be refused even when DELETE is registered.

DRF validation commonly returns field-keyed arrays or detail; some custom
endpoints add code. There is no one universal error envelope. Provider failures
and custom actions have additional documented statuses. Avoid exposing internal
exceptions or private provider responses in the UI.

## OpenAPI and source contracts

- GET /schema/ — generated schema.
- GET /api/schema/swagger-ui/ — Swagger UI.
- GET /api/schema/redoc/ — ReDoc.

Schema generation is configured by drf-spectacular. The checked-in schema.yaml may
lag code. Internal notification processing is hidden from schema by default.
Use generated serializers/schema plus these narrative documents for full field
definitions; route tables deliberately omit router roots and format suffixes.

## Safe verification

```bash
DJANGO_SETTINGS_MODULE=holansladminservices.test_settings ./venv/bin/python manage.py check
DJANGO_SETTINGS_MODULE=holansladminservices.test_settings ./venv/bin/python manage.py test --keepdb
```

Confirm the local disposable PostgreSQL test database before running tests.
Never fall back to online DATABASE_URL. Application migrations need user approval.
Mock email, storage and other outbound providers in tests; never send real
notifications to verify documentation.
