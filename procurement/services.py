import hashlib
import itertools
import string
import uuid
from decimal import Decimal
from io import BytesIO
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.db.models import Max, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from crm.document_services import (
    calculate_document_checksum,
    sanitize_document_filename,
)
from crm.models import Party
from crm.storage import get_document_storage
from notifications.services.document_email import send_document_email
from . import models as m
from .quotation_documents import render_quotation_pdf
from .waybill_documents import render_waybill_pdf

LAGOS = ZoneInfo("Africa/Lagos")


def decimal_display(value):
    return format(Decimal(str(value)).normalize(), "f")


ACTIVITY_TITLES = {
    "SOURCING": "Item is being sourced",
    "SUPPLIER_SEARCH": "Supplier search in progress",
    "ITEM_FOUND": "Item found",
    "PRICE_REVIEW": "Price review required",
    "WAITING_CLIENT": "Waiting for client response",
    "WAITING_INTERNAL": "Waiting for internal review",
    "PAID": "Item paid for",
    "IN_TRANSIT": "Item being transported",
    "RECEIVED_BY_HOLAN": "Item received by Holan",
    "PREPARED_FOR_DELIVERY": "Item prepared for delivery",
    "GENERAL_UPDATE": "General update",
    "CUSTOM": "Custom",
}
STATUS_TITLES = {
    "OPEN": "Item reopened",
    "SUSPENDED": "Item suspended",
    "DELIVERED": "Item delivered",
    "CLOSED": "Item closed",
}
EDITABLE_ITEM_FIELDS = {
    "name": "item name",
    "description": "description",
    "specification": "specification",
    "brand": "brand",
    "quantity": "quantity",
    "unit": "unit",
    "urgency": "urgency",
    "deadline": "deadline",
}


def actor_name(user):
    return user.get_full_name().strip() or user.email


def ensure_client(party):
    if not Party.objects.active().with_role("CLIENT").filter(pk=party.pk).exists():
        raise ValidationError({"client": "Choose an active CRM client."})


def ensure_supplier(party):
    if not Party.objects.active().with_role("SUPPLIER").filter(pk=party.pk).exists():
        raise ValidationError({"supplier": "Choose an active CRM supplier."})


def client_code(user, client):
    existing = m.ProcurementClientCode.objects.filter(client=client).first()
    if existing:
        return existing
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('procurement.client.code', 0))"
        )
    existing = m.ProcurementClientCode.objects.filter(client=client).first()
    if existing:
        return existing
    letters = "".join(
        c for c in client.display_name.upper() if c in string.ascii_uppercase
    )
    preferred = letters[:3].ljust(3, "X")
    occupied = set(m.ProcurementClientCode.objects.values_list("code", flat=True))
    code = (
        preferred
        if preferred not in occupied
        else next(
            (
                "".join(chars)
                for chars in itertools.product(string.ascii_uppercase, repeat=3)
                if "".join(chars) not in occupied
            ),
            None,
        )
    )
    if not code:
        raise ValidationError(
            {"client": "No three-letter client code remains available."}
        )
    return m.ProcurementClientCode.objects.create(
        client=client, code=code, created_by=user, created_by_name=actor_name(user)
    )


def allocate_codes(user, client, count, registered_at):
    code = client_code(user, client).code
    local = registered_at.astimezone(LAGOS)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('procurement.item.sequence', 0))"
        )
    sequence, _ = m.ItemDailySequence.objects.get_or_create(date=local.date())
    sequence = m.ItemDailySequence.objects.select_for_update().get(pk=sequence.pk)
    start = sequence.next_serial
    sequence.next_serial += count
    sequence.save(update_fields=["next_serial"])
    stamp = local.strftime("%y%m%d%H%M")
    return [
        f"Item-{code}-{stamp}-{serial:02d}" for serial in range(start, start + count)
    ]


@transaction.atomic
def create_request(user, data):
    items = data.pop("items")
    client = data["client"]
    ensure_client(client)
    now = timezone.now()
    codes = allocate_codes(user, client, len(items), now)
    name = actor_name(user)
    request = m.ItemRequest.objects.create(
        **data,
        client_name=client.display_name,
        created_by=user,
        created_by_name=name,
    )
    for code, values in zip(codes, items):
        item = m.RequestedItem.objects.create(
            request=request, code=code, created_by=user, created_by_name=name, **values
        )
        m.ItemActivity.objects.create(
            item=item,
            category="SYSTEM",
            title_code="REQUEST_RECEIVED",
            title="Request received",
            description=(
                f"{name} registered a request received via "
                f"{request.get_received_via_display() or 'an unspecified channel'} for "
                f"{decimal_display(item.quantity)} {item.unit} of {item.name}; the item had no previous "
                "tracker state and is now Open."
            ),
            occurred_at=request.received_at,
            status_result="OPEN",
            created_by=user,
            created_by_name=name,
        )
    return request


@transaction.atomic
def reorder_item(user, source_item, data):
    items = data.pop("items")
    if len(items) != 1:
        raise ValidationError({"items": "A reorder creates one new tracked item."})
    client = data["client"]
    ensure_client(client)
    now = timezone.now()
    code = allocate_codes(user, client, 1, now)[0]
    name = actor_name(user)
    request = m.ItemRequest.objects.create(
        **data,
        client_name=client.display_name,
        created_by=user,
        created_by_name=name,
    )
    item = m.RequestedItem.objects.create(
        request=request,
        reordered_from=source_item,
        code=code,
        created_by=user,
        created_by_name=name,
        **items[0],
    )
    m.ItemActivity.objects.create(
        item=item,
        category="SYSTEM",
        title_code="ITEM_REORDERED",
        title="Item reordered",
        description=(
            f"{name} reordered {item.name} from {source_item.code} through the "
            f"Procurement reorder form for {decimal_display(item.quantity)} "
            f"{item.unit}; the new tracker is {item.code} and is now Open."
        ),
        occurred_at=request.received_at,
        status_result="OPEN",
        source_type="REQUESTED_ITEM",
        source_id=source_item.id,
        metadata={
            "reordered_from_id": str(source_item.id),
            "reordered_from_code": source_item.code,
        },
        created_by=user,
        created_by_name=name,
    )
    return request


@transaction.atomic
def add_activity(user, item, data):
    item = m.RequestedItem.objects.select_for_update().get(pk=item.pk)
    if item.status in {"DELIVERED", "CLOSED"}:
        raise ValidationError("Completed items cannot receive new activities.")
    code = data["title_code"].upper()
    if data["category"] == "COMMENT":
        title = "Comment"
        code = "COMMENT"
    elif code == "CUSTOM":
        title = data.get("custom_title", "").strip()
        if not title:
            raise ValidationError({"custom_title": "Enter the custom activity title."})
    else:
        title = ACTIVITY_TITLES.get(code)
        if not title:
            raise ValidationError({"title_code": "Choose a suggested title or Custom."})
    return m.ItemActivity.objects.create(
        item=item,
        category=data["category"],
        title_code=code,
        title=title,
        description=(
            f"{actor_name(user)} added this {data['category'].lower()} through the "
            f"Procurement tracker: {data.get('description', '').strip() or title}."
        ),
        occurred_at=data.get("occurred_at", timezone.now()),
        created_by=user,
        created_by_name=actor_name(user),
    )


def _history_value(value):
    if value is None:
        return None
    return str(value)


