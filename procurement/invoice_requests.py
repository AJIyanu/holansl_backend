import logging
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from notifications.constants import NotificationChannel, NotificationEventMode
from notifications.data import RecipientSpec
from notifications.services import notify
from . import models as m
from .services import actor_name

logger = logging.getLogger(__name__)


def item_defaults(item):
    if item.status != m.RequestedItem.Status.DELIVERED:
        raise ValidationError("Only delivered items can be invoiced.")
    accepted = item.verdicts.filter(verdict="ACCEPTED").aggregate(
        total=Sum("quantity")
    )["total"] or Decimal("0")
    if accepted <= 0:
        raise ValidationError("The item has no accepted quantity.")
    verdict = (
        item.verdicts.filter(verdict="ACCEPTED")
        .select_related("waybill_item__waybill__purchase_order")
        .order_by("-occurred_at", "-created_at")
        .first()
    )
    waybill = verdict.waybill_item.waybill if verdict else None
    po = waybill.purchase_order if waybill else None
    po_line = po.items.filter(item=item).first() if po else None
    if not po_line or po_line.unit_price is None:
        raise ValidationError("The accepted item has no purchase-order price.")
    return {
        "item": str(item.id),
        "client": str(item.request.client_id),
        "client_name": item.request.client_name,
        "item_code": item.code,
        "description": item.name,
        "quantity": accepted,
        "unit": item.unit,
        "unit_price": po_line.unit_price,
        "currency": po.currency,
        "purchase_order": po,
        "purchase_order_reference": po.reference,
        "waybill": waybill,
        "waybill_reference": waybill.reference,
    }


def _notify_accounts(row):
    users = list(
        get_user_model()
        .objects.filter(is_active=True, is_staff=True, profile__department__code="ACC")
        .distinct()
    )
    if not users:
        return
    url = f"/dashboard/ledger/ledgers/sales/queued-requests?request={row.id}"
    try:
        notify(
            recipients=[
                RecipientSpec(user=u, action_url=url, action_label="Review request")
                for u in users
            ],
            notification_type="procurement.invoice_request.queued",
            category="procurement",
            title=f"Invoice requested for {row.item.name}",
            message=f"{row.created_by_name} requested an invoice for {row.quantity:g} {row.unit} of {row.description} for {row.client_name}.",
            channels=(NotificationChannel.DASHBOARD, NotificationChannel.EMAIL),
            event_mode=NotificationEventMode.SHARED,
            actor=row.created_by,
            source=row,
            deduplication_key=f"procurement.invoice_request:{row.id}",
        )
    except Exception:
        logger.exception("Could not deliver queued invoice-request notification")


@transaction.atomic
def create_invoice_request(user, item, data):
    defaults = item_defaults(item)
    if item.invoice_requests.filter(status__in=["QUEUED", "APPROVED"]).exists():
        raise ValidationError("This item already has an active invoice request.")
    row = m.InvoiceRequest.objects.create(
        item=item,
        client=item.request.client,
        client_name=item.request.client_name,
        purchase_order=defaults["purchase_order"],
        purchase_order_reference=defaults["purchase_order_reference"],
        waybill=defaults["waybill"],
        waybill_reference=defaults["waybill_reference"],
        created_by=user,
        created_by_name=actor_name(user),
        **data,
    )
    m.ItemActivity.objects.create(
        item=item,
        category="SYSTEM",
        title_code="INVOICE_REQUESTED",
        title="Invoice requested",
        description=f"{actor_name(user)} requested an invoice for {row.quantity:g} {row.unit} of {row.description} at {row.currency} {row.unit_price:,.2f} each through Procurement; the request is now Queued for Account review.",
        source_type="INVOICE_REQUEST",
        source_id=row.id,
        metadata={"invoice_request_id": str(row.id)},
        created_by=user,
        created_by_name=actor_name(user),
    )
    transaction.on_commit(lambda: _notify_accounts(row))
    return row


@transaction.atomic
def approve_invoice_request(user, row, data):
    from ledger.invoices import invoice_action, save_invoice, today

    row = (
        m.InvoiceRequest.objects.select_for_update()
        .select_related("client", "item")
        .get(pk=row.pk)
    )
    if row.status != "QUEUED":
        raise ValidationError("Only queued invoice requests can be approved.")
    payload = {**data, "request_key": uuid.uuid4(), "date": data.get("date") or today()}
    invoice = save_invoice(actor=user, party_id=row.client_id, payload=payload)
    invoice.invoice_request = row
    invoice.save(update_fields=["invoice_request"])
    invoice = invoice_action(
        actor=user,
        invoice_id=invoice.id,
        action="confirm",
        payload={"request_key": uuid.uuid4(), "version": invoice.version},
    )
    row.status = "APPROVED"
    row.decided_at = timezone.now()
    row.decided_by = user
    row.decided_by_name = actor_name(user)
    row.save(
        update_fields=[
            "status",
            "decided_at",
            "decided_by",
            "decided_by_name",
            "updated_at",
        ]
    )
    m.ItemActivity.objects.create(
        item=row.item,
        category="SYSTEM",
        title_code="INVOICE_REQUEST_APPROVED",
        title="Invoice request approved",
        description=f"{actor_name(user)} approved the invoice request and created confirmed invoice {invoice.reference}; the request changed from Queued to Approved.",
        source_type="INVOICE_REQUEST",
        source_id=row.id,
        metadata={
            "invoice_request_id": str(row.id),
            "invoice_id": str(invoice.id),
            "invoice_reference": invoice.reference,
        },
        created_by=user,
        created_by_name=actor_name(user),
    )
    return row


@transaction.atomic
def cancel_invoice_request(user, row, reason):
    row = (
        m.InvoiceRequest.objects.select_for_update()
        .select_related("item")
        .get(pk=row.pk)
    )
    if row.status != "QUEUED":
        raise ValidationError("Only queued invoice requests can be cancelled.")
    row.status = "CANCELLED"
    row.decision_reason = reason
    row.decided_at = timezone.now()
    row.decided_by = user
    row.decided_by_name = actor_name(user)
    row.save()
    m.ItemActivity.objects.create(
        item=row.item,
        category="SYSTEM",
        title_code="INVOICE_REQUEST_CANCELLED",
        title="Invoice request cancelled",
        description=f"{actor_name(user)} cancelled the invoice request because {reason}; the request changed from Queued to Cancelled.",
        source_type="INVOICE_REQUEST",
        source_id=row.id,
        metadata={"invoice_request_id": str(row.id), "reason": reason},
        created_by=user,
        created_by_name=actor_name(user),
    )
    return row
