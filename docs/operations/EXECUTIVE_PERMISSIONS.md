# Executive permissions: provisioning and rollout

CEO and CTO are ordinary Django groups (Role is a Group proxy). Membership alone
does not authorize an operation. Users require assigned permissions; superusers
retain their permission bypass without group membership. Staff-only API gates and
data-integrity/deletion protections remain in force.

## Initial baseline — explicit operator action

Preview with `python manage.py bootstrap_executive_permissions`. It reports the
current permission count without changing groups. After approval, add `--apply`
to create missing executive roles and add every current permission to both.
Use `--database` only for the intended database alias. The command is transactional
and additive: it does not remove existing grants or alter other groups.

The approved baseline was applied to the configured application database on
2026-09-04. CEO and CTO each moved from 126 to all 191 current permissions;
other group assignments were verified unchanged. No migration was run.
For another environment, arrange its approved baseline before deploying the
removal of role-name bypasses. Record prior assignments before applying a baseline
if rollback is needed; rolling back code does not undo grants.

Never schedule `--apply` as synchronization: explicitly rerunning it restores all
current permissions, including intentionally removed ones.

## Grant-only lifecycle

- New CEO/CTO Group or Role objects receive all permissions available at creation.
- Permission post_save grants only genuinely created permissions to existing
  executive groups. Updating a permission does not regrant it.
- Django creates model permissions with bulk_create during post_migrate, so
  pre_migrate snapshots permission IDs and post_migrate grants only new IDs.
  No role-membership reconciliation occurs; no-op migrations preserve removals.
- The normal default-account seeder leaves existing executive grants alone.
- Custom runtime bulk_create/fixtures bypass post_save: use ordinary Permission
  creation, or explicitly pass only newly created IDs to grant_new_permissions.
  New migration-defined permissions are covered by the migration hooks.
- Do not write database records from AppConfig.ready(); it only registers hooks.
- Django permission caches on already-loaded User objects follow standard Django
  semantics; fetch a fresh user on the next request after changing assignments.

## API enforcement

Account resource reads require their view permission; writes use add/change/delete.
User/profile PUT and PATCH follow the same checks. Role assignments require
accounts.change_role plus the granted permissions; account-status edits require
accounts.change_user. A non-superuser cannot modify a superuser account or grant
permissions they do not hold. Existing permanent user/profile/leadership deletion
remains superuser-only. Role CRUD uses accounts.*_role; permission listing uses
auth.view_permission. Audit/security summaries and AI use accounts.view_auditlog.

Frontend request/response shapes and permission formatting are unchanged. UI
controls for delegated role assignment should reflect the additional change_role
check in a separately authorized frontend task; Django always enforces it.

Tests cover baseline preview/apply, creation, bulk migration handling, revoked
permissions, re-seeding, role-free superusers, PUT/PATCH, security reads and
privilege escalation. Use the isolated local test settings; do not run migration
commands against an application database without prior approval.