@transaction.atomic
def edit_item(user, item, data):
    item = m.RequestedItem.objects.select_for_update().get(pk=item.pk)
    if item.status in {"DELIVERED", "CLOSED"}:
        raise ValidationError("Delivered or closed items cannot be edited.")
    changes = {}
    for field, value in data.items():
        previous = getattr(item, field)
        if previous == value:
            continue
        changes[field] = {
            "label": EDITABLE_ITEM_FIELDS[field],
            "from": _history_value(previous),
            "to": _history_value(value),
        }
        setattr(item, field, value)
    if not changes:
        raise ValidationError("No item details changed.")
    item.save(update_fields=[*changes, "updated_at"])
    labels = [change["label"] for change in changes.values()]
    if len(labels) == 1:
        summary = labels[0]
    else:
        summary = f"{', '.join(labels[:-1])} and {labels[-1]}"
    name = actor_name(user)
    m.ItemActivity.objects.create(
        item=item,
        category="SYSTEM",
        title_code="ITEM_UPDATED",
        title="Item details updated",
        description=(
            f"{name} updated the {summary} through the Procurement item editor; "
            + "; ".join(
                f"{change['label']} changed from {change['from'] or 'not set'} "
                f"to {change['to'] or 'not set'}"
                for change in changes.values()
            )
            + "."
        ),
        metadata={"changes": changes},
        created_by=user,
        created_by_name=name,
    )
    return item


@transaction.atomic
def change_status(user, item, data):
    item = m.RequestedItem.objects.select_for_update().get(pk=item.pk)
    target = data["status"]
    previous_status = item.status
    allowed = {
        "OPEN": {"SUSPENDED", "DELIVERED", "CLOSED"},
        "SUSPENDED": {"OPEN", "CLOSED"},
        "DELIVERED": set(),
        "CLOSED": set(),
    }
    if target not in allowed[item.status]:
        raise ValidationError({"status": f"Cannot change {item.status} to {target}."})
    occurred = data.get("occurred_at", timezone.now())
    item.status = target
    if target in {"DELIVERED", "CLOSED"}:
        item.completed_at = occurred
        item.completed_by = user
    else:
        item.completed_at = None
        item.completed_by = None
    item.save(update_fields=["status", "completed_at", "completed_by", "updated_at"])
    m.ItemActivity.objects.create(
        item=item,
        category="SYSTEM",
        title_code=f"STATUS_{target}",
        title=STATUS_TITLES[target],
        description=(
            f"{actor_name(user)} changed the item status through the Procurement "
            f"tracker from {m.RequestedItem.Status(previous_status).label} to "
            f"{m.RequestedItem.Status(target).label}; reason: {data['reason']}"
        ),
        occurred_at=occurred,
        status_result=target,
        created_by=user,
        created_by_name=actor_name(user),
    )
    return item


def _picture_description(name, verb, count, previous_count, current_count):
    pictures = "a picture sample" if count == 1 else f"{count} picture samples"
    return (
        f"{name} {verb} {pictures} through the Procurement item picture manager; "
        f"the stored picture count changed from {previous_count} to {current_count}."
    )


@transaction.atomic
def upload_images(user, item, uploads, caption=""):
    item = (
        m.RequestedItem.objects.select_for_update()
        .select_related("request")
        .get(pk=item.pk)
    )
    if item.status in {"DELIVERED", "CLOSED"}:
        raise ValidationError("Delivered or closed items cannot change pictures.")
    uploads = list(uploads)
    if not uploads:
        raise ValidationError({"files": "Choose at least one picture sample."})
    previous_count = item.images.count()
    if previous_count + len(uploads) > 5:
        raise ValidationError({"files": "An item can have at most 5 picture samples."})
    for uploaded in uploads:
        if uploaded.size > 5 * 1024 * 1024:
            raise ValidationError({"files": "Each picture must be 5 MB or smaller."})
        if (uploaded.content_type or "").lower() not in {
            "image/jpeg",
            "image/png",
            "image/webp",
        }:
            raise ValidationError({"files": "Upload JPEG, PNG or WebP pictures."})
    storage = get_document_storage("supabase")
    last_position = item.images.aggregate(value=Max("position"))["value"]
    position = last_position + 1 if last_position is not None else 0
    created = []
    stored_files = []
    try:
        for offset, uploaded in enumerate(uploads):
            filename = sanitize_document_filename(uploaded.name)
            mime = (uploaded.content_type or "").lower()
            checksum = calculate_document_checksum(uploaded)
            document_id = uuid.uuid4()
            uploaded.seek(0)
            stored = storage.upload(
                document_id=document_id,
                party_id=item.request.client_id,
                category=f"PROCUREMENT/ITEM_REQUESTS/{item.code}",
                filename=filename,
                mime_type=mime,
                size_bytes=uploaded.size,
                file_object=uploaded.file,
            )
            stored_files.append(stored)
            created.append(
                m.RequestedItemImage.objects.create(
                    id=document_id,
                    item=item,
                    position=position + offset,
                    caption=caption,
                    filename=filename,
                    mime_type=mime,
                    size_bytes=uploaded.size,
                    checksum=checksum,
                    storage_provider=stored.provider,
                    external_file_id=stored.external_file_id,
                    storage_path=stored.storage_path,
                    created_by=user,
                    created_by_name=actor_name(user),
                )
            )
        name = actor_name(user)
        m.ItemActivity.objects.create(
            item=item,
            category="SYSTEM",
            title_code="PICTURES_ADDED",
            title="Picture samples added",
            description=_picture_description(
                name,
                "added",
                len(created),
                previous_count,
                previous_count + len(created),
            ),
            metadata={
                "pictures": [
                    {"id": str(image.id), "filename": image.filename}
                    for image in created
                ]
            },
            created_by=user,
            created_by_name=name,
        )
        return created
    except Exception:
        for stored in stored_files:
            storage.delete(
                external_file_id=stored.external_file_id,
                storage_path=stored.storage_path,
            )
        raise


@transaction.atomic
def remove_images(user, item, image_ids):
    item = m.RequestedItem.objects.select_for_update().get(pk=item.pk)
    if item.status in {"DELIVERED", "CLOSED"}:
        raise ValidationError("Delivered or closed items cannot change pictures.")
    previous_count = item.images.count()
    images = list(
        m.RequestedItemImage.objects.select_for_update()
        .filter(item=item, id__in=image_ids)
        .order_by("position")
    )
    if len(images) != len(set(image_ids)):
        raise ValidationError({"images": "Choose existing pictures for this item."})
    for image in images:
        get_document_storage(image.storage_provider).delete(
            external_file_id=image.external_file_id,
            storage_path=image.storage_path,
        )
    details = [{"id": str(image.id), "filename": image.filename} for image in images]
    m.RequestedItemImage.objects.filter(id__in=image_ids).delete()
    for position, image in enumerate(item.images.order_by("position", "created_at")):
        if image.position != position:
            image.position = position
            image.save(update_fields=["position"])
    name = actor_name(user)
    m.ItemActivity.objects.create(
        item=item,
        category="SYSTEM",
        title_code="PICTURES_REMOVED",
        title="Picture samples removed",
        description=_picture_description(
            name,
            "removed",
            len(images),
            previous_count,
            previous_count - len(images),
        ),
        metadata={"pictures": details},
        created_by=user,
        created_by_name=name,
    )
    return images


def download_image(image):
    return get_document_storage(image.storage_provider).download(
        external_file_id=image.external_file_id,
        storage_path=image.storage_path,
        filename=image.filename,
        mime_type=image.mime_type,
    )


def _quote_activity(
    user, item, *, code, title, description, source_type, source_id, metadata=None
):
    return m.ItemActivity.objects.create(
        item=item,
        category="SYSTEM",
        title_code=code,
        title=title,
        description=description,
        source_type=source_type,
        source_id=source_id,
        metadata=metadata or {},
        created_by=user,
        created_by_name=actor_name(user),
    )


