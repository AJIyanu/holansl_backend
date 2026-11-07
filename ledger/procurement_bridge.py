"""Procurement references are retained; imports post once from source values."""

from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from procurement import models as p
from procurement.services import visible_requests, lock_request
from crm.models import Party
from .models import (
    ProcurementApproval,
    FinanceApproval,
    ProcurementLedgerLink,
    ProcurementInvoiceLine,
    FinanceSubmission,
)
from .party_posting import post_party_transaction
from .operations import require, recognize, recognized


def delivery_totals(line):
    scope = p.DeliveryLine.objects.filter(
        receipt_line__purchase_line__attempt__po_line_id=line.po_line_id,
        delivery__dispatched_at__isnull=False,
    )
    if not line.po_line_id:
        return Decimal("0"), Decimal("0"), Decimal("0")
    dispatched = scope.aggregate(total=Sum("quantity"))["total"] or Decimal("0")
    outcomes = p.DeliveryOutcomeLine.objects.filter(delivery_line__in=scope)
    totals = outcomes.aggregate(accepted=Sum("accepted"), rejected=Sum("rejected"))
    return (
        dispatched,
        totals["accepted"] or Decimal("0"),
        totals["rejected"] or Decimal("0"),
    )


def can_cancel(invoice):
    for line in invoice.procurement_link.lines.all():
        dispatched, accepted, rejected = delivery_totals(line)
        if accepted or dispatched > rejected:
            raise ValidationError(
                "Delivered goods must be rejected before invoice cancellation."
            )


@transaction.atomic
def sync_acceptance(request, actor, date):
    links = ProcurementLedgerLink.objects.filter(
        request=request,
        transaction__transaction_type="INVOICE",
        transaction__reversal__isnull=True,
    ).select_related("transaction")
    for link in links:
        invoice = link.transaction
        Party.objects.select_for_update().get(pk=invoice.party_id)
        target = Decimal("0")
        for line in link.lines.all():
            _, accepted, _ = delivery_totals(line)
            target += (
                line.amount * min(accepted, line.quantity) / line.quantity
            ).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        credits = invoice.adjustments.filter(transaction_type="CREDIT").aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0")
        target = max(Decimal("0"), min(target, invoice.amount) - credits)
        current = recognized(invoice)
        if target > current:
            recognize(
                invoice,
                actor,
                max(date, invoice.date),
                target - current,
                f"procurement:{invoice.pk}:{target}",
                "Client acceptance recorded against procurement delivery evidence.",
            )


class ProcurementInput(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=[
            "APPROVE",
            "REJECT",
            "APPROVE_REQUEST",
            "REJECT_REQUEST",
            "IMPORT_INVOICE",
            "IMPORT_BILL",
        ]
    )
    source_id = serializers.UUIDField()
    request_key = serializers.UUIDField()
    date = serializers.DateField()
    reason = serializers.CharField(max_length=500)


