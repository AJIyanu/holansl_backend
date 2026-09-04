"""Transactional business operations. Views never directly edit workflow state."""

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from accounts.models import AuditLog
from crm.integrations import validate_client_contact, validate_party_for_new_business
from . import models as m


def stamp(user):
    return {
        "created_by": user,
        "created_by_name": (user.get_full_name() or user.username)
        if user
        else "System",
    }


def make(model, user, **fields):
    return model.objects.create(**stamp(user), **fields)


def visible_requests(user):
    qs = m.ProcurementRequest.objects.all()
    if not user.is_active or not (user.is_staff or user.is_superuser):
        return qs.none()
    if not user.has_perm("procurement.view_procurementrequest"):
        return qs.none()
    if user.has_perm("procurement.view_all_procurementrequest"):
        return qs
    condition = Q(owner=user)
    profile = getattr(user, "profile", None)
    if (
        profile
        and profile.department_id
        and user.has_perm("procurement.view_department_procurementrequest")
    ):
        condition |= Q(department_id=profile.department_id)
    return qs.filter(condition)


def lock_request(request, user, *, submitted=True):
    try:
        obj = visible_requests(user).select_for_update().get(pk=request.pk)
    except m.ProcurementRequest.DoesNotExist:
        raise NotFound()
    if submitted and not obj.submitted_at:
        raise ValidationError(
            "Submit the request before recording operational activity."
        )
    return obj


def check(condition, message):
    if not condition:
        raise ValidationError(message)


def same_request(request, actual):
    check(
        request.pk == actual.pk,
        "Related records must belong to the same procurement request.",
    )


def validate_party(party, role):
    try:
        validate_party_for_new_business(party, role=role)
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict)


def contact(request, party):
    check(party is not None, "A receiving client contact is required.")
    try:
        validate_client_contact(client=request.client, contact_party=party)
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict)


def document(request, doc, category=None):
    if doc:
        same_request(request, doc.request)
        if category:
            check(doc.category == category, f"A {category} document is required.")


def total(qs, field="quantity"):
    return qs.aggregate(n=Sum(field))["n"] or Decimal(0)


def not_future(value):
    check(value <= timezone.now(), "An occurrence date cannot be in the future.")


def event(item, kind, user, **kwargs):
    entry = make(m.ItemTrackingEvent, user, item=item, kind=kind, **kwargs)
    AuditLog.objects.create(
        user=user,
        event_category="CRUD",
        event_type="UPDATE",
        status="SUCCESS",
        app_label="procurement",
        resource="requestitem",
        action=kind.lower(),
        object_id=str(item.id),
        metadata={"event_id": str(entry.id)},
    )
    # Persist dashboard notifications in the same transaction; no outbound email.
    owner = item.request.owner
    if (
        kind in {"ITEM_REJECTED", "QUOTATION_EXPIRED", "CLOSED_REJECTED"}
        and owner.is_active
        and owner != user
        and visible_requests(owner).filter(pk=item.request_id).exists()
    ):
        from notifications.services import notify

        notify(
            recipients=[owner],
            actor=user,
            title="Procurement item needs attention",
            message="An assigned procurement item has a new workflow event.",
            category="procurement",
            notification_type=kind.lower(),
            channels=["DASHBOARD"],
            source=entry,
            deduplication_key=f"procurement:{entry.id}",
        )
    return entry


@transaction.atomic
def create_request(user, data, previous_item=None):
    data = dict(data)
    items = data.pop("items")
    validate_party(data["client"], "CLIENT")
    if data.get("contact"):
        try:
            validate_client_contact(
                client=data["client"], contact_party=data["contact"]
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict)
    owner = data.setdefault("owner", user)
    check(
        owner.is_active and (owner.is_staff or owner.is_superuser),
        "Owner must be active staff.",
    )
    if "department" not in data:
        data["department"] = getattr(
            getattr(owner, "profile", None), "department", None
        )
    if not user.has_perm("procurement.view_all_procurementrequest"):
        profile = getattr(user, "profile", None)
        own_department = getattr(profile, "department_id", None)
        check(owner == user, "Assigning another owner requires all-request scope.")
        check(
            getattr(data.get("department"), "pk", None) == own_department,
            "Department must match your staff department.",
        )
    not_future(data.get("received_at", timezone.now()))
    obj = make(m.ProcurementRequest, user, client_name=str(data["client"]), **data)
    for row in items:
        make(m.RequestItem, user, request=obj, previous_item=previous_item, **row)
    return obj