def _line_measurement(
    line, currency, *, tax_enabled=False, tax_rate=Decimal("0"), discount=Decimal("0")
):
    subtotal = line.quantity * line.unit_price
    taxable = max(Decimal("0"), subtotal - discount)
    tax = taxable * tax_rate / 100 if tax_enabled else Decimal("0")
    total = taxable + tax
    symbol = {"NGN": "₦", "USD": "$", "GBP": "£"}.get(currency, f"{currency} ")
    result = (
        f"{decimal_display(line.quantity)} {line.unit} at {symbol}{line.unit_price:,.2f} each; "
        f"subtotal {symbol}{subtotal:,.2f}"
    )
    if discount:
        result += f", allocated discount {symbol}{discount:,.2f}"
    if tax_enabled:
        rate = format(Decimal(tax_rate).normalize(), "f")
        result += f", VAT {rate}% ({symbol}{tax:,.2f})"
    return f"{result}, line total {symbol}{total:,.2f}"


def _validate_quote_attachment(upload):
    if not upload:
        return
    if upload.size > 10 * 1024 * 1024:
        raise ValidationError(
            {"attachment": "Quotation attachments must be 10 MB or smaller."}
        )
    if (upload.content_type or "").lower() not in {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
    }:
        raise ValidationError(
            {"attachment": "Upload a PDF, JPEG, PNG or WebP quotation."}
        )


@transaction.atomic
def create_supplier_quote(user, data, attachment=None):
    supplier = data["supplier"]
    ensure_supplier(supplier)
    items = data.pop("items")
    if any(row["item"].status not in {"OPEN", "SUSPENDED"} for row in items):
        raise ValidationError(
            {"items": "Only open or suspended items can receive supplier prices."}
        )
    _validate_quote_attachment(attachment)
    name = actor_name(user)
    quote = m.SupplierQuote.objects.create(
        **data,
        supplier_name=supplier.display_name,
        created_by=user,
        created_by_name=name,
    )
    for row in items:
        item = row.pop("item")
        line = m.SupplierQuoteItem.objects.create(
            quote=quote,
            item=item,
            item_name=item.name,
            unit=item.unit,
            created_by=user,
            created_by_name=name,
            **row,
        )
        _quote_activity(
            user,
            item,
            code="SUPPLIER_QUOTE_ADDED",
            title="Supplier quotation added",
            description=(
                f"{name} recorded a supplier price received via "
                f"{quote.received_via_custom if quote.received_via == 'CUSTOM' else quote.get_received_via_display()} "
                f"from {supplier.display_name} through "
                f"the Procurement supplier quote form for "
                f"{_line_measurement(line, quote.currency, tax_enabled=line.tax_enabled, tax_rate=line.tax_rate)}; "
                "this supplier-price record previously did not exist and is now active."
            ),
            source_type="SUPPLIER_QUOTE_ITEM",
            source_id=line.id,
            metadata={"quote_id": str(quote.id), "supplier": supplier.display_name},
        )
    if attachment:
        filename = sanitize_document_filename(attachment.name)
        checksum = calculate_document_checksum(attachment)
        attachment.seek(0)
        stored = get_document_storage("supabase").upload(
            document_id=quote.id,
            party_id=supplier.id,
            category=f"PROCUREMENT/SUPPLIER_QUOTES/{quote.id}",
            filename=filename,
            mime_type=(attachment.content_type or "").lower(),
            size_bytes=attachment.size,
            file_object=attachment.file,
        )
        quote.attachment_filename = filename
        quote.attachment_mime_type = (attachment.content_type or "").lower()
        quote.attachment_size_bytes = attachment.size
        quote.attachment_checksum = checksum
        quote.attachment_storage_provider = stored.provider
        quote.attachment_external_file_id = stored.external_file_id
        quote.attachment_storage_path = stored.storage_path
        quote.save(
            update_fields=[
                "attachment_filename",
                "attachment_mime_type",
                "attachment_size_bytes",
                "attachment_checksum",
                "attachment_storage_provider",
                "attachment_external_file_id",
                "attachment_storage_path",
                "updated_at",
            ]
        )
    return quote


@transaction.atomic
def edit_supplier_quote(user, quote, data, attachment=None):
    quote = (
        m.SupplierQuote.objects.select_for_update()
        .prefetch_related("items__item")
        .get(pk=quote.pk)
    )
    supplier = data["supplier"]
    ensure_supplier(supplier)
    rows = data.pop("items")
    existing_item_ids = {line.item_id for line in quote.items.all()}
    if any(
        row["item"].status not in {"OPEN", "SUSPENDED"}
        and row["item"].id not in existing_item_ids
        for row in rows
    ):
        raise ValidationError(
            {"items": "Only open or suspended items can receive supplier prices."}
        )
    _validate_quote_attachment(attachment)
    previous_lines = {str(line.item_id): line for line in quote.items.all()}
    header_changes = []
    for field, value in data.items():
        previous = getattr(quote, field)
        if previous != value:
            label = field.replace("_", " ")
            old = previous.display_name if field == "supplier" else previous
            new = value.display_name if field == "supplier" else value
            old_text = decimal_display(old) if isinstance(old, Decimal) else old
            new_text = decimal_display(new) if isinstance(new, Decimal) else new
            header_changes.append(
                f"{label} changed from {old_text or 'none'} to {new_text or 'none'}"
            )
            setattr(quote, field, value)
    quote.supplier_name = supplier.display_name
    quote.save()

    quote.items.all().delete()
    name = actor_name(user)
    new_lines = {}
    for values in rows:
        item = values.pop("item")
        line = m.SupplierQuoteItem.objects.create(
            quote=quote,
            item=item,
            item_name=item.name,
            unit=item.unit,
            created_by=user,
            created_by_name=name,
            **values,
        )
        new_lines[str(item.id)] = line

    for item_id in set(previous_lines) | set(new_lines):
        before = previous_lines.get(item_id)
        after = new_lines.get(item_id)
        item = (after or before).item
        changes = list(header_changes)
        if before and after:
            for field, label in [
                ("quantity", "quantity"),
                ("unit_price", "unit price"),
                ("tax_enabled", "tax inclusion"),
                ("tax_rate", "tax rate"),
                ("note", "note"),
            ]:
                old, new = getattr(before, field), getattr(after, field)
                if old != new:
                    if field in {"quantity", "unit_price", "tax_rate"}:
                        old_text = decimal_display(old)
                        new_text = decimal_display(new)
                    else:
                        old_text, new_text = old, new
                    changes.append(
                        f"{label} changed from {old_text or 'none'} to {new_text or 'none'}"
                    )
        elif after:
            changes.append("the item was added to the supplier quotation")
        else:
            changes.append("the item was removed from the supplier quotation")
        detail = "; ".join(changes) or "the quotation was saved without a value change"
        measurement = (
            _line_measurement(
                after,
                quote.currency,
                tax_enabled=after.tax_enabled,
                tax_rate=after.tax_rate,
            )
            if after
            else f"the removed line was {decimal_display(before.quantity)} {before.unit} at {quote.currency} {before.unit_price:,.2f} each"
        )
        _quote_activity(
            user,
            item,
            code="SUPPLIER_QUOTE_EDITED",
            title="Supplier quotation edited",
            description=(
                f"{name} edited the supplier quotation from {quote.supplier_name} "
                f"through the Procurement supplier quote form: {detail}; {measurement}."
            ),
            source_type="SUPPLIER_QUOTE_ITEM",
            source_id=(after or before).id,
            metadata={"quote_id": str(quote.id), "supplier": quote.supplier_name},
        )

    if attachment:
        old_storage = (
            quote.attachment_storage_provider,
            quote.attachment_external_file_id,
            quote.attachment_storage_path,
        )
        filename = sanitize_document_filename(attachment.name)
        checksum = calculate_document_checksum(attachment)
        attachment.seek(0)
        stored = get_document_storage("supabase").upload(
            document_id=quote.id,
            party_id=supplier.id,
            category=f"PROCUREMENT/SUPPLIER_QUOTES/{quote.id}",
            filename=filename,
            mime_type=(attachment.content_type or "").lower(),
            size_bytes=attachment.size,
            file_object=attachment.file,
        )
        quote.attachment_filename = filename
        quote.attachment_mime_type = (attachment.content_type or "").lower()
        quote.attachment_size_bytes = attachment.size
        quote.attachment_checksum = checksum
        quote.attachment_storage_provider = stored.provider
        quote.attachment_external_file_id = stored.external_file_id
        quote.attachment_storage_path = stored.storage_path
        quote.save()
        if old_storage[1]:
            get_document_storage(old_storage[0]).delete(
                external_file_id=old_storage[1], storage_path=old_storage[2]
            )
    return quote


