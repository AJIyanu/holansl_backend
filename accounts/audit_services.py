import json
from datetime import timedelta
from typing import Literal

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .models import AuditLog

ALLOWED_RANGES = {
    "week": {
        "days": 7,
        "label": "Last 7 days",
    },
    "month": {
        "days": 30,
        "label": "Last 30 days",
    },
    "quarter": {
        "days": 90,
        "label": "Last 90 days",
    },
    "year": {
        "days": 365,
        "label": "Last 365 days",
    },
}


LOGIN_EVENT_TYPES = [
    AuditLog.EventType.LOGIN_SUCCESS,
    AuditLog.EventType.LOGIN_FAILED,
    AuditLog.EventType.DEFAULT_PASSWORD_LOGIN_BLOCKED,
    AuditLog.EventType.PASSWORD_RESET_LINK_SENT,
    AuditLog.EventType.PASSWORD_RESET_COMPLETED,
    AuditLog.EventType.PASSWORD_RESET_FAILED,
]


CACHE_VERSION = "v1"


def get_summary_cache_key(
    *,
    category: str,
    summary_type: str,
    range_name: str,
):
    return f"audit-summary:{CACHE_VERSION}:{category}:{summary_type}:{range_name}"


def get_cache_threshold(range_name: str) -> int:
    return settings.SUMMARY_CACHE_THRESHOLDS.get(
        range_name,
        settings.SUMMARY_CACHE_THRESHOLDS["month"],
    )


def get_category_queryset(category, date_from, date_to):
    queryset = AuditLog.objects.filter(
        created_at__gte=date_from,
        created_at__lte=date_to,
    )

    if category == "login":
        queryset = queryset.filter(event_type__in=LOGIN_EVENT_TYPES)

    return queryset


def should_regenerate_cached_result(
    *,
    cached_entry,
    category,
    range_name,
    max_age,
):
    if not cached_entry:
        return True

    cached_at_raw = cached_entry.get("cached_at")
    last_record_at_raw = cached_entry.get("last_record_at")

    if not cached_at_raw:
        return True

    cached_at = timezone.datetime.fromisoformat(cached_at_raw)

    if timezone.is_naive(cached_at):
        cached_at = timezone.make_aware(cached_at)

    cache_age = (timezone.now() - cached_at).total_seconds()

    if cache_age >= max_age:
        return True

    period = get_summary_period(range_name)

    queryset = get_category_queryset(
        category,
        period["date_from"],
        period["date_to"],
    )

    if last_record_at_raw:
        last_record_at = timezone.datetime.fromisoformat(last_record_at_raw)

        if timezone.is_naive(last_record_at):
            last_record_at = timezone.make_aware(last_record_at)

        new_record_count = queryset.filter(created_at__gt=last_record_at).count()
    else:
        new_record_count = queryset.count()

    threshold = get_cache_threshold(range_name)

    return new_record_count >= threshold


def get_or_generate_cached_result(
    *,
    category,
    summary_type,
    range_name,
    generator,
    max_age,
):
    cache_key = get_summary_cache_key(
        category=category,
        summary_type=summary_type,
        range_name=range_name,
    )

    cached_entry = cache.get(cache_key)

    regenerate = should_regenerate_cached_result(
        cached_entry=cached_entry,
        category=category,
        range_name=range_name,
        max_age=max_age,
    )

    if not regenerate:
        return {
            **cached_entry["result"],
            "cache": {
                "hit": True,
                "cached_at": cached_entry["cached_at"],
                "new_records_threshold": get_cache_threshold(range_name),
            },
        }

    result = generator(range_name)

    period = get_summary_period(range_name)

    queryset = get_category_queryset(
        category,
        period["date_from"],
        period["date_to"],
    )

    latest_record = queryset.order_by("-created_at").first()

    cached_at = timezone.now()

    entry = {
        "result": result,
        "cached_at": cached_at.isoformat(),
        "last_record_at": (
            latest_record.created_at.isoformat() if latest_record else None
        ),
    }

    cache.set(
        cache_key,
        entry,
        timeout=max_age * 2,
    )

    return {
        **result,
        "cache": {
            "hit": False,
            "cached_at": entry["cached_at"],
            "new_records_threshold": get_cache_threshold(range_name),
        },
    }