@transaction.atomic
def submit_request(user, obj):
    obj = lock_request(obj, user, submitted=False)
    check(obj.submitted_at is None, "Request has already been submitted.")
    validate_party(obj.client, "CLIENT")
    check(obj.items.exists(), "At least one item is required.")
    obj.submitted_at = timezone.now()
    obj.version += 1
    obj.save(update_fields=["submitted_at", "version"])
    for item in obj.items.all():
        event(
            item,
            "REQUEST_RECEIVED",
            user,
            quantity=item.quantity,
            occurred_at=obj.received_at,
        )
    return obj


@transaction.atomic
def edit_request(user, obj, data):
    obj = lock_request(obj, user, submitted=False)
    check(obj.submitted_at is None, "Submitted requests are immutable.")
    check(
        obj.version == data["expected_version"],
        "Request changed; reload before editing.",
    )
    if "notes" in data:
        obj.notes = data["notes"]
    obj.version += 1
    obj.save(update_fields=["notes", "version"])
    return obj


@transaction.atomic
def create_offer(user, data, previous=None):
    data = dict(data)
    request = lock_request(data["request"], user)
    rows = data.pop("lines")
    validate_party(data["supplier"], "SUPPLIER")
    document(request, data.get("document"))
    if previous:
        same_request(request, previous.request)
        check(
            data["supplier"] == previous.supplier,
            "A supplier revision must retain its supplier.",
        )
        check(
            not m.SupplierQuotation.objects.filter(previous=previous).exists(),
            "Revise the latest supplier quotation.",
        )
        check(data.get("reason"), "Revision reason is required.")
        data["previous"] = previous
    if data.get("expires_at"):
        check(
            data["expires_at"] > timezone.now(),
            "New supplier offers must not be expired.",
        )
    obj = make(m.SupplierQuotation, user, supplier_name=str(data["supplier"]), **data)
    for row in rows:
        same_request(request, row["item"].request)
        if row.get("attempt"):
            check(
                row["attempt"].item_id == row["item"].id,
                "Attempt belongs to another item.",
            )
        line = make(m.SupplierQuotationLine, user, quotation=obj, **row)
        event(
            line.item,
            "ITEM_SOURCED",
            user,
            supplier_offer=line,
            attempt=line.attempt,
            quantity=line.quantity,
        )
    return obj


@transaction.atomic
def create_quote(user, data, quotation=None):
    data = dict(data)
    request = lock_request(data.pop("request"), user)
    rows = data.pop("lines")
    document(request, data.get("document"), "QUOTATION")
    if quotation:
        same_request(request, quotation.request)
        check(data.get("reason"), "A quotation revision requires a reason.")
        number = quotation.revisions.count() + 1
    else:
        quotation = make(m.ClientQuotation, user, request=request)
        number = 1
    if data.get("expires_at"):
        check(
            data["expires_at"] > timezone.now(), "New quotations must not be expired."
        )
    revision = make(
        m.ClientQuotationRevision, user, quotation=quotation, number=number, **data
    )
    seen = set()
    for row in rows:
        same_request(request, row["item"].request)
        check(
            row["item"].id not in seen,
            "An item may occur only once in a quotation revision.",
        )
        seen.add(row["item"].id)
        offer = row["supplier_offer"]
        check(
            offer.item_id == row["item"].id, "Supplier offer belongs to another item."
        )
        check(
            offer.quotation.currency == revision.currency,
            "Currency conversion is not implemented; use matching currencies.",
        )
        check(
            not offer.quotation.expires_at
            or offer.quotation.expires_at > timezone.now(),
            "Supplier offer has expired.",
        )
        check(
            row.get("discount", 0) <= row["quantity"] * row["unit_price"],
            "Discount exceeds line value.",
        )
        make(m.ClientQuotationLine, user, revision=revision, **row)
        if number > 1:
            event(
                row["item"],
                "QUOTATION_REVIEWED",
                user,
                quote_revision=revision,
                reason=revision.reason,
            )
    return revision


