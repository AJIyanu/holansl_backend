"""Authorization, audit, decimal arithmetic and scoped idempotent operations."""

import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
from zoneinfo import ZoneInfo
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import (
    APIException,
    PermissionDenied,
    NotFound,
    ValidationError,
)
from ledger import models as m


class Conflict(APIException):
    status_code = 409
    default_code = "ledger_conflict"
    default_detail = (
        "The record changed or this operation conflicts with financial history."
    )


class Unconfigured(APIException):
    status_code = 503
    default_code = "policy_unconfigured"
    default_detail = "Configure the required ledger policy before this operation."


def check(condition, message):
    if not condition:
        raise ValidationError(message)


def require(user, permission):
    if (
        not user
        or not user.is_authenticated
        or not user.is_active
        or not (user.is_staff or user.is_superuser)
    ):
        raise PermissionDenied("Active staff access is required.")
    if not user.has_perm("ledger." + permission):
        raise PermissionDenied("Required ledger permission is missing.")


def stamp(user):
    return dict(
        created_by=user,
        created_by_name=(user.get_full_name() or user.username) if user else "System",
    )


def make(model, user, **values):
    return model.objects.create(**stamp(user), **values)


def primitive(value):
    if isinstance(value, dict):
        return {str(k): primitive(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [primitive(v) for v in value]
    if hasattr(value, "pk"):
        return str(value.pk)
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    return str(value)


def digest(value):
    return hashlib.sha256(
        json.dumps(primitive(value), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def amount(value, currency=None):
    try:
        result = Decimal(str(value))
        check(result.is_finite(), "Amount must be finite.")
        places = currency.minor_units if currency else 4
        result = result.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        check(abs(result) < Decimal("1e20"), "Amount is too large.")
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("A valid decimal amount is required.") from None


def business_date(value):
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            raise ValidationError("Use an ISO date.") from None
    check(
        isinstance(value, date)
        and value <= timezone.localdate(timezone=ZoneInfo("Africa/Lagos")),
        "A nonfuture accounting date is required.",
    )
    return value


def visible_records(user):
    require(user, "view_financialrecord")
    qs = m.FinancialRecord.objects.all()
    if user.has_perm("ledger.viewall_financialrecord"):
        return qs
    q = Q(created_by=user)
    profile = getattr(user, "profile", None)
    if (
        profile
        and profile.department_id
        and user.has_perm("ledger.viewdepartment_financialrecord")
    ):
        q |= Q(department_id=profile.department_id)
    return qs.filter(q)


def get_record(user, pk, *, lock=False, action="view"):
    qs = visible_records(user)
    if lock:
        qs = qs.select_for_update()
    try:
        obj = qs.get(pk=getattr(pk, "pk", pk))
    except (m.FinancialRecord.DoesNotExist, ValueError):
        raise NotFound() from None
    require(user, action + "_" + m.RESOURCE[obj.kind])
    return obj


def full_scope(user, permission):
    require(user, permission)
    require(user, "viewall_financialrecord")


def accounts_department():
    from accounts.models import Department

    configured = getattr(settings, "LEDGER_ACCOUNTS_DEPARTMENT_ID", None)
    qs = (
        Department.objects.filter(pk=configured)
        if configured
        else Department.objects.filter(name__iexact="Accounts")
    )
    department = qs.first()
    if department is None:
        raise Unconfigured(
            "Configure the existing Accounts department (LEDGER_ACCOUNTS_DEPARTMENT_ID)."
        )
    return department


def event(user, action, record=None, reason="", **metadata):
    from accounts.models import AuditLog

    entry = make(
        m.LedgerEvent,
        user,
        record=record,
        action=action,
        reason=reason,
        metadata=primitive(metadata),
    )
    AuditLog.objects.create(
        user=user,
        event_category="CRUD",
        event_type="UPDATE",
        status="SUCCESS",
        app_label="ledger",
        resource="financialrecord",
        action=action,
        object_id=str(record.pk) if record else "",
        metadata={"ledger_event": str(entry.id)},
    )
    return entry


def evidence(user, doc, party=None, record=None):
    require(user, "view_ledgerdocument")
    if not isinstance(doc, m.LedgerDocument):
        try:
            doc = m.LedgerDocument.objects.get(pk=doc)
        except m.LedgerDocument.DoesNotExist:
            raise NotFound() from None
    if doc.record_id:
        get_record(user, doc.record_id)
    elif doc.created_by_id != user.pk and not user.has_perm(
        "ledger.viewall_financialrecord"
    ):
        raise NotFound()
    check(
        not party or doc.party_id in {None, party.pk},
        "Evidence belongs to another party.",
    )
    check(
        not record or doc.record_id in {None, record.pk},
        "Evidence belongs to another financial record.",
    )
    return doc


@transaction.atomic
def operation(user, key, method, path, payload, callback):
    import uuid

    require(user, "view_financialrecord")
    try:
        key = uuid.UUID(str(key))
    except (ValueError, TypeError):
        raise ValidationError(
            {"Idempotency-Key": "A UUID header is required."}
        ) from None
    fingerprint = digest(
        {"actor": user.pk, "method": method, "path": path, "payload": payload}
    )
    op, created = m.LedgerOperation.objects.get_or_create(
        key=key, defaults={**stamp(user), "fingerprint": fingerprint}
    )
    if not created:
        if op.fingerprint != fingerprint or op.created_by_id != user.pk:
            raise Conflict("Idempotency key belongs to a different operation.")
        for pk in op.result.get("records", []):
            get_record(user, pk)
        return op.result["body"]
    body, records = callback()
    op.result = {
        "body": primitive(body),
        "records": [str(getattr(r, "pk", r)) for r in records],
    }
    op.save(update_fields=["result"])
    return op.result["body"]


def version(obj, expected):
    if obj.version != expected:
        raise Conflict("Stale version. Reload the record before retrying.")


def client_account(user, party, currency, lock=False):
    obj, _ = m.ClientAccount.objects.get_or_create(
        party=party, currency=currency, defaults=stamp(user)
    )
    if lock:
        obj = m.ClientAccount.objects.select_for_update().get(pk=obj.pk)
    return obj
