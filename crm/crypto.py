"""
Encryption and deterministic lookup helpers for sensitive CRM values.

The module encrypts confidential database fields using Fernet and creates
keyed HMAC lookup values for exact duplicate detection without storing
searchable plaintext.
"""

from __future__ import annotations

import hashlib
import hmac
import unicodedata
from functools import lru_cache

from cryptography.fernet import (
    Fernet,
    InvalidToken,
    MultiFernet,
)
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@lru_cache(maxsize=1)
def get_sensitive_cipher() -> MultiFernet:
    """
    Build the configured CRM field-encryption cipher.

    Returns:
        MultiFernet: Cipher using the configured keys. The first key is used
        for new encryption and all supplied keys may decrypt existing values.

    Raises:
        ImproperlyConfigured: If no encryption key is configured or a key is
        not a valid Fernet key.
    """

    keys = getattr(
        settings,
        "CRM_FIELD_ENCRYPTION_KEYS",
        [],
    )

    if not keys:
        raise ImproperlyConfigured(
            "CRM_FIELD_ENCRYPTION_KEYS must contain at least one valid Fernet key."
        )

    try:
        fernets = [Fernet(key.encode("utf-8")) for key in keys]
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured(
            "CRM_FIELD_ENCRYPTION_KEYS contains an invalid Fernet key."
        ) from exc

    return MultiFernet(fernets)


def encrypt_sensitive_value(value: str) -> str:
    """
    Encrypt a sensitive CRM string.

    Args:
        value: Plaintext value to encrypt.

    Returns:
        str: URL-safe encrypted token suitable for database storage.

    Raises:
        ValueError: If the supplied value is empty.
        ImproperlyConfigured: If encryption keys are unavailable.
    """

    plaintext = str(value or "").strip()

    if not plaintext:
        raise ValueError("A non-empty sensitive value is required.")

    token = get_sensitive_cipher().encrypt(
        plaintext.encode("utf-8"),
    )

    return token.decode("utf-8")


def decrypt_sensitive_value(token: str) -> str:
    """
    Decrypt a CRM encrypted token.

    Args:
        token: Fernet token stored in the database.

    Returns:
        str: Decrypted plaintext value.

    Raises:
        ValueError: If the token is empty or cannot be decrypted.
        ImproperlyConfigured: If encryption keys are unavailable.
    """

    if not token:
        raise ValueError("An encrypted value is required.")

    try:
        plaintext = get_sensitive_cipher().decrypt(
            token.encode("utf-8"),
        )
    except InvalidToken as exc:
        raise ValueError("The encrypted CRM value could not be decrypted.") from exc

    return plaintext.decode("utf-8")


def normalize_sensitive_value(value: str) -> str:
    """
    Normalise an identifier for exact keyed lookup.

    Args:
        value: Identifier, account number, IBAN or similar value.

    Returns:
        str: Uppercase alphanumeric representation with formatting removed.
    """

    normalized = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    )

    return "".join(character for character in normalized.upper() if character.isalnum())


def hash_sensitive_value(value: str) -> str:
    """
    Create a deterministic keyed digest for exact duplicate checks.

    Args:
        value: Plaintext or already normalised sensitive value.

    Returns:
        str: SHA-256 HMAC digest.

    Raises:
        ValueError: If the supplied value is empty.
        ImproperlyConfigured: If CRM_SENSITIVE_HASH_KEY is unavailable.
    """

    normalized = normalize_sensitive_value(value)

    if not normalized:
        raise ValueError("A non-empty sensitive value is required.")

    secret = getattr(
        settings,
        "CRM_SENSITIVE_HASH_KEY",
        "",
    )

    if not secret:
        raise ImproperlyConfigured("CRM_SENSITIVE_HASH_KEY must be configured.")

    return hmac.new(
        secret.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def sensitive_value_last_four(value: str) -> str:
    """
    Return the final four normalised characters of a sensitive value.

    Args:
        value: Plaintext sensitive value.

    Returns:
        str: Up to four trailing characters.
    """

    normalized = normalize_sensitive_value(value)
    return normalized[-4:]


def mask_sensitive_value(
    last_four: str,
) -> str:
    """
    Build a non-sensitive display value.

    Args:
        last_four: Final characters stored separately from encrypted data.

    Returns:
        str: Masked value such as ``••••1234`` or an empty string.
    """

    if not last_four:
        return ""

    return f"••••{last_four}"
