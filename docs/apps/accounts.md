# Accounts application

Status: implemented in source. Last reviewed: 2026-09-03.

## Purpose and implementation

Owns UUID users, staff profiles, departments, reporting hierarchy and department
leadership, roles (Django Groups), permissions, password reset and security audit
records. Models include User, StaffProfile, Department, DepartmentLeadership,
AuditLog and PasswordResetCode. A Role is a Group proxy, not an independent
business-role table.

Sources: [models](../../accounts/models.py), [views](../../accounts/views.py),
[serializers](../../accounts/serializers.py), [permissions](../../accounts/permissions.py),
[utilities](../../accounts/utils.py), [audit services](../../accounts/audit_services.py).

## Registered endpoints

Verified from Django's URL resolver and allowed HTTP methods on 2026-09-03.
Paths are relative to the backend origin; `{id}` is a resource identifier.
HEAD/OPTIONS and router discovery/format-suffix variants are omitted.
Permission classes below include action-decorator overrides; object checks,
serializer validation and service rules can impose additional restrictions.

| Methods | Path | Handler / operation | Permission classes |
| --- | --- | --- | --- |
| GET, POST | `/account/users/` | `UserViewSet`: list, create | IsAdminUser, StrictDjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/account/users/{id}/` | `UserViewSet`: retrieve, update, partial_update, destroy | IsAdminUser, StrictDjangoModelPermissions |
| GET, POST | `/account/profiles/` | `StaffProfileViewSet`: list, create | IsAdminUser, StrictDjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/account/profiles/{id}/` | `StaffProfileViewSet`: retrieve, update, partial_update, destroy | IsAdminUser, StrictDjangoModelPermissions |
| GET, POST | `/account/departments/` | `DepartmentViewSet`: list, create | IsAdminUser, StrictDjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/account/departments/{id}/` | `DepartmentViewSet`: retrieve, update, partial_update, destroy | IsAdminUser, StrictDjangoModelPermissions |
| GET, POST | `/account/department-leadership/` | `DepartmentLeadershipViewSet`: list, create | IsAuthenticated, CanManageDepartmentLeadership |
| GET, PUT, PATCH, DELETE | `/account/department-leadership/{id}/` | `DepartmentLeadershipViewSet`: retrieve, update, partial_update, destroy | IsAuthenticated, CanManageDepartmentLeadership |
| GET, POST | `/account/roles/` | `RoleViewSet`: list, create | IsAdminUser, StrictDjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/account/roles/{id}/` | `RoleViewSet`: retrieve, update, partial_update, destroy | IsAdminUser, StrictDjangoModelPermissions |
| GET | `/account/permissions/` | `PermissionViewSet`: list | IsAdminUser, StrictDjangoModelPermissions |
| GET | `/account/permissions/{id}/` | `PermissionViewSet`: retrieve | IsAdminUser, StrictDjangoModelPermissions |
| GET | `/account/audit-logs/` | `AuditLogViewSet`: list | IsAuthenticated, CanViewAuditLogs |
| GET | `/account/audit-logs/ai-insight/` | `AuditLogViewSet`: ai_insight | IsAuthenticated, IsSecurityExecutive |
| GET | `/account/audit-logs/summary/` | `AuditLogViewSet`: summary | IsAuthenticated, IsSecurityExecutive |
| GET | `/account/audit-logs/{id}/` | `AuditLogViewSet`: retrieve | IsAuthenticated, CanViewAuditLogs |
| GET | `/account/login-activity/` | `LoginActivityViewSet`: list | IsAuthenticated, CanViewAuditLogs |
| GET | `/account/login-activity/ai-insight/` | `LoginActivityViewSet`: ai_insight | IsAuthenticated, IsSecurityExecutive |
| GET | `/account/login-activity/summary/` | `LoginActivityViewSet`: summary | IsAuthenticated, IsSecurityExecutive |
| GET | `/account/login-activity/{id}/` | `LoginActivityViewSet`: retrieve | IsAuthenticated, CanViewAuditLogs |
| GET | `/account/me/` | `CurrentUserView`: get | IsAuthenticated |
| POST | `/account/password-reset/verify/` | `PasswordResetVerifyView`: post | AllowAny |
| POST | `/account/password-reset/confirm/` | `PasswordResetConfirmView`: post | AllowAny |
| GET | `/account/health/` | `health_check`: get | AllowAny |
| POST | `/account/password-reset/request/` | `ForgotPasswordView`: post | AllowAny |
| POST | `/token/` | `HolanTokenObtainPairView`: post | Token endpoint (credentials/refresh token) |
| POST | `/token/refresh/` | `TokenRefreshView`: post | Token endpoint (credentials/refresh token) |