@transaction.atomic
def issue_quote(user, revision, data):
    request = lock_request(revision.quotation.request, user)
    check(
        not revision.expires_at or revision.expires_at > timezone.now(),
        "Expired quotations must be revised before sending.",
    )
    check(
        revision.number == revision.quotation.revisions.count(),
        "Only the latest revision can be sent.",
    )
    check(
        revision.document_id,
        "Attach the quotation document when creating the revision before recording it sent.",
    )
    contact(request, data["recipient"])
    occurred = data.get("occurred_at", timezone.now())
    not_future(occurred)
    check(
        occurred >= revision.created_at,
        "Sending cannot precede the quotation revision.",
    )
    obj = make(
        m.QuotationIssue,
        user,
        revision=revision,
        recipient_name=str(data["recipient"]),
        **data,
    )
    first_issue = revision.issues.count() == 1
    for line in revision.lines.all():
        if revision.number > 1 and first_issue:
            event(
                line.item,
                "QUOTATION_REISSUED",
                user,
                quote_revision=revision,
                reason=revision.reason,
                occurred_at=occurred,
            )
        event(
            line.item,
            "QUOTATION_SENT",
            user,
            quote_revision=revision,
            document=revision.document,
            quantity=line.quantity,
            occurred_at=occurred,
        )
    return obj


@transaction.atomic
def create_po(user, data):
    data = dict(data)
    request = lock_request(data["request"], user)
    rows = data.pop("lines")
    if data.get("verbal"):
        check(data.get("evidence"), "Verbal orders require confirmation evidence.")
    else:
        check(data.get("document"), "Documented orders require the PO document.")
    document(request, data.get("document"), "PURCHASE_ORDER")
    occurred = data.get("received_at", timezone.now())
    not_future(occurred)
    obj = make(m.ClientPurchaseOrder, user, **data)
    for row in rows:
        quote = row["quote_line"]
        same_request(request, quote.item.request)
        check(
            quote.revision.issues.filter(occurred_at__lte=occurred).exists(),
            "PO must reference a quotation sent before the PO was received.",
        )
        check(
            not quote.revision.expires_at or occurred <= quote.revision.expires_at,
            "PO was received after quotation expiry; reissue the quotation first.",
        )
        check(
            row["unit_price"] == quote.unit_price,
            "Changed PO unit prices require a quotation revision.",
        )
        line = make(m.ClientPurchaseOrderLine, user, order=obj, **row)
        attempt = make(
            m.FulfilmentAttempt,
            user,
            item=quote.item,
            po_line=line,
            quantity=line.quantity,
            strategy="ORIGINAL",
        )
        event(
            quote.item,
            "PURCHASE_ORDER_RECEIVED",
            user,
            quantity=line.quantity,
            po_line=line,
            attempt=attempt,
            occurred_at=occurred,
        )
    return obj


