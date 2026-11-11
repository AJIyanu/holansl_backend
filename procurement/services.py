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
from crm.models import Party, PartyAffiliation
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


def actor_name(user):
    return user.get_full_name().strip() or user.email


def ensure_client(party):
    if not Party.objects.active().with_role("CLIENT").filter(pk=party.pk).exists():
        raise ValidationError({"client": "Choose an active CRM client."})


def ensure_requester(client, requester):
    if requester is None or requester.pk == client.pk:
        return
    if not PartyAffiliation.objects.filter(
        organisation=client, person=requester, is_current=True
    ).exists():
        raise ValidationError(
            {
                "requester": "Choose the client or a current contact affiliated with the client."
            }
        )


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
    client, requester = data["client"], data.get("requester")
    ensure_client(client)
    ensure_requester(client, requester)
    now = timezone.now()
    codes = allocate_codes(user, client, len(items), now)
    name = actor_name(user)
    request = m.ItemRequest.objects.create(
        **data,
        client_name=client.display_name,
        requester_name=requester.display_name if requester else client.display_name,
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


@transaction.atomic
def upload_image(user, item, uploaded, caption=""):
    item = (
        m.RequestedItem.objects.select_for_update()
        .select_related("request")
        .get(pk=item.pk)
    )
    if item.images.count() >= 10:
        raise ValidationError({"file": "An item can have at most 10 sample images."})
    if uploaded.size > 10 * 1024 * 1024:
        raise ValidationError({"file": "Each image must be 10 MB or smaller."})
    mime = (uploaded.content_type or "").lower()
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValidationError({"file": "Upload a JPEG, PNG or WebP image."})
    filename = sanitize_document_filename(uploaded.name)
    checksum = calculate_document_checksum(uploaded)
    document_id = uuid.uuid4()
    position = (item.images.aggregate(value=Max("position"))["value"] or -1) + 1
    storage = get_document_storage("supabase")
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
    try:
        return m.RequestedItemImage.objects.create(
            id=document_id,
            item=item,
            position=position,
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
    except Exception:
        storage.delete(
            external_file_id=stored.external_file_id, storage_path=stored.storage_path
        )
        raise


def download_image(image):
    return get_document_storage(image.storage_provider).download(
        external_file_id=image.external_file_id,
        storage_path=image.storage_path,
        filename=image.filename,
        mime_type=image.mime_type,
    )
