from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit


_WHITESPACE_RE = re.compile(r"\s+")
_PHONE_RE = re.compile(r"[^0-9+]")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""

    normalized = unicodedata.normalize("NFKC", str(value))
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def normalize_party_name(value: str | None) -> str:
    return normalize_text(value).casefold()


def normalize_email(value: str | None) -> str:
    return normalize_text(value).casefold()


def normalize_phone(value: str | None) -> str:
    cleaned = _PHONE_RE.sub("", normalize_text(value))

    if cleaned.count("+") > 1:
        cleaned = cleaned.replace("+", "")

    if "+" in cleaned and not cleaned.startswith("+"):
        cleaned = cleaned.replace("+", "")

    return cleaned


def normalize_url(value: str | None) -> str:
    value = normalize_text(value)

    if not value:
        return ""

    candidate = value if "://" in value else f"https://{value}"

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return value.casefold()

    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").casefold()
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{hostname}{port}"
    path = parsed.path.rstrip("/") or ""

    return urlunsplit(
        (
            scheme,
            netloc,
            path,
            parsed.query,
            "",
        )
    )


def normalize_contact_value(
    method_type: str,
    value: str | None,
) -> str:
    method_type = (method_type or "").upper()

    if method_type == "EMAIL":
        return normalize_email(value)

    if method_type in {"PHONE", "MOBILE", "WHATSAPP"}:
        return normalize_phone(value)

    if method_type == "WEBSITE":
        return normalize_url(value)

    return normalize_text(value).casefold()
