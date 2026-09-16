"""Ledger owns financial authority; procurement retains its own records and scope."""

from decimal import Decimal
from django.db import transaction
from ledger import models as m
from ledger.services.common import (
    require,
    full_scope,
    make,
    check,
    event,
    digest,
    Conflict,
    primitive,
)
from ledger.services.settlement import book_lock


@transaction.atomic
def register_financial(user, record, kind):
    from procurement import models as pm
    from procurement.document_registry import allocate_number

    register = make(
        pm.DocumentRegister,
        user,
        request=record.request,
        client=record.party,
        kind=kind,
        **allocate_number(record.party, kind),
    )
    content = {
        "number": register.number,
        "kind": kind,
        "client": record.party_name,
        "client_code": register.client_code,
        "date": str(record.date),
        "revision": record.version,
        "currency": record.currency.code,
        "lines": [],
        "amount": str(record.amount),
    }
    if kind == "IN":
        for line in record.invoice.lines.all():
            content["lines"].append(
                dict(
                    description=line.description,
                    quantity=str(line.quantity),
                    unit_price=str(line.unit_price),
                    discount_type="AMOUNT",
                    discount_value=str(line.discount),
                    discount=str(line.discount),
                    tax_rate=str(line.tax_policy.rate) if line.tax_policy_id else "0",
                    tax=str(line.tax),
                    logistics="0",
                    additional_charges="0",
                )
            )
    version = make(
        pm.DocumentVersion,
        user,
        register=register,
        revision=record.version,
        snapshot=content,
    )
    record.registered_version = version
    record.reference = register.number
    record.save(update_fields=["registered_version", "reference"])
    return version


@transaction.atomic
def set_client_code(user, party, code):
    from procurement import models as pm
    from crm.models import Party

    require(user, "generate_financialrecord")
    full_scope(user, "view_financialrecord")
    import re

    check(re.fullmatch(r"[A-Z]{3,4}", code), "Use three or four uppercase letters.")
    Party.objects.select_for_update().get(pk=party.pk)
    existing = pm.ClientDocumentCode.objects.filter(client=party).first()
    if existing:
        check(
            existing.code == code,
            "Client document code is already assigned and immutable.",
        )
        return existing
    check(
        not pm.ClientDocumentCode.objects.filter(code=code).exists(),
        "Client code is already used.",
    )
    return make(pm.ClientDocumentCode, user, client=party, code=code)


@transaction.atomic
def submit_quotation(user, version):
    from procurement.services import lock_request

    check(
        user.has_perm("procurement.requestapproval_documentregister"),
        "Quotation submission permission is missing.",
    )
    lock_request(version.register.request, user)
    obj, created = m.ApprovalRequest.objects.get_or_create(
        quotation_revision=version.quotation_revision,
        defaults={
            **dict(
                created_by=user, created_by_name=user.get_full_name() or user.username
            ),
            "snapshot": version.snapshot,
        },
    )
    if created:
        queue_intent(user, approval=obj, event="SUBMITTED")
    return obj


def require_quotation_approval(revision):
    from procurement.document_registry import IntegrationUnavailable

    if revision is None:
        raise IntegrationUnavailable()
    approval = m.ApprovalRequest.objects.filter(
        quotation_revision=revision, status="APPROVED"
    ).first()
    if (
        not approval
        or not hasattr(approval, "decision")
        or not approval.decision.approved
    ):
        raise IntegrationUnavailable()
    if not hasattr(revision, "document_version") or digest(approval.snapshot) != digest(
        revision.document_version.snapshot
    ):
        raise Conflict("Quotation revision differs from its approved snapshot.")
    return approval


@transaction.atomic
def decide_quotation(user, approval, approved, reason):
    full_scope(user, "approve_quotation")
    check(reason.strip(), "Decision reason is required.")
    approval = m.ApprovalRequest.objects.select_for_update().get(pk=approval.pk)
    check(
        approval.quotation_revision_id and approval.status == "PENDING",
        "Quotation is not awaiting a decision.",
    )
    check(
        digest(approval.snapshot)
        == digest(approval.quotation_revision.document_version.snapshot),
        "Quotation content changed.",
    )
    make(m.ApprovalDecision, user, request=approval, approved=approved, reason=reason)
    approval.status = "APPROVED" if approved else "REJECTED"
    approval.save(update_fields=["status"])
    queue_intent(user, approval=approval, event=approval.status)
    event(
        user,
        "quotation_decided",
        reason=reason,
        approval=approval.pk,
        approved=approved,
    )
    return approval


@transaction.atomic
def submit_finance(user, source):
    from procurement.services import lock_request

    check(
        user.has_perm("procurement.change_financerequest"),
        "Finance submission permission is missing.",
    )
    lock_request(source.request, user)
    content = primitive(
        {
            "id": source.pk,
            "kind": source.kind,
            "currency": source.currency,
            "notes": source.notes,
            "lines": list(source.lines.values()),
        }
    )
    obj, created = m.FinanceSubmission.objects.get_or_create(
        source=source,
        defaults={
            **dict(
                created_by=user, created_by_name=user.get_full_name() or user.username
            ),
            "snapshot": content,
        },
    )
    check(obj.snapshot == content, "Submitted finance content changed.")
    return obj