def get_cached_login_summary(range_name):
    return get_or_generate_cached_result(
        category="login",
        summary_type="rules",
        range_name=range_name,
        generator=calculate_login_summary,
        max_age=settings.SUMMARY_CACHE_MAX_AGE,
    )


def get_cached_audit_summary(range_name):
    return get_or_generate_cached_result(
        category="audit",
        summary_type="rules",
        range_name=range_name,
        generator=calculate_audit_summary,
        max_age=settings.SUMMARY_CACHE_MAX_AGE,
    )


def get_cached_login_ai_insight(range_name):
    return get_or_generate_cached_result(
        category="login",
        summary_type="ai",
        range_name=range_name,
        generator=generate_ai_security_insight,
        max_age=settings.AI_CACHE_MAX_AGE,
    )


def get_cached_audit_ai_insight(range_name):
    return get_or_generate_cached_result(
        category="audit",
        summary_type="ai",
        range_name=range_name,
        generator=generate_audit_ai_insight,
        max_age=settings.AI_CACHE_MAX_AGE,
    )


def get_summary_period(range_name: str):
    range_name = (range_name or "month").lower()

    if range_name not in ALLOWED_RANGES:
        valid_values = ", ".join(ALLOWED_RANGES.keys())
        raise ValueError(f"Invalid range. Expected one of: {valid_values}.")

    now = timezone.now()
    date_to = now
    date_from = now - timedelta(days=ALLOWED_RANGES[range_name]["days"])

    return {
        "range": range_name,
        "period": ALLOWED_RANGES[range_name]["label"],
        "date_from": date_from,
        "date_to": date_to,
    }


def get_display_name(user):
    if not user:
        return "Unknown user"

    full_name = user.get_full_name().strip()
    return full_name or user.username


def login_activity_queryset(date_from, date_to):
    return AuditLog.objects.select_related("user", "target_user").filter(
        created_at__gte=date_from,
        created_at__lte=date_to,
        event_type__in=LOGIN_EVENT_TYPES,
    )


