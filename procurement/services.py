import itertools
import string
import uuid
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from crm.document_services import (
    calculate_document_checksum,
    sanitize_document_filename,
)
from crm.models import Party
from crm.storage import get_document_storage
from . import models as m

LAGOS = ZoneInfo("Africa/Lagos")
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
            description="Item request registered.",
            occurred_at=request.received_at,
            status_result="OPEN",
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
        description=data.get("description", ""),
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
        description=f"{name} updated the {summary}.",
        metadata={"changes": changes},
        created_by=user,
        created_by_name=name,
    )
    return item


@transaction.atomic
def change_status(user, item, data):
    item = m.RequestedItem.objects.select_for_update().get(pk=item.pk)
    target = data["status"]
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
        description=data["reason"],
        occurred_at=occurred,
        status_result=target,
        created_by=user,
        created_by_name=actor_name(user),
    )
    return item


def _picture_description(name, verb, count):
    pictures = "a picture sample" if count == 1 else f"{count} picture samples"
    return f"{name} {verb} {pictures}."


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
    if item.images.count() + len(uploads) > 5:
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
            description=_picture_description(name, "added", len(created)),
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
        description=_picture_description(name, "removed", len(images)),
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
