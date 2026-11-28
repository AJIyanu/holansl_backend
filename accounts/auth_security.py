import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.html import escape
from rest_framework_simplejwt.tokens import RefreshToken

from notifications.services import send_sensitive_email

from .client_context import distance_km, get_trusted_client_context
from .models import AuthenticationSession, LoginThrottle, MFAChallenge
from .utils import get_client_ip


def login_email_is_allowed(user):
    email = (user.email or "").strip().casefold()
    if email.endswith("@holansl.com") and email.count("@") == 1:
        return True
    return user.is_superuser and email == "aderemijoseph@live.com"


def trusted_context(request):
    return get_trusted_client_context(
        request, required=settings.APP_ENV == "production"
    )


def client_ip_for_request(request):
    context = trusted_context(request)
    return context.ip_address or get_client_ip(request)


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
        was_blocked = row.blocked_until is not None and row.blocked_until > now
        if row.window_started_at <= now - timedelta(minutes=15):
            row.failures = 0
            row.window_started_at = now
            row.blocked_until = None
        row.failures += 1
        if row.failures >= limit:
            row.blocked_until = now + timedelta(minutes=block_minutes)
        row.save()
        if scope == "account" and row.blocked_until and not was_blocked:
            transaction.on_commit(
                lambda address=email: _send_login_attack_alert(address)
            )


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
    context = trusted_context(request)
    MFAChallenge.objects.filter(
        user=user, consumed_at__isnull=True, expires_at__gt=now
    ).update(consumed_at=now)
    challenge = MFAChallenge.objects.create(
        user=user,
        code_hash="pending",
        expires_at=now + timedelta(minutes=5),
        ip_address=context.ip_address,
        user_agent=context.user_agent,
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


def create_managed_session_tokens(user, request):
    now = timezone.now()
    context = trusted_context(request)
    session = AuthenticationSession.objects.create(
        user=user,
        ip_address=context.ip_address,
        user_agent=context.user_agent,
        device_hash=context.device_hash,
        country_code=context.country_code,
        region=context.region,
        city=context.city,
        latitude=context.latitude,
        longitude=context.longitude,
        expires_at=now + timedelta(hours=24),
    )
    refresh = RefreshToken.for_user(user)
    refresh["sid"] = str(session.id)
    clear_account_throttle(user.email)
    return session, {"refresh": str(refresh), "access": str(refresh.access_token)}


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
    context = trusted_context(request)
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
        record_login_failure(email=challenge.user.email, ip_address=context.ip_address)
        return None, None
    challenge.consumed_at = now
    challenge.save(update_fields=["attempts", "consumed_at"])
    return create_managed_session_tokens(challenge.user, request)


def _send_security_alert(session, reason):
    location = (
        ", ".join(
            value
            for value in (session.city, session.region, session.country_code)
            if value
        )
        or "an unknown location"
    )
    subject = "HolanSL security alert: session revoked"
    body = (
        f"A HolanSL session was revoked because of {reason}. "
        f"The latest approximate location was {location}. If this was not you, "
        "reset your password and contact your administrator."
    )
    recipients = [session.user.email, *settings.SECURITY_ALERT_EMAILS]
    for destination in dict.fromkeys(recipients):
        try:
            send_sensitive_email(
                destination=destination,
                subject=subject,
                body_text=body,
                body_html=f"<p>{escape(body)}</p>",
            )
        except Exception:
            continue


def _send_login_attack_alert(email):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(email__iexact=email, is_active=True).first()
    if not user or not login_email_is_allowed(user):
        return
    body = (
        "Repeated unsuccessful sign-in attempts temporarily blocked your HolanSL "
        "account. If these attempts were not yours, reset your password and contact "
        "your administrator."
    )
    for destination in dict.fromkeys([user.email, *settings.SECURITY_ALERT_EMAILS]):
        try:
            send_sensitive_email(
                destination=destination,
                subject="HolanSL security alert: repeated sign-in attempts",
                body_text=body,
                body_html=f"<p>{escape(body)}</p>",
            )
        except Exception:
            continue


@transaction.atomic
def validate_session_context(session, request):
    now = timezone.now()
    if settings.APP_ENV == "development":
        session.last_seen_at = now
        session.save(update_fields=["last_seen_at"])
        return True

    context = trusted_context(request)
    session = (
        AuthenticationSession.objects.select_for_update()
        .select_related("user")
        .get(pk=session.pk)
    )
    reason = ""
    if session.device_hash and context.device_hash != session.device_hash:
        reason = "a material device or browser change"
    elif (
        session.country_code
        and context.country_code
        and session.country_code != context.country_code
    ):
        elapsed_hours = max((now - session.last_seen_at).total_seconds() / 3600, 0.01)
        distance = distance_km(
            session.latitude,
            session.longitude,
            context.latitude,
            context.longitude,
        )
        if (distance is None and elapsed_hours < 2) or (
            distance is not None and distance >= 500 and distance / elapsed_hours > 1000
        ):
            reason = "impossible travel"

    if reason:
        session.revoked_at = now
        session.revoke_reason = "session_risk"
        session.risk_status = "REVOKED"
        session.risk_reason = reason
        session.save(
            update_fields=[
                "revoked_at",
                "revoke_reason",
                "risk_status",
                "risk_reason",
            ]
        )
        transaction.on_commit(lambda: _send_security_alert(session, reason))
        return False

    session.ip_address = context.ip_address
    session.user_agent = context.user_agent
    session.device_hash = context.device_hash
    session.country_code = context.country_code or session.country_code
    session.region = context.region or session.region
    session.city = context.city or session.city
    session.latitude = context.latitude or session.latitude
    session.longitude = context.longitude or session.longitude
    session.last_seen_at = now
    session.save(
        update_fields=[
            "ip_address",
            "user_agent",
            "device_hash",
            "country_code",
            "region",
            "city",
            "latitude",
            "longitude",
            "last_seen_at",
        ]
    )
    return True