## Contracts and authorization

- Token obtain accepts username/password credentials through the configured
  email-or-username backend; successful response contains access and refresh JWTs.
  Refresh accepts {"refresh":"TOKEN"} and returns an access token.
- must_change_password blocks token login and triggers reset email flow; it is not
  the restricted authenticated change-password session described in old planning.
- Reset request accepts {"email":"staff@example.invalid"} with neutral eligibility
  wording. Verify accepts {"code":"RESET_CODE"}. Confirm accepts code, password and
  password_confirm. Expired/used/invalid codes and mismatched/weak passwords fail
  validation. See PasswordReset*Serializer for exact response/validation details.
- GET /account/me/ returns CurrentUserSerializer identity, profile, roles and
  formatted permissions. UserSummarySerializer and StaffProfileReadSerializer
  define list/detail contracts; UserWriteSerializer and StaffProfileWriteSerializer
  define writes. Do not reuse read-only response fields as write payloads.
- User/profile/department APIs require staff status and strict model permissions,
  including view permissions for reads. User/profile PUT and PATCH enforce the
  same rules; account status also requires change_user, role assignment change_role
  and possession of assigned permissions. Superuser accounts remain protected.
  Permanent user/profile deletion remains superuser-only.
- Department leadership uses CanManageDepartmentLeadership; hierarchy validation
  lives in models/serializers. Role/permission routes require staff status and
  strict model permissions (accounts.*_role and auth.view_permission respectively).
- Audit/login lists, summary and AI actions require accounts.view_auditlog, with
  a superuser bypass. IsSecurityExecutive is retained as a compatibility class
  name, but no longer checks role names.
- CEO/CTO receive new permissions through creation/migration hooks; explicit
  initial provisioning and operational limits are documented in
  [executive rollout](../operations/EXECUTIVE_PERMISSIONS.md).

## Filtering and examples

User filters include email, names, staff/active state and groups; profiles include
department, reporting manager, job title and user state. Audit and login filtering
differ; check each view's filterset_fields/search_fields/ordering_fields.
Summary/AI supports range=month|quarter|year and caches aggregate work by range.

```http
GET /account/login-activity/?user={id}&page=1&page_size=10&ordering=-created_at
GET /account/audit-logs/summary/?range=month
```

Summary/AI invalid ranges return 400; unavailable AI may return 503, provider
failure 502. Other common failures follow [shared conventions](API_CONVENTIONS.md).

## Integrations and known differences

Password reset and default-password login call notifications.services.send_sensitive_email
from accounts/utils.py; notifications owns Resend transport. Email sender/API
configuration, NOTIFICATION_EMAIL_ENABLED and FRONTEND_URL are required.
The service sends immediately and returns only safe acceptance metadata; reset
links never enter dashboard/WhatsApp, the outbox, audit metadata or API responses.
On failure, existing account handling invalidates the new reset record and returns
the existing error response. No background retry occurs; the user can request a
fresh link. Existing eligibility-neutral responses and routes are unchanged.
Security AI/configured cache require
their provider/cache settings; implementation is not proof of live availability.

There are no registered /account/change-password/, /account/logout/ or dedicated
user activate/deactivate action routes. Next.js logout is separate.
Do not copy hypothetical role/department fields from the old handover.

## Verification

Use isolated local test settings and run manage.py test accounts --keepdb.
Review accounts/tests.py and accounts/test_hierarchy.py; cover permissions,
hierarchy cycles, reset expiry/reuse, neutral reset responses and audit events.
No email or AI requests were sent as part of this documentation review.