@transaction.atomic
def act(actor, payload):
    require(actor, "ledger.approve_procurement")
    form = ProcurementInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    if d["action"] in ("APPROVE_REQUEST", "REJECT_REQUEST"):
        source = get_object_or_404(
            p.FinanceRequest.objects.filter(request__in=visible_requests(actor)),
            pk=d["source_id"],
        )
        lock_request(source.request, actor)
        if not FinanceSubmission.objects.filter(request=source).exists():
            raise ValidationError("Submit the finance request before review.")
        approved = d["action"] == "APPROVE_REQUEST"
        row, created = FinanceApproval.objects.get_or_create(
            request=source,
            defaults={"approved": approved, "reason": d["reason"], "actor": actor},
        )
        if not created and (
            row.approved != approved
            or row.actor_id != actor.pk
            or row.reason != d["reason"]
        ):
            raise ValidationError("This request already has a recorded decision.")
        return {
            "id": str(source.pk),
            "message": "Request reviewed. This decision does not record a payment.",
        }
    if d["action"] in ("APPROVE", "REJECT"):
        source = get_object_or_404(
            p.ClientQuotationRevision.objects.filter(
                quotation__request__in=visible_requests(actor)
            ),
            pk=d["source_id"],
        )
        lock_request(source.quotation.request, actor)
        if not p.DocumentVersion.objects.filter(
            quotation_revision=source, approval_requested_at__isnull=False
        ).exists():
            raise ValidationError("Approval has not been requested for this revision.")
        row, created = ProcurementApproval.objects.get_or_create(
            revision=source,
            defaults={
                "approved": d["action"] == "APPROVE",
                "reason": d["reason"],
                "actor": actor,
            },
        )
        if not created and (
            row.approved != (d["action"] == "APPROVE")
            or row.actor_id != actor.pk
            or row.reason != d["reason"]
        ):
            raise ValidationError(
                "This revision has already been decided. Submit a revised quotation."
            )
        return {"id": str(source.pk), "message": "Decision recorded."}
    invoice = d["action"] == "IMPORT_INVOICE"
    source = get_object_or_404(
        (p.FinanceRequest if invoice else p.SupplierPurchase).objects.filter(
            request__in=visible_requests(actor)
        ),
        pk=d["source_id"],
    )
    lock_request(source.request, actor)
    if invoice and (
        source.kind != "INVOICE"
        or not FinanceSubmission.objects.filter(request=source).exists()
    ):
        raise ValidationError("Submit an invoice request first.")
    field = "finance_request" if invoice else "supplier_purchase"
    existing = ProcurementLedgerLink.objects.filter(**{field: source}).first()
    if existing:
        require(actor, f"ledger.view_{existing.transaction.ledger_kind}ledger")
        return {
            "id": str(existing.transaction_id),
            "message": "Source was already imported.",
        }
    if invoice:
        from .models import SalesInvoice
        from .invoices import access

        access(actor, "add")
        existing_document = SalesInvoice.objects.filter(finance_request=source).first()
        if existing_document:
            return {
                "id": str(existing_document.pk),
                "message": "Invoice document already created; open it in the sales ledger.",
            }
    lines = list(source.lines.all())
    amounts = [
        (line.amount if invoice else line.quantity * line.unit_price).quantize(
            Decimal(".01"), rounding=ROUND_HALF_UP
        )
        for line in lines
    ]
    if not lines or any(a <= 0 for a in amounts):
        raise ValidationError("Every source line must have a positive goods value.")
    if invoice:
        from .models import SalesInvoiceItem

        for line in lines:
            if SalesInvoiceItem.objects.filter(
                source_item=line.item,
                po_line=line.po_line,
                invoice__posted_transaction__reversal__isnull=True,
            ).exists():
                raise ValidationError(
                    "This source item/order line already has an invoice document."
                )
            if ProcurementInvoiceLine.objects.filter(
                item=line.item,
                po_line=line.po_line,
                link__transaction__reversal__isnull=True,
            ).exists():
                raise ValidationError(
                    "This source item/order line already has an invoice. Review it before importing another."
                )
    if invoice:
        from .invoices import save_invoice

        items = []
        for line, amount in zip(lines, amounts):
            unit_price = (amount / line.quantity).quantize(
                Decimal(".01"), rounding=ROUND_HALF_UP
            )
            if (unit_price * line.quantity).quantize(
                Decimal(".01"), rounding=ROUND_HALF_UP
            ) != amount:
                raise ValidationError(
                    "Source line totals must match quantity multiplied by a two-decimal unit price."
                )
            items.append(
                {
                    "description": line.item.description,
                    "quantity": str(line.quantity),
                    "unit": line.item.unit or "each",
                    "unit_price": str(unit_price),
                }
            )
        document = save_invoice(
            actor=actor,
            source=source,
            party_id=source.request.client_id,
            payload={
                "request_key": str(d["request_key"]),
                "date": str(d["date"]),
                "currency": source.currency,
                "billed_to": source.request.client.display_name,
                "notes": d["reason"],
                "items": items,
            },
        )
        document.finance_request = source
        document.save(update_fields=["finance_request"])
        for item, line in zip(document.items.all(), lines):
            item.source_item = line.item
            item.po_line = line.po_line
            item.save(update_fields=["source_item", "po_line"])
        return {
            "id": str(document.pk),
            "message": "Invoice created. Confirm it in the sales ledger; it posts only when marked delivered.",
        }
    record = post_party_transaction(
        actor=actor,
        payload={
            "request_key": str(d["request_key"]),
            "party_id": str(
                source.request.client_id if invoice else source.supplier_id
            ),
            "ledger_kind": "sales" if invoice else "purchase",
            "transaction_type": "INVOICE" if invoice else "BILL",
            "currency": source.currency,
            "amount": str(sum(amounts)),
            "date": str(d["date"]),
            "description": d["reason"],
            "reference": f"PROC-{source.pk}",
        },
    )
    link = ProcurementLedgerLink.objects.create(
        transaction=record, request=source.request, **{field: source}
    )
    if invoice:
        for line, amount in zip(lines, amounts):
            ProcurementInvoiceLine.objects.create(
                link=link,
                item=line.item,
                po_line=line.po_line,
                quantity=line.quantity,
                amount=amount,
            )
        sync_acceptance(source.request, actor, d["date"])
    return {"id": str(record.pk), "message": "Source posted to the ledger."}
