"""Read-only projections for the milestone UI, including replacement cycles."""

from decimal import Decimal
from django.utils import timezone
from . import models as m, serializers as z

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
    "available": True,
    "status": "AVAILABLE",
    "reason": "Official financial records are managed by the Accounts ledger.",
}


def milestone_kinds(code):
    related = {
        "REQUEST_RECEIVED": ["DRAFT_ITEM_UPDATED"],
        "ITEM_SOURCED": ["SOURCING_REOPENED"],
        "ITEM_PURCHASED_AND_RECEIVED": ["ITEM_PURCHASED"],
        "ITEM_REJECTED": list(m.RejectionAction.Kind.values),
        "QUOTATION_REVIEWED": ["QUOTATION_APPROVAL_REQUESTED"],
        "PAYMENT_RECEIVED": [
            "PAYMENT_ALLOCATION_RELEASED",
            "INVOICE_SETTLEMENT_UPDATED",
        ],
    }
    return [code, *related.get(code, [])]


def grouped(qs, group, field="quantity"):
    from django.db.models import Sum

    return {
        str(row[group]): row["value"]
        for row in qs.values(group).annotate(value=Sum(field))
    }


def attempt_totals(item):
    receipts = m.GoodsReceiptLine.objects.filter(purchase_line__attempt__item=item)
    deliveries = m.DeliveryLine.objects.filter(
        receipt_line__purchase_line__attempt__item=item
    )
    outcomes = m.DeliveryOutcomeLine.objects.filter(
        delivery_line__receipt_line__purchase_line__attempt__item=item
    )
    outcome_path = "delivery_line__receipt_line__purchase_line__attempt_id"
    return {
        "purchased": grouped(
            m.SupplierPurchaseLine.objects.filter(attempt__item=item), "attempt_id"
        ),
        "received": grouped(receipts, "purchase_line__attempt_id"),
        "reserved_for_delivery": grouped(
            deliveries, "receipt_line__purchase_line__attempt_id"
        ),
        "dispatched": grouped(
            deliveries.filter(delivery__dispatched_at__isnull=False),
            "receipt_line__purchase_line__attempt_id",
        ),
        "accepted": grouped(outcomes, outcome_path, "accepted"),
        "rejected": grouped(outcomes, outcome_path, "rejected"),
        "closed_rejected": grouped(
            m.RejectionAction.objects.filter(
                rejection__in=outcomes, kind=m.RejectionAction.Kind.CLOSE
            ),
            "rejection__" + outcome_path,
        ),
        "assigned_to_replacements": grouped(
            m.FulfilmentAttempt.objects.filter(replaces__in=outcomes),
            "replaces__" + outcome_path,
        ),
    }


def attempt_summary(attempt, totals):
    values = {
        key: value.get(str(attempt.id), Decimal(0)) for key, value in totals.items()
    }
    values["target"] = attempt.quantity
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


EVENT_RELATED = [
    "item",
    "supplier_offer__quotation",
    "quote_revision__quotation",
    "po_line__order",
    "purchase_line__purchase",
    "delivery_line__delivery",
    "outcome_line__outcome",
    "document",
]


def tracker(item):
    events = item.events.select_related(*EVENT_RELATED)
    from django.db.models import Count

    counts = dict(
        events.values("kind").annotate(count=Count("id")).values_list("kind", "count")
    )
    event_types = set(counts)
    latest = {
        entry.kind: z.READ[m.ItemTrackingEvent](entry).data
        for entry in events.order_by(
            "kind", "-occurred_at", "-created_at", "-id"
        ).distinct("kind")
    }
    # Existing registered drafts predate registration-time event creation.
    latest.setdefault(
        "REQUEST_RECEIVED",
        {
            "id": str(item.id),
            "kind": "REQUEST_RECEIVED",
            "created_by_name": item.created_by_name,
            "occurred_at": item.created_at.isoformat(),
            "quantity": str(item.quantity),
            "reason": "Request registered (from the retained registration record).",
        },
    )
    totals = attempt_totals(item)
    attempts = [
        attempt_summary(a, totals) for a in item.attempts.all().order_by("created_at")
    ]
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
    from ledger.integrations.procurement import item_finance

    finance = item_finance(item)
    milestones = []
    for code, label in MILESTONES:
        if code == "INVOICE_SENT":
            state = (
                "RECORDED"
                if any(row["sent"] for row in finance["invoices"])
                else "PENDING"
            )
        elif code == "PAYMENT_RECEIVED":
            state = (
                "RECORDED"
                if any(
                    Decimal(row["invoice_cash_allocated"]) > 0
                    for row in finance["invoices"]
                )
                else "PENDING"
            )
        elif code == "ITEM_REJECTED" and code in event_types:
            state = "EXCEPTION"
        elif code == "ITEM_ACCEPTED" and accepted:
            state = "COMPLETED" if accepted == original_qty else "PARTIAL"
        elif code == "REQUEST_RECEIVED" or code in event_types:
            state = "RECORDED"
        else:
            state = "NOT_APPLICABLE_YET" if code in conditional else "PENDING"
        milestones.append(
            {
                "code": code,
                "label": label,
                "state": state,
                "event_count": sum(
                    counts.get(kind, 0) for kind in milestone_kinds(code)
                )
                or (1 if code == "REQUEST_RECEIVED" else 0),
                "latest_event": max(
                    (latest[kind] for kind in milestone_kinds(code) if kind in latest),
                    key=lambda entry: str(entry["occurred_at"]),
                    default=None,
                ),
            }
        )
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
        "finance": finance,
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