def calculate_login_summary(range_name: str):
    period = get_summary_period(range_name)

    queryset = login_activity_queryset(
        period["date_from"],
        period["date_to"],
    )

    successful_logins = queryset.filter(
        event_type=AuditLog.EventType.LOGIN_SUCCESS
    ).count()

    failed_logins = queryset.filter(event_type=AuditLog.EventType.LOGIN_FAILED).count()

    password_reset_requests = queryset.filter(
        event_type__in=[
            AuditLog.EventType.PASSWORD_RESET_LINK_SENT,
            getattr(
                AuditLog.EventType,
                "PASSWORD_RESET_REQUESTED",
                "PASSWORD_RESET_REQUESTED",
            ),
        ]
    ).count()

    password_reset_failures = queryset.filter(
        event_type=AuditLog.EventType.PASSWORD_RESET_FAILED
    ).count()

    unique_users = (
        queryset.exclude(user__isnull=True).values("user_id").distinct().count()
    )

    top_successful_record = (
        queryset.filter(
            event_type=AuditLog.EventType.LOGIN_SUCCESS,
            user__isnull=False,
        )
        .values(
            "user_id",
            "user__first_name",
            "user__last_name",
            "user__username",
        )
        .annotate(count=Count("id"))
        .order_by("-count")
        .first()
    )

    top_successful_user = None

    if top_successful_record:
        full_name = " ".join(
            filter(
                None,
                [
                    top_successful_record["user__first_name"],
                    top_successful_record["user__last_name"],
                ],
            )
        ).strip()

        top_successful_user = {
            "user_id": str(top_successful_record["user_id"]),
            "display_name": (full_name or top_successful_record["user__username"]),
            "count": top_successful_record["count"],
        }

    # Group by the stored attempted account identifier.
    # Never return the submitted username/email publicly.
    top_failed_record = (
        queryset.filter(event_type=AuditLog.EventType.LOGIN_FAILED)
        .exclude(username_attempted="")
        .values("username_attempted")
        .annotate(count=Count("id"))
        .order_by("-count")
        .first()
    )

    top_failed_account = None

    if top_failed_record:
        top_failed_account = {
            "display_name": "Hidden account",
            "count": top_failed_record["count"],
        }

    repeated_failure_accounts = list(
        queryset.filter(event_type=AuditLog.EventType.LOGIN_FAILED)
        .exclude(username_attempted="")
        .values("username_attempted")
        .annotate(count=Count("id"))
        .filter(count__gte=settings.AUDIT_REPEATED_FAILURE_THRESHOLD)
    )

    multiple_ip_accounts = list(
        queryset.filter(
            event_type=AuditLog.EventType.LOGIN_SUCCESS,
            user__isnull=False,
            ip_address__isnull=False,
        )
        .values("user_id")
        .annotate(ip_count=Count("ip_address", distinct=True))
        .filter(ip_count__gt=1)
    )

    unusual_time_logins = queryset.filter(
        event_type=AuditLog.EventType.LOGIN_SUCCESS,
        created_at__hour__gte=settings.AUDIT_UNUSUAL_HOUR_START,
        created_at__hour__lt=settings.AUDIT_UNUSUAL_HOUR_END,
    ).count()

    risk_indicators = {
        "repeated_failures": bool(repeated_failure_accounts),
        "multiple_ips_for_account": bool(multiple_ip_accounts),
        "unusual_time_activity": unusual_time_logins > 0,
    }

    risk_points = sum(
        [
            2 if risk_indicators["repeated_failures"] else 0,
            1 if risk_indicators["multiple_ips_for_account"] else 0,
            1 if risk_indicators["unusual_time_activity"] else 0,
            1 if password_reset_failures else 0,
        ]
    )

    if risk_points >= 4:
        risk_level = "high"
        insight_text = (
            "Login activity contains several indicators requiring "
            "administrative review, including repeated authentication "
            "failures or unusual access patterns."
        )
    elif risk_points >= 2:
        risk_level = "medium"
        insight_text = (
            "Login activity is mostly stable, but one or more patterns "
            "should be reviewed by an administrator."
        )
    else:
        risk_level = "low"
        insight_text = (
            "Login activity appears generally normal for the selected "
            "period, with no major rule-based warning indicators."
        )

    return {
        "range": period["range"],
        "date_from": period["date_from"],
        "date_to": period["date_to"],
        "successful_logins": successful_logins,
        "failed_logins": failed_logins,
        "password_reset_requests": password_reset_requests,
        "password_reset_failures": password_reset_failures,
        "unique_users": unique_users,
        "top_successful_user": top_successful_user,
        "top_failed_account": top_failed_account,
        "risk_indicators": risk_indicators,
        "insight": {
            "source": "rules",
            "risk_level": risk_level,
            "text": insight_text,
        },
    }