def direct_invoice_line(user, request, pk, quantity, price):
    from procurement.services import visible_requests

    check(
        visible_requests(user).filter(pk=request.pk).exists(),
        "Request is not accessible.",
    )
    try:
        line = m.InvoiceLine.objects.select_related("invoice__record", "item").get(
            pk=pk, invoice__record__request=request, invoice__record__status="POSTED"
        )
    except m.InvoiceLine.DoesNotExist:
        from rest_framework.exceptions import NotFound

        raise NotFound() from None
    book_lock(line.invoice.record.currency)
    check(
        line.item_id and line.item.request_id == request.pk,
        "Invoice line is not linked to this request item.",
    )
    check(
        price == line.unit_price, "Direct order price must match the approved invoice."
    )
    ordered = sum((r.quantity for r in line.order_links.all()), Decimal(0))
    cancelled = sum(
        (
            r.quantity
            for r in m.CreditNoteLine.objects.filter(
                line=line, credit__record__status="POSTED"
            )
        ),
        Decimal(0),
    )
    check(
        0 < quantity <= line.quantity - ordered - cancelled,
        "Order exceeds approved invoice quantity.",
    )
    return line


@transaction.atomic
def recognize(user, line, quantity, day):
    """Explicit finance action backed by recorded acceptance quantities."""
    from ledger.services.common import get_record, amount
    from ledger.services.posting import journal

    record = get_record(user, line.invoice.record_id, action="post")
    if record.request_id:
        from procurement.models import ProcurementRequest

        ProcurementRequest.objects.select_for_update().get(pk=record.request_id)
    book_lock(record.currency)
    record = get_record(user, record.pk, lock=True, action="post")
    check(record.status == "POSTED", "Invoice is not posted.")
    from procurement.models import DeliveryOutcomeLine

    po_ids = list(line.order_links.values_list("po_line_id", flat=True))
    outcomes = DeliveryOutcomeLine.objects.filter(
        delivery_line__receipt_line__purchase_line__attempt__po_line_id__in=po_ids,
        outcome__occurred_at__date__lte=day,
    )
    accepted = sum((r.accepted for r in outcomes), Decimal(0))
    recognized = sum((r.quantity for r in line.recognitions.all()), Decimal(0))
    shared = m.RevenueRecognition.objects.filter(
        line__order_links__po_line_id__in=po_ids
    ).distinct()
    committed = sum((r.quantity for r in shared), Decimal(0))
    credits = m.CreditNoteLine.objects.filter(
        line=line, credit__record__status="POSTED"
    )
    cancelled = sum((r.quantity for r in credits), Decimal(0))
    quantity = amount(quantity)
    check(
        0
        < quantity
        <= min(accepted - committed, line.quantity - cancelled - recognized),
        "Revenue requires unrecognised client-accepted quantities.",
    )
    previous = sum((r.amount for r in line.recognitions.all()), Decimal(0))
    value = (
        line.net - previous - sum((r.net for r in credits), Decimal(0))
        if recognized + quantity + cancelled == line.quantity
        else amount(line.net * quantity / line.quantity, record.currency)
    )
    entry = journal(
        user,
        record,
        "RECOGNITION:" + str(line.pk) + ":" + str(line.recognitions.count() + 1),
        [
            (record.policy.deferred_revenue, value, 0, record.party, None),
            (record.policy.revenue, 0, value, record.party, None),
        ],
        day,
    )
    obj = make(
        m.RevenueRecognition,
        user,
        line=line,
        quantity=quantity,
        amount=value,
        journal=entry,
    )
    event(user, "revenue_recognized", record, recognition=obj.pk)
    return obj


def queue_intent(user, **kwargs):
    from ledger.integrations.workflow import schedule

    intent = make(m.IntegrationIntent, user, **kwargs)
    schedule(intent)
    return intent


def tracking_event(user, record, kind, reason):
    if record.kind != "INVOICE":
        return
    from procurement.services import event

    for item in {
        line.item
        for line in record.invoice.lines.select_related("item")
        if line.item_id
    }:
        event(item, kind, user, reason=reason)


def item_finance(item):
    from ledger.services.settlement import invoice_due, applied

    invoices = (
        m.Invoice.objects.filter(lines__item=item, record__status="POSTED")
        .select_related("record")
        .distinct()
    )
    rows = []
    for invoice in invoices:
        cash = applied(invoice.allocations.filter(source__record__kind="RECEIPT"))
        rows.append(
            {
                "invoice": str(invoice.record_id),
                "reference": invoice.record.reference,
                "currency": invoice.record.currency.code,
                "item_amount": str(
                    sum(
                        (line.total for line in invoice.lines.filter(item=item)),
                        Decimal(0),
                    )
                ),
                "invoice_outstanding": str(invoice_due(invoice)),
                "invoice_cash_allocated": str(cash),
                "sent": invoice.issues.exists(),
            }
        )
    return {"available": True, "status": "AVAILABLE", "invoices": rows}
