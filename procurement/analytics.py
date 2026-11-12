import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.db.models.functions import ExtractMonth
from django.utils import timezone
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .models import ItemActivity, RequestedItem

LAGOS = ZoneInfo("Africa/Lagos")
CACHE_VERSION = "v1"
RANGES = {"week", "month", "quarter", "year"}
MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


class AIConfigurationError(RuntimeError):
    pass


def _aware(day, *, end=False):
    value = datetime.combine(day, time.max if end else time.min)
    return timezone.make_aware(value, LAGOS)


def analytics_period(range_name):
    range_name = (range_name or "year").lower()
    if range_name not in RANGES:
        raise ValueError("Range must be week, month, quarter or year.")
    today = timezone.localdate()
    if range_name == "week":
        start = today - timedelta(days=today.weekday())
    elif range_name == "month":
        start = today.replace(day=1)
    elif range_name == "quarter":
        start = today.replace(month=((today.month - 1) // 3) * 3 + 1, day=1)
    else:
        start = today.replace(month=1, day=1)
    return {
        "range": range_name,
        "date_from": start,
        "date_to": today,
        "start": _aware(start),
        "end": timezone.now(),
    }


def _year_period():
    today = timezone.localdate()
    return _aware(today.replace(month=1, day=1)), _aware(
        today.replace(month=12, day=31), end=True
    )


def _open_at(moment):
    latest = (
        ItemActivity.objects.filter(
            occurred_at__lte=moment,
            status_result__in=["OPEN", "SUSPENDED", "DELIVERED", "CLOSED"],
        )
        .order_by("item_id", "-occurred_at", "-created_at")
        .distinct("item_id")
    )
    return sum(1 for activity in latest if activity.status_result == "OPEN")


def calculate_analytics(range_name):
    period = analytics_period(range_name)
    items = RequestedItem.objects.filter(
        request__received_at__gte=period["start"],
        request__received_at__lte=period["end"],
    )
    by_status = {
        row["status"]: row["count"]
        for row in items.values("status").annotate(count=Count("id"))
    }
    total = items.count()
    open_count = by_status.get("OPEN", 0)
    suspended = by_status.get("SUSPENDED", 0)
    closed = by_status.get("CLOSED", 0)
    delivered = by_status.get("DELIVERED", 0)

    now = timezone.now()
    current_week_open = RequestedItem.objects.filter(status="OPEN").count()
    previous_week_open = _open_at(now - timedelta(days=7))
    change = current_week_open - previous_week_open
    percent_change = (
        round(change / previous_week_open * 100, 2)
        if previous_week_open
        else (100.0 if current_week_open else 0.0)
    )

    year_start, year_end = _year_period()
    delivered_rows = (
        RequestedItem.objects.filter(
            status="DELIVERED",
            completed_at__gte=year_start,
            completed_at__lte=year_end,
        )
        .annotate(month=ExtractMonth("completed_at"))
        .values("month")
        .annotate(count=Count("id"))
    )
    delivered_by_month = {row["month"]: row["count"] for row in delivered_rows}

    client_rows = list(
        items.values("request__client_id", "request__client_name")
        .annotate(count=Count("id"))
        .order_by("-count", "request__client_name")
    )
    yearly_received = (
        RequestedItem.objects.filter(
            request__received_at__gte=year_start,
            request__received_at__lte=year_end,
        )
        .annotate(month=ExtractMonth("request__received_at"))
        .values("request__client_id", "request__client_name", "month")
        .annotate(count=Count("id"))
    )
    yearly_delivered = (
        RequestedItem.objects.filter(
            status="DELIVERED",
            request__received_at__gte=year_start,
            request__received_at__lte=year_end,
        )
        .annotate(month=ExtractMonth("request__received_at"))
        .values("request__client_id", "month")
        .annotate(count=Count("id"))
    )
    client_data = {}
    for row in yearly_received:
        client_id = str(row["request__client_id"])
        client_data.setdefault(
            client_id,
            {
                "client_id": client_id,
                "client_name": row["request__client_name"],
                "received": {},
                "delivered": {},
            },
        )["received"][row["month"]] = row["count"]
    for row in yearly_delivered:
        client_id = str(row["request__client_id"])
        if client_id in client_data:
            client_data[client_id]["delivered"][row["month"]] = row["count"]
    ratios = []
    for client in sorted(client_data.values(), key=lambda value: value["client_name"]):
        months = []
        for month in range(1, 13):
            received_count = client["received"].get(month, 0)
            delivered_count = client["delivered"].get(month, 0)
            months.append(
                {
                    "month": MONTHS[month - 1],
                    "received": received_count,
                    "delivered": delivered_count,
                    "ratio_percent": round(delivered_count / received_count * 100, 2)
                    if received_count
                    else 0.0,
                }
            )
        ratios.append(
            {
                "client_id": client["client_id"],
                "client_name": client["client_name"],
                "months": months,
            }
        )

    return {
        "range": period["range"],
        "date_from": period["date_from"].isoformat(),
        "date_to": period["date_to"].isoformat(),
        "cards": {
            "received": total,
            "active": open_count,
            "suspended": suspended,
            "closed": closed,
            "delivered": delivered,
            "not_delivered": total - delivered,
        },
        "open_comparison": {
            "current_week": current_week_open,
            "previous_week": previous_week_open,
            "difference": change,
            "percent_change": percent_change,
        },
        "delivered_by_month": [
            {"month": MONTHS[month - 1], "count": delivered_by_month.get(month, 0)}
            for month in range(1, 13)
        ],
        "received_by_client": [
            {
                "client_id": str(row["request__client_id"]),
                "client_name": row["request__client_name"],
                "count": row["count"],
            }
            for row in client_rows
        ],
        "client_monthly_ratios": ratios,
    }


def _cache_threshold(range_name):
    return settings.SUMMARY_CACHE_THRESHOLDS.get(
        range_name, settings.SUMMARY_CACHE_THRESHOLDS["month"]
    )


def _cached(range_name, kind, generator, max_age):
    analytics_period(range_name)
    key = f"procurement-analytics:{CACHE_VERSION}:{kind}:{range_name}"
    entry = cache.get(key)
    regenerate = not entry
    if entry:
        cached_at = datetime.fromisoformat(entry["cached_at"])
        if timezone.is_naive(cached_at):
            cached_at = timezone.make_aware(cached_at)
        regenerate = (timezone.now() - cached_at).total_seconds() >= max_age
        if not regenerate:
            last_activity = entry.get("last_activity_at")
            activities = ItemActivity.objects.all()
            if last_activity:
                activities = activities.filter(
                    created_at__gt=datetime.fromisoformat(last_activity)
                )
            regenerate = activities.count() >= _cache_threshold(range_name)
    if not regenerate:
        return {
            **entry["result"],
            "cache": {
                "hit": True,
                "cached_at": entry["cached_at"],
                "new_records_threshold": _cache_threshold(range_name),
            },
        }
    result = generator(range_name)
    latest = ItemActivity.objects.order_by("-created_at").first()
    entry = {
        "result": result,
        "cached_at": timezone.now().isoformat(),
        "last_activity_at": latest.created_at.isoformat() if latest else None,
    }
    cache.set(key, entry, timeout=max_age * 2)
    return {
        **result,
        "cache": {
            "hit": False,
            "cached_at": entry["cached_at"],
            "new_records_threshold": _cache_threshold(range_name),
        },
    }


def get_cached_analytics(range_name):
    return _cached(
        range_name,
        "summary",
        calculate_analytics,
        settings.SUMMARY_CACHE_MAX_AGE,
    )


class ProcurementInsight(BaseModel):
    summary: str
    observations: list[str] = Field(default_factory=list)
    delivery_concerns: list[str] = Field(default_factory=list)
    demand_patterns: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    disclaimer: str


def generate_ai_analytics(range_name):
    if not settings.GOOGLE_AI_API_KEY:
        raise AIConfigurationError("GOOGLE_AI_API_KEY is not configured.")
    data = calculate_analytics(range_name)
    payload = {
        "range": data["range"],
        "date_from": data["date_from"],
        "date_to": data["date_to"],
        "cards": data["cards"],
        "open_comparison": data["open_comparison"],
        "delivered_by_month": data["delivered_by_month"],
        "client_item_counts": sorted(
            [row["count"] for row in data["received_by_client"]], reverse=True
        ),
    }
    prompt = f"""
You are an operations analyst for a Nigerian procurement company.
Analyse only the supplied aggregate item-request statistics. Do not infer client
or staff identities, item descriptions, prices, documents, causes, or facts not
present in the data. Explain operational patterns, delivery concerns, demand
distribution and practical recommendations in concise professional language.

Aggregate Procurement analytics:
{json.dumps(payload, indent=2)}
""".strip()
    client = genai.Client(api_key=settings.GOOGLE_AI_API_KEY)
    response = client.models.generate_content(
        model=settings.GOOGLE_AI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ProcurementInsight,
            temperature=0.2,
        ),
    )
    insight = ProcurementInsight.model_validate_json(response.text)
    return {
        "range": range_name,
        "input_summary": payload,
        "insight": insight.model_dump(),
        "provider": "google_ai_studio",
        "model": settings.GOOGLE_AI_MODEL,
    }


def get_cached_ai_analytics(range_name):
    return _cached(
        range_name,
        "ai",
        generate_ai_analytics,
        settings.AI_CACHE_MAX_AGE,
    )