def calculate_audit_summary(range_name: str):
    period = get_summary_period(range_name)

    queryset = AuditLog.objects.select_related("user", "target_user").filter(
        created_at__gte=period["date_from"],
        created_at__lte=period["date_to"],
    )

    create_count = queryset.filter(
        Q(action__iexact="create") | Q(event_type=AuditLog.EventType.CREATE)
    ).count()

    update_count = queryset.filter(
        Q(action__iexact="update")
        | Q(action__iexact="edit")
        | Q(event_type=AuditLog.EventType.UPDATE)
    ).count()

    delete_count = queryset.filter(
        Q(action__iexact="delete") | Q(event_type=AuditLog.EventType.DELETE)
    ).count()

    failed_count = queryset.filter(status=AuditLog.EventStatus.FAILED).count()

    most_active_record = (
        queryset.exclude(user__isnull=True)
        .values(
            "user_id",
            "user__first_name",
            "user__last_name",
            "user__username",
        )
        .annotate(count=Count("id"))
        .order_by("-count")
        .first()
    )

    most_active_actor = None

    if most_active_record:
        full_name = " ".join(
            filter(
                None,
                [
                    most_active_record["user__first_name"],
                    most_active_record["user__last_name"],
                ],
            )
        ).strip()

        most_active_actor = {
            "user_id": str(most_active_record["user_id"]),
            "display_name": (full_name or most_active_record["user__username"]),
            "count": most_active_record["count"],
        }

    resource_record = (
        queryset.exclude(resource="")
        .values("resource")
        .annotate(count=Count("id"))
        .order_by("-count")
        .first()
    )

    most_affected_resource = None

    if resource_record:
        most_affected_resource = {
            "resource": resource_record["resource"].replace("_", " ").title(),
            "count": resource_record["count"],
        }

    target_record = (
        queryset.exclude(target_user__isnull=True)
        .values("target_user_id")
        .annotate(count=Count("id"))
        .order_by("-count")
        .first()
    )

    most_affected_target = None

    if target_record:
        most_affected_target = {
            "target_type": "User",
            "display_name": "Staff accounts",
            "count": target_record["count"],
        }

    activity_by_action = [
        {
            "action": "CREATE",
            "count": create_count,
        },
        {
            "action": "UPDATE",
            "count": update_count,
        },
        {
            "action": "DELETE",
            "count": delete_count,
        },
    ]

    if delete_count >= 10 or failed_count >= 10:
        risk_level = "high"
        insight_text = (
            "The selected period contains an elevated number of "
            "deletions or failed administrative operations."
        )
    elif delete_count > 0 or failed_count > 0:
        risk_level = "medium"
        insight_text = (
            "Administrative activity was mostly routine, but deletions "
            "or failed actions should be reviewed."
        )
    else:
        risk_level = "low"
        insight_text = (
            "Administrative activity appears routine for the selected period."
        )

    return {
        "range": period["range"],
        "date_from": period["date_from"],
        "date_to": period["date_to"],
        "total_events": queryset.count(),
        "create_count": create_count,
        "update_count": update_count,
        "delete_count": delete_count,
        "failed_count": failed_count,
        "most_active_actor": most_active_actor,
        "most_affected_resource": most_affected_resource,
        "most_affected_target": most_affected_target,
        "activity_by_action": activity_by_action,
        "insight": {
            "source": "rules",
            "risk_level": risk_level,
            "text": insight_text,
        },
    }