def download_supplier_attachment(quote):
    if not quote.attachment_external_file_id:
        raise ValidationError("This supplier quotation has no attachment.")
    return get_document_storage(quote.attachment_storage_provider).download(
        external_file_id=quote.attachment_external_file_id,
        storage_path=quote.attachment_storage_path,
        filename=quote.attachment_filename,
        mime_type=quote.attachment_mime_type,
    )


def _allocate_quotation_reference(on_date):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('procurement.quotation.sequence', 0))"
        )
    sequence, _ = m.QuotationDailySequence.objects.get_or_create(date=on_date)
    sequence = m.QuotationDailySequence.objects.select_for_update().get(pk=sequence.pk)
    serial = sequence.next_serial
    sequence.next_serial += 1
    sequence.save(update_fields=["next_serial"])
    return f"HOL-QTN-{on_date:%y%m%d}-{serial:02d}"


def quotation_snapshot(quote):
    items = list(quote.items.all())
    subtotal = sum((row.quantity * row.unit_price for row in items), Decimal("0"))
    if quote.discount_type == "PERCENT":
        discount = subtotal * quote.discount_value / 100
    elif quote.discount_type == "AMOUNT":
        discount = quote.discount_value
    else:
        discount = Decimal("0")
    net = max(Decimal("0"), subtotal - discount)
    tax = net * quote.tax_rate / 100 if quote.tax_enabled else Decimal("0")
    return {
        "reference": quote.reference,
        "version": quote.version,
        "client_name": quote.client_name,
        "quotation_date": str(quote.quotation_date),
        "expires_on": str(quote.expires_on),
        "currency": quote.currency,
        "tax_rate": str(quote.tax_rate if quote.tax_enabled else 0),
        "discount_type": quote.discount_type,
        "discount_value": str(quote.discount_value),
        "notes": quote.notes,
        "terms": quote.terms,
        "items": [
            {
                "id": str(row.item_id),
                "name": row.item_name,
                "description": row.description,
                "quantity": str(row.quantity),
                "unit": row.unit,
                "unit_price": str(row.unit_price),
                "amount": str(row.quantity * row.unit_price),
            }
            for row in items
        ],
        "subtotal": str(subtotal),
        "discount": str(discount),
        "net": str(net),
        "tax": str(tax),
        "total": str(net + tax),
    }


def _quotation_line_allocations(quote):
    lines = list(quote.items.select_related("item"))
    subtotal = sum((line.quantity * line.unit_price for line in lines), Decimal("0"))
    if quote.discount_type == "PERCENT":
        total_discount = subtotal * quote.discount_value / 100
    elif quote.discount_type == "AMOUNT":
        total_discount = quote.discount_value
    else:
        total_discount = Decimal("0")
    return [
        (
            line,
            total_discount * (line.quantity * line.unit_price) / subtotal
            if subtotal
            else Decimal("0"),
        )
        for line in lines
    ]


def _ensure_current_revision(quote):
    if not quote.document_external_file_id or not quote.snapshot:
        return None
    revision, _ = m.ClientQuotationRevision.objects.get_or_create(
        quotation=quote,
        version=quote.version,
        defaults={
            "snapshot": quote.snapshot,
            "issued_at": quote.issued_at or timezone.now(),
            "issued_by_name": actor_name(quote.issued_by)
            if quote.issued_by
            else quote.created_by_name,
            "document_filename": quote.document_filename,
            "document_mime_type": quote.document_mime_type,
            "document_size_bytes": quote.document_size_bytes or 0,
            "document_checksum": quote.document_checksum,
            "document_storage_provider": quote.document_storage_provider,
            "document_external_file_id": quote.document_external_file_id,
            "document_storage_path": quote.document_storage_path,
            "created_by": quote.issued_by,
            "created_by_name": actor_name(quote.issued_by)
            if quote.issued_by
            else quote.created_by_name,
        },
    )
    return revision


@transaction.atomic
def create_client_quotation(user, data):
    client = data["client"]
    ensure_client(client)
    rows = data.pop("items")
    on_date = data.pop("quotation_date", None) or timezone.localdate()
    name = actor_name(user)
    quote = m.ClientQuotation.objects.create(
        **data,
        quotation_date=on_date,
        reference=_allocate_quotation_reference(on_date),
        client_name=client.display_name,
        created_by=user,
        created_by_name=name,
    )
    for row in rows:
        item = row.pop("item")
        row["description"] = row.get("description", "").strip() or item.name
        m.ClientQuotationItem.objects.create(
            quotation=quote,
            item=item,
            item_name=item.name,
            unit=item.unit,
            created_by=user,
            created_by_name=name,
            **row,
        )
    for line, discount in _quotation_line_allocations(quote):
        _quote_activity(
            user,
            line.item,
            code="CLIENT_QUOTATION_CREATED",
            title="Client quotation created",
            description=(
                f"{name} created quotation {quote.reference} through the Procurement "
                f"quotation form for {_line_measurement(line, quote.currency, tax_enabled=quote.tax_enabled, tax_rate=quote.tax_rate, discount=discount)}; "
                "this item previously had no link to this quotation and is now included in its Draft."
            ),
            source_type="CLIENT_QUOTATION_ITEM",
            source_id=line.id,
            metadata={"quotation_id": str(quote.id), "reference": quote.reference},
        )
    return quote


