import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework_simplejwt.tokens import RefreshToken

from notifications.services import send_sensitive_email

from .models import AuthenticationSession, LoginThrottle, MFAChallenge
from .utils import get_client_ip, get_user_agent


def login_email_is_allowed(user):
    email = (user.email or "").strip().casefold()
    if email.endswith("@holansl.com") and email.count("@") == 1:
        return True
    return user.is_superuser and email == "aderemijoseph@live.com"


def company_email_is_allowed(email):
    normalized = (email or "").strip().casefold()
    return normalized.endswith("@holansl.com") and normalized.count("@") == 1


def _key_hash(scope, value):
    return hashlib.sha256(f"{scope}:{value.casefold()}".encode()).hexdigest()


def throttle_is_blocked(*, email, ip_address):
    now = timezone.now()
    keys = [
        _key_hash("account", email),
        _key_hash("ip", ip_address or "unknown"),
    ]
    return LoginThrottle.objects.filter(
        key_hash__in=keys, blocked_until__gt=now
    ).exists()


@transaction.atomic
def record_login_failure(*, email, ip_address):
    now = timezone.now()
    for scope, value, limit, block_minutes in (
        ("account", email, 5, 15),
        ("ip", ip_address or "unknown", 20, 60),
    ):
        row, _ = LoginThrottle.objects.select_for_update().get_or_create(
            key_hash=_key_hash(scope, value), defaults={"scope": scope}
        )
        if row.window_started_at <= now - timedelta(minutes=15):
            row.failures = 0
            row.window_started_at = now
            row.blocked_until = None
        row.failures += 1
        if row.failures >= limit:
            row.blocked_until = now + timedelta(minutes=block_minutes)
        row.save()


def clear_account_throttle(email):
    LoginThrottle.objects.filter(key_hash=_key_hash("account", email)).delete()


def _otp_hash(challenge_id, code):
    return salted_hmac(
        "accounts.email_mfa", f"{challenge_id}:{code}", secret=settings.SECRET_KEY
    ).hexdigest()


def _send_otp(user, code):
    send_sensitive_email(
        destination=user.email,
        subject="Your HolanSL sign-in code",
        body_text=(
            f"Your HolanSL sign-in code is {code}. It expires in 5 minutes. "
            "If you did not attempt to sign in, contact your administrator."
        ),
        body_html=(
            "<p>Your HolanSL sign-in code is:</p>"
            f'<p style="font-size:28px;font-weight:700;letter-spacing:6px">{code}</p>'
            "<p>It expires in 5 minutes. If you did not attempt to sign in, "
            "contact your administrator.</p>"
        ),
    )


def create_email_challenge(user, request):
    now = timezone.now()
    MFAChallenge.objects.filter(
        user=user, consumed_at__isnull=True, expires_at__gt=now
    ).update(consumed_at=now)
    challenge = MFAChallenge.objects.create(
        user=user,
        code_hash="pending",
        expires_at=now + timedelta(minutes=5),
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge.code_hash = _otp_hash(challenge.id, code)
    try:
        _send_otp(user, code)
    except Exception:
        challenge.delete()
        raise
    challenge.save(update_fields=["code_hash"])
    return challenge


@transaction.atomic
def resend_email_challenge(challenge_id):
    now = timezone.now()
    challenge = (
        MFAChallenge.objects.select_for_update()
        .select_related("user")
        .filter(id=challenge_id, consumed_at__isnull=True, expires_at__gt=now)
        .first()
    )
    if (
        challenge is None
        or challenge.resend_count >= 3
        or challenge.last_sent_at > now - timedelta(seconds=30)
    ):
        return False
    code = f"{secrets.randbelow(1_000_000):06d}"
    _send_otp(challenge.user, code)
    challenge.code_hash = _otp_hash(challenge.id, code)
    challenge.last_sent_at = now
    challenge.expires_at = now + timedelta(minutes=5)
    challenge.attempts = 0
    challenge.resend_count += 1
    challenge.save(
        update_fields=[
            "code_hash",
            "last_sent_at",
            "expires_at",
            "attempts",
            "resend_count",
        ]
    )
    return True


@transaction.atomic
def verify_email_challenge(*, challenge_id, code, request):
    now = timezone.now()
    challenge = (
        MFAChallenge.objects.select_for_update()
        .select_related("user")
        .filter(id=challenge_id)
        .first()
    )
    if (
        challenge is None
        or challenge.consumed_at is not None
        or challenge.expires_at <= now
        or challenge.attempts >= 5
    ):
        return None, None
    challenge.attempts += 1
    valid = hmac.compare_digest(challenge.code_hash, _otp_hash(challenge.id, code))
    if not valid:
        challenge.save(update_fields=["attempts"])
        record_login_failure(
            email=challenge.user.email, ip_address=get_client_ip(request)
        )
        return None, None
    challenge.consumed_at = now
    challenge.save(update_fields=["attempts", "consumed_at"])
    session = AuthenticationSession.objects.create(
        user=challenge.user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        expires_at=now + timedelta(hours=24),
    )
    refresh = RefreshToken.for_user(challenge.user)
    refresh["sid"] = str(session.id)
    clear_account_throttle(challenge.user.email)
    return session, {"refresh": str(refresh), "access": str(refresh.access_token)}