def calculate_ai_security_payload(range_name: str):
    period = get_summary_period(range_name)

    queryset = login_activity_queryset(
        period["date_from"],
        period["date_to"],
    )

    successful = queryset.filter(event_type=AuditLog.EventType.LOGIN_SUCCESS)

    failed = queryset.filter(event_type=AuditLog.EventType.LOGIN_FAILED)

    highest_failures_for_one_account = (
        failed.exclude(username_attempted="")
        .values("username_attempted")
        .annotate(count=Count("id"))
        .order_by("-count")
        .values_list("count", flat=True)
        .first()
        or 0
    )

    highest_failures_from_one_ip = (
        failed.exclude(ip_address__isnull=True)
        .values("ip_address")
        .annotate(count=Count("id"))
        .order_by("-count")
        .values_list("count", flat=True)
        .first()
        or 0
    )

    unusual_time_logins = successful.filter(
        created_at__hour__gte=settings.AUDIT_UNUSUAL_HOUR_START,
        created_at__hour__lt=settings.AUDIT_UNUSUAL_HOUR_END,
    ).count()

    accounts_with_repeated_failures = (
        failed.exclude(username_attempted="")
        .values("username_attempted")
        .annotate(count=Count("id"))
        .filter(count__gte=settings.AUDIT_REPEATED_FAILURE_THRESHOLD)
        .count()
    )

    accounts_using_multiple_ips = (
        successful.filter(
            user__isnull=False,
            ip_address__isnull=False,
        )
        .values("user_id")
        .annotate(ip_count=Count("ip_address", distinct=True))
        .filter(ip_count__gt=1)
        .count()
    )

    blocked_default_password_logins = queryset.filter(
        event_type=AuditLog.EventType.DEFAULT_PASSWORD_LOGIN_BLOCKED
    ).count()

    completed_password_resets = queryset.filter(
        event_type=AuditLog.EventType.PASSWORD_RESET_COMPLETED
    ).count()

    failed_password_resets = queryset.filter(
        event_type=AuditLog.EventType.PASSWORD_RESET_FAILED
    ).count()

    unique_successful_users = (
        successful.exclude(user__isnull=True).values("user_id").distinct().count()
    )

    unique_failed_accounts = (
        failed.exclude(username_attempted="")
        .values("username_attempted")
        .distinct()
        .count()
    )

    # No names, emails, usernames, raw IP addresses, tokens,
    # user agents or audit metadata leave the backend.
    return {
        "period": period["period"],
        "successful_logins": successful.count(),
        "failed_logins": failed.count(),
        "password_reset_requests": queryset.filter(
            event_type__in=[
                AuditLog.EventType.PASSWORD_RESET_LINK_SENT,
                getattr(
                    AuditLog.EventType,
                    "PASSWORD_RESET_REQUESTED",
                    "PASSWORD_RESET_REQUESTED",
                ),
            ]
        ).count(),
        "completed_password_resets": completed_password_resets,
        "failed_password_resets": failed_password_resets,
        "blocked_default_password_logins": (blocked_default_password_logins),
        "unique_successful_users": unique_successful_users,
        "unique_failed_accounts": unique_failed_accounts,
        "highest_failures_for_one_account": (highest_failures_for_one_account),
        "highest_failures_from_one_ip": (highest_failures_from_one_ip),
        "accounts_with_repeated_failures": (accounts_with_repeated_failures),
        "accounts_using_multiple_ips": accounts_using_multiple_ips,
        "unusual_time_logins": unusual_time_logins,
        "failure_rate_percent": round(
            (failed.count() / max(successful.count() + failed.count(), 1)) * 100,
            2,
        ),
    }


class SecurityRecommendation(BaseModel):
    priority: Literal["low", "medium", "high", "critical"]
    title: str
    recommendation: str


class SecurityInsightResponse(BaseModel):
    risk_level: Literal["low", "medium", "high", "critical"]
    summary: str
    observations: list[str] = Field(default_factory=list)
    risk_indicators: list[str] = Field(default_factory=list)
    recommendations: list[SecurityRecommendation] = Field(default_factory=list)
    requires_immediate_review: bool
    disclaimer: str


def generate_ai_security_insight(range_name: str):
    if not settings.GOOGLE_AI_API_KEY:
        raise RuntimeError("GOOGLE_AI_API_KEY is not configured.")

    payload = calculate_ai_security_payload(range_name)

    client = genai.Client(api_key=settings.GOOGLE_AI_API_KEY)

    prompt = f"""
You are a cybersecurity analyst reviewing aggregate authentication
statistics for an internal administrative web application.

Analyse only the supplied aggregate statistics. Do not infer identities,
locations, passwords, IP addresses, vulnerabilities, attack attribution,
or facts that are not present in the data.

Your responsibilities:
1. Determine an overall risk level.
2. Explain notable patterns in plain professional language.
3. Identify security indicators that deserve review.
4. Provide proportionate, actionable recommendations.
5. Avoid alarmist claims.
6. Never request or reveal usernames, emails, IP addresses, reset tokens,
   passwords, user agents, or personal data.

Aggregate authentication summary:

{json.dumps(payload, indent=2)}
""".strip()

    response = client.models.generate_content(
        model=settings.GOOGLE_AI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SecurityInsightResponse,
            temperature=0.2,
        ),
    )

    insight = SecurityInsightResponse.model_validate_json(response.text)

    return {
        "range": range_name,
        "input_summary": payload,
        "insight": insight.model_dump(),
        "provider": "google_ai_studio",
        "model": settings.GOOGLE_AI_MODEL,
    }