@transaction.atomic
def edit_client_quotation(user, quote, data):
    quote = (
        m.ClientQuotation.objects.select_for_update()
        .prefetch_related("items__item")
        .get(pk=quote.pk)
    )
    if quote.status not in {"DRAFT", "ISSUED"}:
        raise ValidationError("Only draft or issued quotations can be edited.")
    if data["client"].pk != quote.client_id:
        raise ValidationError(
            {"client": "A quotation revision must retain its client."}
        )
    was_issued = quote.status == "ISSUED"
    if was_issued:
        _ensure_current_revision(quote)
    previous_lines = {str(line.item_id): line for line in quote.items.all()}
    rows = data.pop("items")
    data.pop("client")
    data.pop("quotation_date", None)
    header_changes = {}
    for field, value in data.items():
        previous = getattr(quote, field)
        if previous != value:
            header_changes[field] = {"from": str(previous), "to": str(value)}
            setattr(quote, field, value)
    if was_issued:
        quote.version += 1
        quote.status = "DRAFT"
        quote.snapshot = {}
        quote.document_filename = ""
        quote.document_mime_type = ""
        quote.document_size_bytes = None
        quote.document_checksum = ""
        quote.document_storage_provider = ""
        quote.document_external_file_id = ""
        quote.document_storage_path = ""
        quote.issued_at = None
        quote.issued_by = None
    quote.save()
    quote.items.all().delete()
    name = actor_name(user)
    new_lines = {}
    for row in rows:
        item = row.pop("item")
        line = m.ClientQuotationItem.objects.create(
            quotation=quote,
            item=item,
            item_name=item.name,
            unit=item.unit,
            created_by=user,
            created_by_name=name,
            **row,
        )
        new_lines[str(item.id)] = line
    all_item_ids = set(previous_lines) | set(new_lines)
    for item_id in all_item_ids:
        before = previous_lines.get(item_id)
        after = new_lines.get(item_id)
        item = (after or before).item
        changes = []
        if before and after:
            for field, label in [
                ("description", "description"),
                ("quantity", "quantity"),
                ("unit_price", "unit price"),
            ]:
                old, new = getattr(before, field), getattr(after, field)
                if old != new:
                    if field in {"quantity", "unit_price"}:
                        old_text = decimal_display(old)
                        new_text = decimal_display(new)
                    else:
                        old_text, new_text = old, new
                    changes.append(f"{label} changed from {old_text} to {new_text}")
        elif after:
            changes.append("the item was added to the quotation")
        else:
            changes.append("the item was removed from the quotation")
        state = (
            f"Revision {quote.version - 1} Issued to Revision {quote.version} Draft"
            if was_issued
            else f"Revision {quote.version} Draft updated"
        )
        line = after or before
        measurement = (
            _line_measurement(
                line,
                quote.currency,
                tax_enabled=quote.tax_enabled,
                tax_rate=quote.tax_rate,
            )
            if after
            else f"the previous line of {decimal_display(before.quantity)} {before.unit} at {quote.currency} {before.unit_price:,.2f} was removed"
        )
        _quote_activity(
            user,
            item,
            code="CLIENT_QUOTATION_REVISED"
            if was_issued
            else "CLIENT_QUOTATION_EDITED",
            title="Client quotation revised"
            if was_issued
            else "Client quotation edited",
            description=f"{name} {'revised' if was_issued else 'edited'} quotation {quote.reference} through the Procurement quotation editor for {measurement}; {', '.join(changes) if changes else 'the line pricing remained unchanged'}; the quotation changed from {state}.",
            source_type="CLIENT_QUOTATION_ITEM" if after else "CLIENT_QUOTATION",
            source_id=after.id if after else quote.id,
            metadata={
                "quotation_id": str(quote.id),
                "reference": quote.reference,
                "version": quote.version,
                "changes": changes,
                "header_changes": header_changes,
            },
        )
    return quote


@transaction.atomic
def issue_client_quotation(user, quote):
    quote = (
        m.ClientQuotation.objects.select_for_update()
        .prefetch_related("items__item")
        .get(pk=quote.pk)
    )
    backfill = quote.status == "ISSUED" and not quote.document_external_file_id
    if quote.status != "DRAFT" and not backfill:
        raise ValidationError("Only draft quotations can be issued.")
    snapshot = (
        quote.snapshot if backfill and quote.snapshot else quotation_snapshot(quote)
    )
    pdf = render_quotation_pdf(snapshot)
    filename = f"{quote.reference}-R{quote.version}.pdf"
    stored = get_document_storage("supabase").upload(
        document_id=quote.id,
        party_id=quote.client_id,
        category=f"PROCUREMENT/CLIENT_QUOTATIONS/{quote.reference}",
        filename=filename,
        mime_type="application/pdf",
        size_bytes=len(pdf),
        file_object=BytesIO(pdf),
    )
    quote.snapshot = snapshot
    if not backfill:
        quote.status = "ISSUED"
        quote.issued_at = timezone.now()
        quote.issued_by = user
    quote.document_filename = filename
    quote.document_mime_type = "application/pdf"
    quote.document_size_bytes = len(pdf)
    quote.document_checksum = hashlib.sha256(pdf).hexdigest()
    quote.document_storage_provider = stored.provider
    quote.document_external_file_id = stored.external_file_id
    quote.document_storage_path = stored.storage_path
    quote.save(
        update_fields=[
            "snapshot",
            "status",
            "issued_at",
            "issued_by",
            "document_filename",
            "document_mime_type",
            "document_size_bytes",
            "document_checksum",
            "document_storage_provider",
            "document_external_file_id",
            "document_storage_path",
            "updated_at",
        ]
    )
    m.ClientQuotationRevision.objects.update_or_create(
        quotation=quote,
        version=quote.version,
        defaults={
            "snapshot": snapshot,
            "issued_at": quote.issued_at,
            "issued_by_name": actor_name(user),
            "document_filename": filename,
            "document_mime_type": "application/pdf",
            "document_size_bytes": len(pdf),
            "document_checksum": quote.document_checksum,
            "document_storage_provider": stored.provider,
            "document_external_file_id": stored.external_file_id,
            "document_storage_path": stored.storage_path,
            "created_by": user,
            "created_by_name": actor_name(user),
        },
    )
    if backfill:
        return quote
    name = actor_name(user)
    for line, discount in _quotation_line_allocations(quote):
        _quote_activity(
            user,
            line.item,
            code="CLIENT_QUOTATION_ISSUED",
            title="Client quotation issued",
            description=(
                f"{name} issued quotation {quote.reference} through the Procurement "
                f"quotation workflow for {_line_measurement(line, quote.currency, tax_enabled=quote.tax_enabled, tax_rate=quote.tax_rate, discount=discount)}; "
                f"the quotation changed from Draft to Issued and expires on {quote.expires_on:%d %B %Y}."
            ),
            source_type="CLIENT_QUOTATION_ITEM",
            source_id=line.id,
            metadata={"quotation_id": str(quote.id), "reference": quote.reference},
        )
    return quote


@transaction.atomic
def record_quotation_communication(user, quote, data):
    if quote.status != "ISSUED":
        raise ValidationError("Issue this quotation before recording communication.")
    communication = m.ClientQuotationCommunication.objects.create(
        quotation=quote, created_by=user, created_by_name=actor_name(user), **data
    )
    verb = {
        "DOWNLOAD": "downloaded",
        "PRINT": "opened for printing",
        "EMAIL": "emailed",
    }[communication.method]
    name = actor_name(user)
    for line, discount in _quotation_line_allocations(quote):
        _quote_activity(
            user,
            line.item,
            code=f"CLIENT_QUOTATION_{communication.method}",
            title=f"Client quotation {verb}",
            description=(
                f"{name} {verb} quotation {quote.reference} as a PDF through the "
                f"{communication.get_method_display()} action"
                f"{f' to {communication.destination}' if communication.destination else ''} "
                f"for {_line_measurement(line, quote.currency, tax_enabled=quote.tax_enabled, tax_rate=quote.tax_rate, discount=discount)}."
            ),
            source_type="CLIENT_QUOTATION_ITEM",
            source_id=line.id,
            metadata={
                "quotation_id": str(quote.id),
                "reference": quote.reference,
                "communication_id": str(communication.id),
                "method": communication.method,
            },
        )
    return communication


def download_client_quotation(quote):
    if quote.status != "ISSUED" or not quote.document_external_file_id:
        raise ValidationError("Issue this quotation before opening its PDF.")
    return get_document_storage(quote.document_storage_provider).download(
        external_file_id=quote.document_external_file_id,
        storage_path=quote.document_storage_path,
        filename=quote.document_filename,
        mime_type=quote.document_mime_type,
    )


