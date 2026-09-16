"""Stable document numbering, snapshots and fail-closed ledger integration."""

import hashlib
import json
from zoneinfo import ZoneInfo

from django.db import transaction, IntegrityError
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied

from . import models as m, services as s


class IntegrationUnavailable(APIException):
    status_code = 503
    default_code = "ledger_unavailable"
    default_detail = "Ledger approval is not yet available. The document can be prepared but cannot be officially issued."


def require_ledger_approval(revision):
    from ledger.integrations.procurement import require_quotation_approval

    return require_quotation_approval(revision)


def permission(user, codename):
    if not user.has_perm("procurement." + codename):
        raise PermissionDenied()


@transaction.atomic
def set_client_code(user, request, code):
    request = s.lock_request(request, user, submitted=False)
    permission(user, "manage_clientdocumentcode")
    # Serialize two requests assigning numbering identity to the same CRM client.
    from crm.models import Party

    Party.objects.select_for_update().get(pk=request.client_id)
    existing = m.ClientDocumentCode.objects.filter(client=request.client).first()
    if existing:
        s.check(
            existing.code == code,
            "This client already has a stable document code. It cannot be renamed here.",
        )
        return request
    s.check(
        not m.ClientDocumentCode.objects.filter(code=code).exists(),
        "That client code is already in use.",
    )
    # The annual sequence lock serializes simultaneous client-code assignments too.
    m.DocumentSequence.objects.get_or_create(
        year=timezone.now().astimezone(ZoneInfo("Africa/Lagos")).year
    )
    # Unique database constraint is the final guard for cross-client races.
    try:
        with transaction.atomic():
            s.make(m.ClientDocumentCode, user, client=request.client, code=code)
    except IntegrityError:
        s.check(False, "That client code is already in use.")
    return request


def allocate_number(client, kind):
    profile = m.ClientDocumentCode.objects.filter(client=client).first()
    s.check(
        profile is not None,
        "Assign the client's three- or four-letter document code first.",
    )
    year = timezone.now().astimezone(ZoneInfo("Africa/Lagos")).year
    m.DocumentSequence.objects.get_or_create(year=year)
    sequence = m.DocumentSequence.objects.select_for_update().get(year=year)
    sequence.last_serial += 1
    sequence.save(update_fields=["last_serial"])
    return {
        "client_code": profile.code,
        "year": year,
        "serial": sequence.last_serial,
        "number": f"Hol-{profile.code}-{kind}-{year}-{sequence.last_serial:05d}",
    }


@transaction.atomic
def prepare(user, data):
    from .serializers import READ

    request = s.lock_request(data["request"], user)
    kind = data["kind"]
    s.check(kind in {"QT", "WB"}, "Invoices and receipts must originate from ledger.")
    revision, delivery = data.get("quotation_revision"), data.get("delivery")
    if kind == "QT":
        s.check(
            revision is not None and delivery is None,
            "Select exactly one quotation revision.",
        )
        s.same_request(request, revision.quotation.request)
        source = {"quotation": revision.quotation}
        revision_number = revision.number
        snapshot = {
            "currency": revision.currency,
            "valid_until": revision.expires_at.isoformat()
            if revision.expires_at
            else None,
            "lines": [
                {
                    k: row[k]
                    for k in (
                        "description",
                        "quantity",
                        "unit_price",
                        "discount_type",
                        "discount_value",
                        "discount",
                        "tax_rate",
                        "tax",
                        "logistics",
                        "additional_charges",
                    )
                }
                for row in READ[m.ClientQuotationLine](
                    revision.lines.all(), many=True
                ).data
            ],
        }
    else:
        s.check(
            delivery is not None and revision is None, "Select exactly one delivery."
        )
        s.same_request(request, delivery.request)
        source = {"delivery": delivery}
        revision_number = 1
        snapshot = {
            "destination": delivery.destination,
            "recipient": delivery.recipient_name,
            "logistics": delivery.logistics_details,
            "lines": [
                {
                    "description": line.receipt_line.purchase_line.attempt.item.description,
                    "unit": line.receipt_line.purchase_line.attempt.item.unit,
                    "quantity": str(line.quantity),
                }
                for line in delivery.lines.select_related(
                    "receipt_line__purchase_line__attempt__item"
                )
            ],
        }
    register = m.DocumentRegister.objects.filter(**source).first()
    if not register:
        register = s.make(
            m.DocumentRegister,
            user,
            request=request,
            client=request.client,
            kind=kind,
            **source,
            **allocate_number(request.client, kind),
        )
    version = register.versions.filter(revision=revision_number).first()
    if version:
        return version
    snapshot.update(
        number=register.number,
        kind=kind,
        client=request.client_name,
        client_code=register.client_code,
        date=timezone.now().astimezone(ZoneInfo("Africa/Lagos")).date().isoformat(),
        revision=revision_number,
    )
    return s.make(
        m.DocumentVersion,
        user,
        register=register,
        revision=revision_number,
        quotation_revision=revision,
        snapshot=snapshot,
    )


@transaction.atomic
def request_approval(user, version):
    s.lock_request(version.register.request, user)
    permission(user, "requestapproval_documentregister")
    s.check(
        version.register.kind == "QT",
        "Waybills do not require ledger approval; financial documents are owned by ledger.",
    )
    if version.approval_requested_at is None:
        version.approval_requested_at = timezone.now()
        version.approval_requested_by = user
        version.save(update_fields=["approval_requested_at", "approval_requested_by"])
        for line in version.quotation_revision.lines.all():
            s.event(
                line.item,
                "QUOTATION_APPROVAL_REQUESTED",
                user,
                quote_revision=version.quotation_revision,
                reason="Awaiting ledger integration; this is not approval.",
            )
    from ledger.integrations.procurement import submit_quotation

    submit_quotation(user, version)
    return version


def approval_status(version):
    if version.register.kind == "WB":
        return "NOT_REQUIRED"
    from ledger.models import ApprovalRequest

    approval = ApprovalRequest.objects.filter(
        quotation_revision=version.quotation_revision
    ).first()
    return (
        approval.status
        if approval
        else ("AWAITING_LEDGER" if version.approval_requested_at else "DRAFT")
    )


def fingerprint(version):
    return hashlib.sha256(
        json.dumps(version.snapshot, sort_keys=True).encode()
    ).hexdigest()