def calculate_audit_ai_payload(range_name: str):
    period = get_summary_period(range_name)

    queryset = AuditLog.objects.filter(
        created_at__gte=period["date_from"],
        created_at__lte=period["date_to"],
    )

    create_count = queryset.filter(
        Q(action__iexact="create") | Q(event_type=AuditLog.EventType.CREATE)
    ).count()

    update_count = queryset.filter(
        Q(action__iexact="update")
        | Q(action__iexact="edit")
        | Q(event_type=AuditLog.EventType.UPDATE)
    ).count()

    delete_count = queryset.filter(
        Q(action__iexact="delete") | Q(event_type=AuditLog.EventType.DELETE)
    ).count()

    failed_count = queryset.filter(status=AuditLog.EventStatus.FAILED).count()

    unique_actors = (
        queryset.exclude(user__isnull=True).values("user_id").distinct().count()
    )

    unique_affected_users = (
        queryset.exclude(target_user__isnull=True)
        .values("target_user_id")
        .distinct()
        .count()
    )

    highest_events_by_one_actor = (
        queryset.exclude(user__isnull=True)
        .values("user_id")
        .annotate(count=Count("id"))
        .order_by("-count")
        .values_list("count", flat=True)
        .first()
        or 0
    )

    highest_events_on_one_resource = (
        queryset.exclude(resource="")
        .values("resource")
        .annotate(count=Count("id"))
        .order_by("-count")
        .values_list("count", flat=True)
        .first()
        or 0
    )

    deletion_actors = (
        queryset.filter(
            Q(action__iexact="delete") | Q(event_type=AuditLog.EventType.DELETE)
        )
        .exclude(user__isnull=True)
        .values("user_id")
        .distinct()
        .count()
    )

    failed_actors = (
        queryset.filter(status=AuditLog.EventStatus.FAILED)
        .exclude(user__isnull=True)
        .values("user_id")
        .distinct()
        .count()
    )

    total_events = queryset.count()

    return {
        "period": period["period"],
        "total_events": total_events,
        "create_count": create_count,
        "update_count": update_count,
        "delete_count": delete_count,
        "failed_count": failed_count,
        "unique_actors": unique_actors,
        "unique_affected_users": unique_affected_users,
        "highest_events_by_one_actor": highest_events_by_one_actor,
        "highest_events_on_one_resource": highest_events_on_one_resource,
        "actors_performing_deletions": deletion_actors,
        "actors_with_failed_actions": failed_actors,
        "delete_rate_percent": round(
            delete_count / max(total_events, 1) * 100,
            2,
        ),
        "failure_rate_percent": round(
            failed_count / max(total_events, 1) * 100,
            2,
        ),
    }


def generate_audit_ai_insight(range_name: str):
    if not settings.GOOGLE_AI_API_KEY:
        raise RuntimeError("GOOGLE_AI_API_KEY is not configured.")

    payload = calculate_audit_ai_payload(range_name)

    client = genai.Client(api_key=settings.GOOGLE_AI_API_KEY)

    prompt = f"""
You are a cybersecurity and governance analyst reviewing anonymised,
aggregate audit activity for an internal administration system.

Analyse only the aggregate statistics supplied.

Your responsibilities:
1. Assess the overall administrative and security risk.
2. Identify unusual concentrations of updates, deletions or failures.
3. Explain possible governance or operational concerns.
4. Recommend proportionate actions such as review, approval controls,
   permission reviews or staff training.
5. Do not infer identities, departments, locations or malicious intent.
6. State that aggregate statistics alone cannot confirm misconduct or
   compromise.
7. Do not request personal data, usernames, email addresses, IP addresses,
   tokens, passwords or detailed audit records.

Anonymous audit summary:

{json.dumps(payload, indent=2)}
""".strip()

    response = client.models.generate_content(
        model=settings.GOOGLE_AI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SecurityInsightResponse,
            temperature=0.2,
        ),
    )

    insight = SecurityInsightResponse.model_validate_json(response.text)

    return {
        "range": range_name,
        "input_summary": payload,
        "insight": insight.model_dump(),
        "provider": "google_ai_studio",
        "model": settings.GOOGLE_AI_MODEL,
    }
