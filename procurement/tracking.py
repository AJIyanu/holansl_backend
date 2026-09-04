"""Read-only projections for the milestone UI, including replacement cycles."""

from decimal import Decimal
from django.utils import timezone
from . import models as m, services as s, serializers as z

MILESTONES = [
    ("REQUEST_RECEIVED", "Request received"),
    ("ITEM_SOURCED", "Item sourced"),
    ("QUOTATION_SENT", "Quotation sent"),
    ("QUOTATION_REVIEWED", "Quotation reviewed"),
    ("QUOTATION_EXPIRED", "Quotation expired"),
    ("QUOTATION_REISSUED", "Quotation reissued"),
    ("PURCHASE_ORDER_RECEIVED", "Purchase order received"),
    ("ITEM_PURCHASED_AND_RECEIVED", "Item purchased and received by Holan"),
    ("ITEM_WAYBILLED", "Item waybilled to client"),
    ("ITEM_ACCEPTED", "Item accepted by client"),
    ("ITEM_REJECTED", "Item rejected by client"),
    ("INVOICE_SENT", "Invoice sent"),
    ("PAYMENT_RECEIVED", "Payment received"),
    ("ITEM_REORDERED", "Item reordered by client"),
]
FINANCE = {
    "available": False,
    "status": "INTEGRATION_UNAVAILABLE",
    "reason": "Ledger is not implemented. No official invoice, payment, allocation or refund verification is available.",
}


def attempt_summary(attempt):
    purchases = attempt.purchase_lines.all()
    receipts = m.GoodsReceiptLine.objects.filter(purchase_line__attempt=attempt)
    deliveries = m.DeliveryLine.objects.filter(
        receipt_line__purchase_line__attempt=attempt
    )
    outcomes = m.DeliveryOutcomeLine.objects.filter(
        delivery_line__receipt_line__purchase_line__attempt=attempt
    )
    closed = m.RejectionAction.objects.filter(
        rejection__in=outcomes, kind=m.RejectionAction.Kind.CLOSE
    )
    replacements = m.FulfilmentAttempt.objects.filter(replaces__in=outcomes)
    values = {
        "target": attempt.quantity,
        "purchased": s.total(purchases),
        "received": s.total(receipts),
        "reserved_for_delivery": s.total(deliveries),
        "dispatched": s.total(deliveries.filter(delivery__dispatched_at__isnull=False)),
        "accepted": s.total(outcomes, "accepted"),
        "rejected": s.total(outcomes, "rejected"),
        "closed_rejected": s.total(closed),
        "assigned_to_replacements": s.total(replacements),
    }
    values["unresolved_rejected"] = (
        values["rejected"]
        - values["closed_rejected"]
        - values["assigned_to_replacements"]
    )
    if values["accepted"] == attempt.quantity:
        state = "ACCEPTED"
    elif values["unresolved_rejected"]:
        state = "REJECTED_AWAITING_RESOLUTION"
    elif (
        values["closed_rejected"]
        and values["closed_rejected"]
        + values["accepted"]
        + values["assigned_to_replacements"]
        == attempt.quantity
    ):
        state = "CLOSED_WITH_REJECTION"
    elif values["assigned_to_replacements"]:
        state = "REPLACEMENT_LINKED"
    elif values["dispatched"]:
        state = "DELIVERY_IN_PROGRESS"
    elif values["received"]:
        state = "RECEIVED_BY_HOLAN"
    elif values["purchased"]:
        state = "PURCHASE_IN_PROGRESS"
    else:
        state = "SOURCING" if attempt.replaces_id else "AWAITING_PURCHASE"
    return {
        "id": str(attempt.id),
        "po_line": str(attempt.po_line_id),
        "replaces": str(attempt.replaces_id) if attempt.replaces_id else None,
        "strategy": attempt.strategy,
        "status": state,
        "quantities": {k: str(v) for k, v in values.items()},
    }


def tracker(item):
    events = item.events.all()
    event_types = set(events.values_list("kind", flat=True))
    attempts = [attempt_summary(a) for a in item.attempts.all().order_by("created_at")]
    original_qty = sum(
        (Decimal(a["quantities"]["target"]) for a in attempts if not a["replaces"]),
        Decimal(0),
    )
    accepted = sum((Decimal(a["quantities"]["accepted"]) for a in attempts), Decimal(0))
    closed = sum(
        (Decimal(a["quantities"]["closed_rejected"]) for a in attempts), Decimal(0)
    )
    conditional = {
        "QUOTATION_REVIEWED",
        "QUOTATION_EXPIRED",
        "QUOTATION_REISSUED",
        "ITEM_REJECTED",
        "ITEM_REORDERED",
    }
    milestones = []
    for code, label in MILESTONES:
        if code in {"INVOICE_SENT", "PAYMENT_RECEIVED"}:
            state = "INTEGRATION_UNAVAILABLE"
        elif code == "ITEM_REJECTED" and code in event_types:
            state = "EXCEPTION"
        elif code == "ITEM_ACCEPTED" and accepted:
            state = "COMPLETED" if accepted == original_qty else "PARTIAL"
        elif code in event_types:
            state = "RECORDED"
        else:
            state = "NOT_APPLICABLE_YET" if code in conditional else "PENDING"
        milestones.append({"code": code, "label": label, "state": state})
    recent = list(events.order_by("-created_at", "-id")[:100])
    recent.reverse()
    quotes = item.quotation_lines.select_related("revision").all()
    quote_validity = [
        {
            "revision": str(line.revision_id),
            "number": line.revision.number,
            "expires_at": line.revision.expires_at,
            "expired": bool(
                line.revision.expires_at and line.revision.expires_at <= timezone.now()
            ),
        }
        for line in quotes
    ]
    return {
        "item": str(item.id),
        "requested_quantity": str(item.quantity),
        "ordered_quantity": str(original_qty),
        "accepted_quantity": str(accepted),
        "closed_rejected_quantity": str(closed),
        "remaining_quantity": str(original_qty - accepted - closed),
        "milestones": milestones,
        "attempts": attempts,
        "quotation_validity": quote_validity,
        "events": z.READ[m.ItemTrackingEvent](recent, many=True).data,
        "event_count": events.count(),
        "history_url": f"/procurement/events/?item={item.id}",
        "previous_item": str(item.previous_item_id) if item.previous_item_id else None,
        "reorders": [str(pk) for pk in item.reorders.values_list("id", flat=True)],
        "finance": FINANCE,
    }


def available_actions(item, user):
    operate = user.has_perm("procurement.operate_procurementrequest")
    active = bool(item.request.submitted_at)
    specs = [
        ("source", "add_supplierquotation", active),
        (
            "prepare_quotation",
            "add_clientquotation",
            active and item.supplier_offers.exists(),
        ),
        (
            "record_po",
            "add_clientpurchaseorder",
            item.quotation_lines.filter(revision__issues__isnull=False).exists(),
        ),
        ("reorder", "add_procurementrequest", active),
    ]
    result = [
        {
            "action": name,
            "allowed": operate and ready and user.has_perm("procurement." + permission),
            "reason": ""
            if operate and ready and user.has_perm("procurement." + permission)
            else "Required permission or preceding business record is missing.",
        }
        for name, permission, ready in specs
    ]
    result.extend(
        {"action": name, "allowed": False, "reason": FINANCE["reason"]}
        for name in ["send_invoice", "record_payment", "verify_refund"]
    )
    return result