@transaction.atomic
def create_purchase(user, data):
    data = dict(data)
    request = lock_request(data["request"], user)
    rows = data.pop("lines")
    validate_party(data["supplier"], "SUPPLIER")
    document(request, data.get("document"))
    obj = make(m.SupplierPurchase, user, supplier_name=str(data["supplier"]), **data)
    for row in rows:
        attempt, offer = row["attempt"], row["supplier_offer"]
        same_request(request, attempt.item.request)
        check(
            offer.item_id == attempt.item_id
            and offer.quotation.supplier_id == obj.supplier_id,
            "Offer must match the item and purchasing supplier.",
        )
        check(
            not offer.attempt_id or offer.attempt_id == attempt.id,
            "Offer belongs to another fulfilment attempt.",
        )
        check(
            offer.quotation.currency == obj.currency,
            "Offer and purchase currencies must match.",
        )
        check(
            not offer.quotation.expires_at
            or offer.quotation.expires_at > timezone.now(),
            "Supplier offer has expired.",
        )
        if attempt.replaces_id:
            original_supplier = attempt.replaces.delivery_line.receipt_line.purchase_line.purchase.supplier_id
            if attempt.strategy == "SAME_SUPPLIER":
                check(
                    obj.supplier_id == original_supplier,
                    "Use the original supplier for a supplier replacement.",
                )
            else:
                check(
                    obj.supplier_id != original_supplier,
                    "Select a different supplier for this replacement attempt.",
                )
        check(
            total(attempt.purchase_lines.all()) + row["quantity"] <= attempt.quantity,
            "Purchase exceeds the remaining attempt quantity.",
        )
        line = make(m.SupplierPurchaseLine, user, purchase=obj, **row)
        event(
            attempt.item,
            "ITEM_PURCHASED",
            user,
            quantity=line.quantity,
            attempt=attempt,
            purchase_line=line,
        )
    return obj


@transaction.atomic
def create_receipt(user, data):
    data = dict(data)
    request = lock_request(data["purchase"].request, user)
    rows = data.pop("lines")
    document(request, data.get("document"))
    not_future(data.get("received_at", timezone.now()))
    obj = make(m.GoodsReceipt, user, **data)
    for row in rows:
        purchase_line = row["purchase_line"]
        check(
            purchase_line.purchase_id == obj.purchase_id,
            "Receipt line belongs to another purchase.",
        )
        check(
            obj.received_at >= purchase_line.created_at,
            "Receipt cannot precede the recorded purchase.",
        )
        check(
            total(purchase_line.receipts.all()) + row["quantity"]
            <= purchase_line.quantity,
            "Receipt exceeds purchased quantity.",
        )
        line = make(m.GoodsReceiptLine, user, receipt=obj, **row)
        event(
            purchase_line.attempt.item,
            "ITEM_PURCHASED_AND_RECEIVED",
            user,
            quantity=line.quantity,
            attempt=purchase_line.attempt,
            purchase_line=purchase_line,
            receipt_line=line,
            occurred_at=obj.received_at,
        )
    return obj


@transaction.atomic
def create_delivery(user, data):
    data = dict(data)
    request = lock_request(data["request"], user)
    rows = data.pop("lines")
    contact(request, data["recipient"])
    document(request, data["waybill"], "WAYBILL")
    obj = make(m.Delivery, user, recipient_name=str(data["recipient"]), **data)
    for row in rows:
        receipt = row["receipt_line"]
        same_request(request, receipt.receipt.purchase.request)
        check(
            total(receipt.deliveries.all()) + row["quantity"] <= receipt.quantity,
            "Delivery exceeds unallocated received quantity (including draft deliveries).",
        )
        make(m.DeliveryLine, user, delivery=obj, **row)
    return obj


@transaction.atomic
def dispatch(user, obj):
    lock_request(obj.request, user)
    obj.refresh_from_db()
    check(obj.dispatched_at is None, "Delivery has already been dispatched.")
    obj.dispatched_at = timezone.now()
    obj.save(update_fields=["dispatched_at"])
    for line in obj.lines.all():
        purchase = line.receipt_line.purchase_line
        event(
            purchase.attempt.item,
            "ITEM_WAYBILLED",
            user,
            quantity=line.quantity,
            attempt=purchase.attempt,
            delivery_line=line,
            document=obj.waybill,
            occurred_at=obj.dispatched_at,
        )
    return obj