def email_client_quotation(user, quote, data):
    if quote.status != "ISSUED":
        raise ValidationError("Issue this quotation before emailing it.")
    downloaded = download_client_quotation(quote)
    content = downloaded.file_object.read()
    result = send_document_email(
        destination=data["destination"],
        subject=data.get("subject") or f"Quotation {quote.reference} — HolanSL",
        message=data.get("message")
        or f"Dear {quote.client_name},\n\nPlease find attached quotation {quote.reference}.\n\nKind regards,\nHolanSL",
        filename=quote.document_filename,
        content=content,
        request_key=data["request_key"],
    )
    record_quotation_communication(
        user,
        quote,
        {
            "method": "EMAIL",
            "destination": data["destination"],
            "note": "Email provider accepted the quotation delivery request.",
        },
    )
    return result


@transaction.atomic
def remove_client_quotation_document(user, quote):
    quote = (
        m.ClientQuotation.objects.select_for_update()
        .prefetch_related("items__item")
        .get(pk=quote.pk)
    )
    if not quote.document_external_file_id:
        raise ValidationError("This quotation has no current PDF to remove.")
    revision = _ensure_current_revision(quote)
    filename = quote.document_filename
    get_document_storage(quote.document_storage_provider).delete(
        external_file_id=quote.document_external_file_id,
        storage_path=quote.document_storage_path,
    )
    now = timezone.now()
    revision.document_external_file_id = ""
    revision.document_storage_path = ""
    revision.removed_at = now
    revision.removed_by = user
    revision.removed_by_name = actor_name(user)
    revision.save(
        update_fields=[
            "document_external_file_id",
            "document_storage_path",
            "removed_at",
            "removed_by",
            "removed_by_name",
        ]
    )
    quote.document_external_file_id = ""
    quote.document_storage_path = ""
    quote.save(
        update_fields=[
            "document_external_file_id",
            "document_storage_path",
            "updated_at",
        ]
    )
    name = actor_name(user)
    for line, discount in _quotation_line_allocations(quote):
        _quote_activity(
            user,
            line.item,
            code="CLIENT_QUOTATION_DOCUMENT_REMOVED",
            title="Quotation PDF removed",
            description=f"{name} removed {filename} for quotation {quote.reference} Revision {quote.version} through the quotation document controls for {_line_measurement(line, quote.currency, tax_enabled=quote.tax_enabled, tax_rate=quote.tax_rate, discount=discount)}; the PDF previously existed in private storage and is now unavailable while the quotation snapshot and pricing remain preserved.",
            source_type="CLIENT_QUOTATION_ITEM",
            source_id=line.id,
            metadata={
                "quotation_id": str(quote.id),
                "reference": quote.reference,
                "version": quote.version,
                "filename": filename,
            },
        )
    return quote


def _allocate_dated_reference(sequence_model, lock_name, prefix, on_date):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [lock_name]
        )
    sequence, _ = sequence_model.objects.get_or_create(date=on_date)
    sequence = sequence_model.objects.select_for_update().get(pk=sequence.pk)
    serial = sequence.next_serial
    sequence.next_serial += 1
    sequence.save(update_fields=["next_serial"])
    return f"HOL-{prefix}-{on_date:%y%m%d}-{serial:02d}"


def _activity(user, item, code, title, description, source, source_id, metadata=None):
    return m.ItemActivity.objects.create(
        item=item,
        category="SYSTEM",
        title_code=code,
        title=title,
        description=description,
        source_type=source,
        source_id=source_id,
        metadata=metadata or {},
        created_by=user,
        created_by_name=actor_name(user),
    )


def _upload_business_attachment(file, *, document_id, party_id, category):
    if not file:
        return None
    allowed = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed or file.size > 5 * 1024 * 1024:
        raise ValidationError(
            {"attachment": "Upload a PDF, JPEG, PNG or WebP file up to 5 MB."}
        )
    safe = sanitize_document_filename(file.name)
    file.seek(0)
    return get_document_storage("supabase").upload(
        document_id=document_id,
        party_id=party_id,
        category=category,
        filename=safe,
        mime_type=file.content_type,
        size_bytes=file.size,
        file_object=file,
    )


@transaction.atomic
def create_purchase_order(user, data, attachment=None):
    rows = data.pop("items")
    ensure_client(data["client"])
    name = actor_name(user)
    on_date = timezone.localtime(data.get("received_at", timezone.now()), LAGOS).date()
    po = m.PurchaseOrder.objects.create(
        **data,
        reference=_allocate_dated_reference(
            m.PurchaseOrderDailySequence,
            "procurement.purchase_order.sequence",
            "PO",
            on_date,
        ),
        client_name=data["client"].display_name,
        created_by=user,
        created_by_name=name,
    )
    checksum = calculate_document_checksum(attachment) if attachment else ""
    stored = _upload_business_attachment(
        attachment,
        document_id=po.id,
        party_id=po.client_id,
        category=f"PROCUREMENT/PURCHASE_ORDERS/{po.reference}",
    )
    if stored:
        po.attachment_filename = sanitize_document_filename(attachment.name)
        po.attachment_mime_type = attachment.content_type
        po.attachment_size_bytes = attachment.size
        po.attachment_checksum = checksum
        po.attachment_storage_provider = stored.provider
        po.attachment_external_file_id = stored.external_file_id
        po.attachment_storage_path = stored.storage_path
        po.save(
            update_fields=[
                "attachment_filename",
                "attachment_mime_type",
                "attachment_size_bytes",
                "attachment_checksum",
                "attachment_storage_provider",
                "attachment_external_file_id",
                "attachment_storage_path",
                "updated_at",
            ]
        )
    for values in rows:
        item = values.pop("item")
        line = m.PurchaseOrderItem.objects.create(
            purchase_order=po,
            item=item,
            item_name=item.name,
            unit=item.unit,
            created_by=user,
            created_by_name=name,
            **values,
        )
        price = (
            f" at {po.currency} {line.unit_price:,.2f} each"
            if line.unit_price is not None
            else " without a recorded price"
        )
        _activity(
            user,
            item,
            "PURCHASE_ORDER_RECEIVED",
            "Purchase order received",
            f"{name} recorded client purchase order {po.reference} ({po.client_reference}) through the Procurement purchase-order form for {decimal_display(line.quantity)} {line.unit} of {item.name}{price}; the PO is now Queued.",
            "purchase_order",
            po.id,
            {"purchase_order_reference": po.reference, "line_id": str(line.id)},
        )
    return po


@transaction.atomic
def change_purchase_order_status(user, po, status, reason):
    po = m.PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    previous = po.status
    if previous == status:
        raise ValidationError({"status": "Choose a different status."})
    reason = reason.strip()
    po.status, po.status_reason = status, reason
    po.save(update_fields=["status", "status_reason", "updated_at"])
    name = actor_name(user)
    for line in po.items.select_related("item"):
        _activity(
            user,
            line.item,
            "PURCHASE_ORDER_STATUS_CHANGED",
            "Purchase order status changed",
            f"{name} changed purchase order {po.reference} from {m.PurchaseOrder.Status(previous).label} to {m.PurchaseOrder.Status(status).label} through the Procurement purchase-order controls{f' because {reason}' if reason else ''}; the order covers {decimal_display(line.quantity)} {line.unit} of {line.item_name}.",
            "purchase_order",
            po.id,
            {"from": previous, "to": status, "reason": reason},
        )
    return po


