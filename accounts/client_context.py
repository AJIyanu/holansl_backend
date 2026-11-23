import base64
import hashlib
import hmac
import ipaddress
import json
import math
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed


@dataclass(frozen=True)
class TrustedClientContext:
    ip_address: str | None
    user_agent: str
    device_hash: str
    country_code: str
    region: str
    city: str
    latitude: Decimal | None
    longitude: Decimal | None


def _decimal(value, minimum, maximum):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if minimum <= result <= maximum else None


def _ip(value):
    try:
        return str(ipaddress.ip_address(str(value)))
    except ValueError:
        return None


def _normalized_device(user_agent):
    value = (user_agent or "unknown").strip().casefold()
    # Ignore patch-version digits while retaining browser/OS family changes.
    normalized = re.sub(r"\d+(?:\.\d+)*", "#", value)
    return hashlib.sha256(normalized.encode()).hexdigest()


def get_trusted_client_context(request, *, required=False):
    encoded = request.META.get("HTTP_X_HOLAN_CLIENT_CONTEXT", "") if request else ""
    signature = request.META.get("HTTP_X_HOLAN_CLIENT_SIGNATURE", "") if request else ""
    secret = settings.AUTH_CONTEXT_SIGNING_KEY

    if not encoded or not signature or not secret:
        if required:
            raise AuthenticationFailed(
                "Trusted client context is required.", code="client_context_required"
            )
        user_agent = request.META.get("HTTP_USER_AGENT", "") if request else ""
        return TrustedClientContext(
            ip_address=None,
            user_agent=user_agent,
            device_hash=_normalized_device(user_agent),
            country_code="",
            region="",
            city="",
            latitude=None,
            longitude=None,
        )

    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AuthenticationFailed(
            "Trusted client context is invalid.", code="client_context_invalid"
        )
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        timestamp = int(payload["ts"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise AuthenticationFailed(
            "Trusted client context is invalid.", code="client_context_invalid"
        ) from None
    if abs(int(time.time()) - timestamp) > settings.AUTH_CONTEXT_MAX_AGE_SECONDS:
        raise AuthenticationFailed(
            "Trusted client context has expired.", code="client_context_expired"
        )

    user_agent = str(payload.get("ua", ""))[:1000]
    return TrustedClientContext(
        ip_address=_ip(payload.get("ip")),
        user_agent=user_agent,
        device_hash=_normalized_device(user_agent),
        country_code=str(payload.get("country") or "").upper()[:2],
        region=str(payload.get("region") or "")[:100],
        city=str(payload.get("city") or "")[:150],
        latitude=_decimal(payload.get("lat"), Decimal("-90"), Decimal("90")),
        longitude=_decimal(payload.get("lon"), Decimal("-180"), Decimal("180")),
    )


def distance_km(lat1, lon1, lat2, lon2):
    values = [lat1, lon1, lat2, lon2]
    if any(value is None for value in values):
        return None
    first_lat, first_lon, second_lat, second_lon = map(
        lambda value: math.radians(float(value)), values
    )
    delta_lat = second_lat - first_lat
    delta_lon = second_lon - first_lon
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(first_lat) * math.cos(second_lat) * math.sin(delta_lon / 2) ** 2
    )
    return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