@transaction.atomic
def record_outcome(user, delivery, data):
    data = dict(data)
    request = lock_request(delivery.request, user)
    delivery.refresh_from_db()
    check(delivery.dispatched_at, "Dispatch the delivery before recording its outcome.")
    rows = data.pop("lines")
    contact(request, data["receiver"])
    document(request, data.get("signed_waybill"), "SIGNED_WAYBILL")
    occurred = data.get("occurred_at", timezone.now())
    not_future(occurred)
    check(occurred >= delivery.dispatched_at, "Outcome cannot precede dispatch.")
    obj = make(
        m.DeliveryOutcome,
        user,
        delivery=delivery,
        receiver_name=str(data["receiver"]),
        **data,
    )
    for row in rows:
        line = row["delivery_line"]
        check(
            line.delivery_id == delivery.id, "Outcome line belongs to another delivery."
        )
        accepted, rejected = row["accepted"], row["rejected"]
        check(accepted + rejected > 0, "Record an accepted or rejected quantity.")
        check(
            not accepted or obj.signed_waybill_id,
            "Accepted items require a signed waybill.",
        )
        check(
            not rejected or row.get("reason", "").strip(),
            "Rejection reason is mandatory.",
        )
        check(
            total(line.outcomes.all(), "accepted")
            + total(line.outcomes.all(), "rejected")
            + accepted
            + rejected
            <= line.quantity,
            "Outcome exceeds dispatched quantity.",
        )
        outcome_line = make(m.DeliveryOutcomeLine, user, outcome=obj, **row)
        attempt = line.receipt_line.purchase_line.attempt
        for qty, kind in [(accepted, "ITEM_ACCEPTED"), (rejected, "ITEM_REJECTED")]:
            if qty:
                event(
                    attempt.item,
                    kind,
                    user,
                    quantity=qty,
                    attempt=attempt,
                    delivery_line=line,
                    outcome_line=outcome_line,
                    reason=row.get("reason", ""),
                    document=obj.signed_waybill,
                    occurred_at=occurred,
                )
    return obj


@transaction.atomic
def resolve_rejection(user, rejection, data):
    data = dict(data)
    original = rejection.delivery_line.receipt_line.purchase_line
    request = lock_request(original.attempt.item.request, user)
    check(
        user.has_perm("procurement.resolve_procurementrequest"),
        "Rejection resolution permission is required.",
    )
    check(rejection.rejected > 0, "This outcome has no rejected quantity.")
    document(request, data.get("document"))
    kind, qty = data["kind"], data["quantity"]
    actions = rejection.resolution_actions.all()
    K = m.RejectionAction.Kind
    committed = total(rejection.replacement_attempts.all()) + total(
        actions.filter(kind=K.CLOSE)
    )
    if kind in {K.REPLACE, K.SOURCE, K.CLOSE}:
        check(
            committed + qty <= rejection.rejected,
            "Quantity has already been assigned a replacement or closed.",
        )
    if kind == K.RETURNED:
        check(
            total(actions.filter(kind=K.RETURNED)) + qty <= rejection.rejected,
            "Returned quantity exceeds rejected quantity.",
        )
    if kind in {K.ACCEPTED, K.REFUSED}:
        check(
            total(actions.filter(kind__in=[K.ACCEPTED, K.REFUSED])) + qty
            <= total(actions.filter(kind=K.RETURNED)),
            "Supplier outcome exceeds quantity returned to supplier.",
        )
    if kind == K.REPLACE:
        check(
            total(actions.filter(kind=K.REPLACE)) + qty
            <= total(actions.filter(kind=K.ACCEPTED)),
            "Record the supplier's acceptance of the returned quantity first.",
        )
    check(qty <= rejection.rejected, "Action quantity exceeds rejected quantity.")
    if kind == K.REFUND:
        check(
            data.get("refund_amount") is not None
            and data["refund_amount"] > 0
            and data.get("currency")
            and data.get("document"),
            "Refund reports require a positive amount, currency and evidence; ledger verification remains pending.",
        )
    else:
        check(
            data.get("refund_amount") is None and not data.get("currency"),
            "Refund fields apply only to refund reports.",
        )
    replacement = None
    if kind in {K.REPLACE, K.SOURCE}:
        replacement = make(
            m.FulfilmentAttempt,
            user,
            item=original.attempt.item,
            po_line=original.attempt.po_line,
            quantity=qty,
            replaces=rejection,
            strategy="SAME_SUPPLIER" if kind == K.REPLACE else "DIFFERENT_SUPPLIER",
            reason=data["reason"],
        )
    obj = make(
        m.RejectionAction,
        user,
        rejection=rejection,
        replacement_attempt=replacement,
        **data,
    )
    event(
        original.attempt.item,
        kind,
        user,
        quantity=qty,
        reason=data["reason"],
        rejection_action=obj,
        outcome_line=rejection,
        attempt=replacement or original.attempt,
        document=data.get("document"),
    )
    if replacement:
        event(
            original.attempt.item,
            "SOURCING_REOPENED",
            user,
            quantity=qty,
            reason=data["reason"],
            attempt=replacement,
            outcome_line=rejection,
        )
    return obj