@transaction.atomic
def create_purchase(user, data):
    rows = data.pop("items")
    party = data["purchased_from"]
    if not Party.objects.active().filter(pk=party.pk).exists():
        raise ValidationError({"purchased_from": "Choose an active CRM party."})
    name = actor_name(user)
    purchase = m.Purchase.objects.create(
        **data,
        purchased_from_name=party.display_name,
        created_by=user,
        created_by_name=name,
    )
    po = purchase.purchase_order
    previous_po_status = po.status if po else ""
    if po and po.status != "ACTIVE":
        po.status = "ACTIVE"
        po.status_reason = "A supplier purchase was recorded for this purchase order."
        po.save(update_fields=["status", "status_reason", "updated_at"])
    logistics = (
        purchase.get_logistics_method_display()
        if purchase.logistics_method
        else "no recorded logistics method"
    )
    if purchase.logistics_method == "CUSTOM":
        logistics = purchase.logistics_method_custom
    for values in rows:
        item = values.pop("item")
        line = m.PurchaseItem.objects.create(
            purchase=purchase,
            item=item,
            item_name=item.name,
            unit=item.unit,
            created_by=user,
            created_by_name=name,
            status_changed_by=user,
            **values,
        )
        po_text = (
            f" linked to {purchase.purchase_order.reference}"
            if purchase.purchase_order
            else " with no linked purchase order"
        )
        _activity(
            user,
            item,
            "ITEM_PURCHASED",
            "Item purchased",
            f"{name} recorded the purchase of {decimal_display(line.quantity)} {line.unit} of {item.name} from {party.display_name} at {purchase.currency} {line.unit_price:,.2f} each ({purchase.currency} {line.quantity * line.unit_price:,.2f} total), using {logistics}{po_text}; the item remains {item.get_status_display()}.",
            "purchase",
            purchase.id,
            {"purchase_id": str(purchase.id), "line_id": str(line.id)},
        )
        if po and previous_po_status != "ACTIVE":
            _activity(
                user,
                item,
                "PURCHASE_ORDER_ACTIVATED",
                "Purchase order activated",
                f"{name} recorded a supplier purchase of {decimal_display(line.quantity)} {line.unit} of {item.name}, which changed purchase order {po.reference} from {m.PurchaseOrder.Status(previous_po_status).label} to Active.",
                "purchase_order",
                po.id,
                {"from": previous_po_status, "to": "ACTIVE"},
            )
    return purchase


@transaction.atomic
def change_purchase_item_status(user, purchase, line_id, status, reason=""):
    line = (
        m.PurchaseItem.objects.select_for_update()
        .select_related("item", "purchase")
        .get(pk=line_id, purchase=purchase)
    )
    previous = line.status
    if previous == status:
        raise ValidationError({"status": "Choose a different status."})
    reason = reason.strip()
    line.status = status
    line.status_reason = reason
    line.status_changed_at = timezone.now()
    line.status_changed_by = user
    line.save(
        update_fields=[
            "status",
            "status_reason",
            "status_changed_at",
            "status_changed_by",
        ]
    )
    name = actor_name(user)
    _activity(
        user,
        line.item,
        "PURCHASE_STATUS_CHANGED",
        "Purchase status changed",
        f"{name} changed the purchase status for {decimal_display(line.quantity)} {line.unit} of {line.item_name} from {m.PurchaseItem.Status(previous).label} to {m.PurchaseItem.Status(status).label} through the Procurement purchase controls{f' because {reason}' if reason else ''}; the recorded purchase value remains {line.purchase.currency} {line.quantity * line.unit_price:,.2f}.",
        "purchase",
        line.purchase_id,
        {"purchase_item_id": str(line.id), "from": previous, "to": status},
    )
    return line


def waybill_snapshot(waybill):
    return {
        "reference": waybill.reference,
        "client_name": waybill.client_name,
        "purchase_order_reference": (
            waybill.purchase_order.reference if waybill.purchase_order else ""
        ),
        "waybill_date": str(waybill.waybill_date),
        "delivery_personnel": waybill.delivery_personnel,
        "delivered_to": waybill.delivered_to,
        "delivery_address": waybill.delivery_address,
        "logistics_method": waybill.logistics_method,
        "driver_name": waybill.driver_name,
        "vehicle_details": waybill.vehicle_details,
        "signatory_name": waybill.signatory_name,
        "received_at": waybill.received_at.isoformat() if waybill.received_at else None,
        "notes": waybill.notes,
        "items": [
            {
                "id": str(line.id),
                "item_id": str(line.item_id),
                "item_name": line.item_name,
                "description": line.description,
                "quantity": str(line.quantity),
                "unit": line.unit,
            }
            for line in waybill.items.all()
        ],
    }


@transaction.atomic
def create_waybill(user, data):
    rows = data.pop("items")
    ensure_client(data["client"])
    if data.get("purchase_order") and data["purchase_order"].status == "FULFILLED":
        raise ValidationError(
            {"purchase_order": "Choose an unfulfilled purchase order."}
        )
    name = actor_name(user)
    on_date = data.get("waybill_date", timezone.localdate())
    waybill = m.Waybill.objects.create(
        **data,
        reference=_allocate_dated_reference(
            m.WaybillDailySequence, "procurement.waybill.sequence", "WYB", on_date
        ),
        client_name=data["client"].display_name,
        created_by=user,
        created_by_name=name,
    )
    for values in rows:
        item = values.pop("item")
        description = values.pop("description", "").strip()
        if not description:
            latest_quote_line = (
                m.ClientQuotationItem.objects.filter(item=item)
                .order_by("-quotation__quotation_date", "-created_at")
                .first()
            )
            description = (
                latest_quote_line.description if latest_quote_line else item.name
            )
        line = m.WaybillItem.objects.create(
            waybill=waybill,
            item=item,
            item_name=item.name,
            description=description,
            unit=item.unit,
            created_by=user,
            created_by_name=name,
            **values,
        )
        _activity(
            user,
            item,
            "ITEM_WAYBILLED",
            "Item waybilled",
            f"{name} added {decimal_display(line.quantity)} {line.unit} of {item.name} to waybill {waybill.reference} for delivery to {waybill.delivered_to} at {waybill.delivery_address} using {waybill.logistics_method or 'an unspecified logistics method'}; the item remains {item.get_status_display()}.",
            "waybill",
            waybill.id,
            {"waybill_reference": waybill.reference, "waybill_item_id": str(line.id)},
        )
    snapshot = waybill_snapshot(waybill)
    pdf = render_waybill_pdf(snapshot)
    filename = f"{waybill.reference}.pdf"
    stored = get_document_storage("supabase").upload(
        document_id=waybill.id,
        party_id=waybill.client_id,
        category=f"PROCUREMENT/WAYBILLS/{waybill.reference}",
        filename=filename,
        mime_type="application/pdf",
        size_bytes=len(pdf),
        file_object=BytesIO(pdf),
    )
    waybill.snapshot = snapshot
    waybill.document_filename = filename
    waybill.document_mime_type = "application/pdf"
    waybill.document_size_bytes = len(pdf)
    waybill.document_checksum = hashlib.sha256(pdf).hexdigest()
    waybill.document_storage_provider = stored.provider
    waybill.document_external_file_id = stored.external_file_id
    waybill.document_storage_path = stored.storage_path
    waybill.save(
        update_fields=[
            "snapshot",
            "document_filename",
            "document_mime_type",
            "document_size_bytes",
            "document_checksum",
            "document_storage_provider",
            "document_external_file_id",
            "document_storage_path",
            "updated_at",
        ]
    )
    return waybill


