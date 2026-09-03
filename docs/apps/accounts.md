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
| GET, POST | `/account/users/` | `UserViewSet`: list, create | IsAdminUser, DjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/account/users/{id}/` | `UserViewSet`: retrieve, update, partial_update, destroy | IsAdminUser, DjangoModelPermissions |
| GET, POST | `/account/profiles/` | `StaffProfileViewSet`: list, create | IsAdminUser, DjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/account/profiles/{id}/` | `StaffProfileViewSet`: retrieve, update, partial_update, destroy | IsAdminUser, DjangoModelPermissions |
| GET, POST | `/account/departments/` | `DepartmentViewSet`: list, create | IsAdminUser, DjangoModelPermissions |
| GET, PUT, PATCH, DELETE | `/account/departments/{id}/` | `DepartmentViewSet`: retrieve, update, partial_update, destroy | IsAdminUser, DjangoModelPermissions |
| GET, POST | `/account/department-leadership/` | `DepartmentLeadershipViewSet`: list, create | IsAuthenticated, CanManageDepartmentLeadership |
| GET, PUT, PATCH, DELETE | `/account/department-leadership/{id}/` | `DepartmentLeadershipViewSet`: retrieve, update, partial_update, destroy | IsAuthenticated, CanManageDepartmentLeadership |
| GET, POST | `/account/roles/` | `RoleViewSet`: list, create | IsAdminUser |
| GET, PUT, PATCH, DELETE | `/account/roles/{id}/` | `RoleViewSet`: retrieve, update, partial_update, destroy | IsAdminUser |
| GET | `/account/permissions/` | `PermissionViewSet`: list | IsAdminUser |
| GET | `/account/permissions/{id}/` | `PermissionViewSet`: retrieve | IsAdminUser |
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
- User/profile writes require IsAdminUser plus model permissions. update and
  destroy also restrict to superuser. partial_update calls through update; do not
  assume the privileged-field role check alone grants CEO/CTO PATCH access.
- Department leadership uses CanManageDepartmentLeadership; hierarchy validation
  lives in models/serializers. Role and permission routes use IsAdminUser.
- Audit/login lists require CanViewAuditLogs (including configured executive
  roles or native audit-view permission). Summary AND AI actions require
  IsSecurityExecutive, not merely permission to view audit rows.

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

Password reset currently calls Resend directly from accounts/utils.py. This is
an existing exception to centralized notification ownership, not permission for
new apps to duplicate delivery. Email sender/API configuration and FRONTEND_URL
are required; never log raw reset codes. Security AI/configured cache require
their provider/cache settings; implementation is not proof of live availability.

There are no registered /account/change-password/, /account/logout/ or dedicated
user activate/deactivate action routes. Next.js logout is separate.
Do not copy hypothetical role/department fields from the old handover.

## Verification

Use isolated local test settings and run manage.py test accounts --keepdb.
Review accounts/tests.py and accounts/test_hierarchy.py; cover permissions,
hierarchy cycles, reset expiry/reuse, neutral reset responses and audit events.
No email or AI requests were sent as part of this documentation review.