@transaction.atomic
def reorder(user, item, data):
    request = lock_request(item.request, user)
    row = {
        "description": item.description,
        "specification": item.specification,
        "unit": item.unit,
        "quantity": data["quantity"],
        "required_date": data.get("required_date"),
    }
    obj = create_request(
        user,
        {
            "client": request.client,
            "contact": request.contact,
            "items": [row],
            "notes": data["reason"],
        },
        previous_item=item,
    )
    submit_request(user, obj)
    new_item = obj.items.get()
    event(
        item,
        "ITEM_REORDERED",
        user,
        quantity=data["quantity"],
        reason=data["reason"],
        related_item=new_item,
    )
    return obj


@transaction.atomic
def create_cost(user, data):
    request = lock_request(data["item"].request, user)
    document(request, data["document"])
    obj = make(m.ProcurementCost, user, **data)
    event(
        obj.item,
        "ACTUAL_COST_RECORDED",
        user,
        document=obj.document,
        reason="Unapproved actual cost; ledger review pending.",
    )
    return obj


@transaction.atomic
def create_finance_request(user, data):
    data = dict(data)
    request = lock_request(data["request"], user)
    rows = data.pop("lines")
    obj = make(m.FinanceRequest, user, **data)
    for row in rows:
        same_request(request, row["item"].request)
        po, delivery = row.get("po_line"), row.get("delivery_line")
        if po:
            check(
                po.quote_line.item_id == row["item"].id,
                "PO line belongs to another item.",
            )
        if delivery:
            purchase = delivery.receipt_line.purchase_line
            check(
                purchase.attempt.item_id == row["item"].id,
                "Delivery belongs to another item.",
            )
            check(
                not po or purchase.attempt.po_line_id == po.id,
                "Delivery and PO line do not match.",
            )
        document(request, row.get("document"))
        make(m.FinanceRequestLine, user, request=obj, **row)
        event(
            row["item"],
            "FINANCE_REQUEST_DRAFTED",
            user,
            reason="Awaiting ledger integration; no official financial record created.",
        )
    return obj


@transaction.atomic
def link_task(user, data):
    from tasks.services.access import can_view_task

    lock_request(data["item"].request, user)
    check(
        user.has_perm("tasks.view_task") and can_view_task(user, data["task"]),
        "Task is not accessible.",
    )
    check(
        not m.ProcurementTaskLink.objects.filter(**data).exists(),
        "Task is already linked.",
    )
    return make(m.ProcurementTaskLink, user, **data)


@transaction.atomic
def expire_revision(revision):
    # Scheduled command only, never a read-triggered mutation.
    m.ProcurementRequest.objects.select_for_update().get(
        pk=revision.quotation.request_id
    )
    revision.refresh_from_db()
    if (
        not revision.expires_at
        or revision.expires_at > timezone.now()
        or revision.expired_recorded_at
        or not revision.issues.exists()
    ):
        return False
    revision.expired_recorded_at = timezone.now()
    revision.save(update_fields=["expired_recorded_at"])
    for line in revision.lines.all():
        event(
            line.item,
            "QUOTATION_EXPIRED",
            None,
            quote_revision=revision,
            occurred_at=revision.expires_at,
        )
    return True