def download_waybill(waybill):
    if not waybill.document_external_file_id:
        raise ValidationError("This waybill has no generated PDF.")
    return get_document_storage(waybill.document_storage_provider).download(
        external_file_id=waybill.document_external_file_id,
        storage_path=waybill.document_storage_path,
        filename=waybill.document_filename,
        mime_type=waybill.document_mime_type,
    )


def record_waybill_communication(user, waybill, method, destination=""):
    row = m.WaybillCommunication.objects.create(
        waybill=waybill,
        method=method,
        destination=destination,
        created_by=user,
        created_by_name=actor_name(user),
    )
    verb = {"DOWNLOAD": "downloaded", "PRINT": "printed", "EMAIL": "emailed"}[method]
    for line in waybill.items.select_related("item"):
        _activity(
            user,
            line.item,
            f"WAYBILL_{method}",
            f"Waybill {verb}",
            f"{actor_name(user)} {verb} waybill {waybill.reference} as a PDF{' to ' + destination if destination else ''} through the Procurement waybill controls for {decimal_display(line.quantity)} {line.unit} of {line.item_name}; the recorded waybill quantity remains {decimal_display(line.quantity)} {line.unit}.",
            "waybill",
            waybill.id,
            {"waybill_reference": waybill.reference, "communication_id": str(row.id)},
        )
    return row


def email_waybill(user, waybill, data):
    downloaded = download_waybill(waybill)
    send_document_email(
        destination=data["destination"],
        subject=data.get("subject") or f"Waybill {waybill.reference}",
        message=data.get("message")
        or f"Please find attached waybill {waybill.reference}.",
        filename=downloaded.filename,
        content=downloaded.file_object.read(),
        request_key=data["request_key"],
    )
    return record_waybill_communication(user, waybill, "EMAIL", data["destination"])


@transaction.atomic
def create_item_verdict(user, data):
    line = (
        m.WaybillItem.objects.select_for_update(of=("self",))
        .select_related("item", "waybill__purchase_order")
        .get(pk=data["waybill_item"].pk)
    )
    verdict = data["verdict"]
    quantity = data["quantity"]
    totals = {
        row["verdict"]: row["total"]
        for row in line.verdicts.values("verdict").annotate(total=Sum("quantity"))
    }
    accepted_rejected = totals.get("ACCEPTED", Decimal("0")) + totals.get(
        "REJECTED", Decimal("0")
    )
    if (
        verdict in {"ACCEPTED", "REJECTED"}
        and accepted_rejected + quantity > line.quantity
    ):
        raise ValidationError(
            {
                "quantity": "Accepted and rejected quantities cannot exceed the waybilled quantity."
            }
        )
    returned_disposed = totals.get("RETURNED", Decimal("0")) + totals.get(
        "DISPOSED", Decimal("0")
    )
    if verdict in {
        "RETURNED",
        "DISPOSED",
    } and returned_disposed + quantity > totals.get("REJECTED", Decimal("0")):
        raise ValidationError(
            {
                "quantity": "Returned and disposed quantities cannot exceed the rejected quantity."
            }
        )
    supplier = data.get("supplier")
    authoriser = data.get("authorised_by")
    row = m.ItemVerdict.objects.create(
        **data,
        item=line.item,
        unit=line.unit,
        supplier_name=supplier.display_name if supplier else "",
        authorised_by_name=actor_name(authoriser) if authoriser else "",
        created_by=user,
        created_by_name=actor_name(user),
    )
    reason = f" because {row.reason}" if row.reason else ""
    third_party = (
        f" with {row.supplier_name}"
        if row.supplier_name
        else (
            f" authorised by {row.authorised_by_name}" if row.authorised_by_name else ""
        )
    )
    _activity(
        user,
        line.item,
        f"ITEM_{verdict}",
        f"Item {row.get_verdict_display().lower()}",
        f"{actor_name(user)} recorded {decimal_display(quantity)} {line.unit} of {line.item_name} as {row.get_verdict_display().lower()} against waybill {line.waybill.reference}{third_party}{reason}; accepted and rejected now total {decimal_display(accepted_rejected + quantity if verdict in {'ACCEPTED', 'REJECTED'} else accepted_rejected)} of {decimal_display(line.quantity)} {line.unit}, while returned and disposed now total {decimal_display(returned_disposed + quantity if verdict in {'RETURNED', 'DISPOSED'} else returned_disposed)} of {decimal_display(totals.get('REJECTED', Decimal('0')))} rejected.",
        "item_verdict",
        row.id,
        {"waybill_reference": line.waybill.reference, "verdict": verdict},
    )
    if verdict == "ACCEPTED":
        if line.item.status != "DELIVERED":
            previous_item_status = line.item.status
            line.item.status = "DELIVERED"
            line.item.completed_at = timezone.now()
            line.item.completed_by = user
            line.item.save(
                update_fields=["status", "completed_at", "completed_by", "updated_at"]
            )
            _activity(
                user,
                line.item,
                "ITEM_DELIVERED",
                "Item delivered",
                f"{actor_name(user)} changed {line.item.name} from {m.RequestedItem.Status(previous_item_status).label} to Delivered through the accepted item verdict; reason: Item was Accepted; {decimal_display(quantity)} {line.unit} was accepted against waybill {line.waybill.reference}.",
                "item_verdict",
                row.id,
                {"from": previous_item_status, "to": "DELIVERED"},
            )
    po = line.waybill.purchase_order
    if po and verdict == "REJECTED" and po.status != "ATTENTION_REQUIRED":
        previous_po_status = po.status
        po.status = "ATTENTION_REQUIRED"
        po.status_reason = f"Rejected verdict recorded on {line.waybill.reference}."
        po.save(update_fields=["status", "status_reason", "updated_at"])
        _activity(
            user,
            line.item,
            "PURCHASE_ORDER_ATTENTION_REQUIRED",
            "Purchase order needs attention",
            f"{actor_name(user)} recorded a rejected verdict for {decimal_display(quantity)} {line.unit} of {line.item_name} on waybill {line.waybill.reference}; purchase order {po.reference} changed from {m.PurchaseOrder.Status(previous_po_status).label} to Attention required.",
            "purchase_order",
            po.id,
            {"from": previous_po_status, "to": "ATTENTION_REQUIRED"},
        )
    if po and verdict == "ACCEPTED" and po.status != "FULFILLED":
        po = m.PurchaseOrder.objects.select_for_update().get(pk=po.pk)
        complete = True
        po_lines = list(po.items.select_related("item"))
        for po_line in po_lines:
            accepted = m.ItemVerdict.objects.filter(
                verdict="ACCEPTED",
                item=po_line.item,
                waybill_item__waybill__purchase_order=po,
            ).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
            if accepted < po_line.quantity:
                complete = False
                break
        if complete:
            previous_po_status = po.status
            po.status = "FULFILLED"
            po.status_reason = "All ordered quantities were accepted by the client."
            po.save(update_fields=["status", "status_reason", "updated_at"])
            for po_line in po_lines:
                _activity(
                    user,
                    po_line.item,
                    "PURCHASE_ORDER_FULFILLED",
                    "Purchase order fulfilled",
                    f"{actor_name(user)} completed the accepted verdict that satisfied all ordered quantities on purchase order {po.reference}; the PO changed from {m.PurchaseOrder.Status(previous_po_status).label} to Fulfilled, including {decimal_display(po_line.quantity)} {po_line.unit} of {po_line.item_name}.",
                    "purchase_order",
                    po.id,
                    {"from": previous_po_status, "to": "FULFILLED"},
                )
    return row
