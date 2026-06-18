import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import AuditLog, PasswordResetCode


def get_client_ip(request):
    if not request:
        return None

    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")

    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR")


def get_user_agent(request):
    if not request:
        return ""

    return request.META.get("HTTP_USER_AGENT", "")


def hash_reset_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_audit_log(
    *,
    user=None,
    target_user=None,
    event_category=AuditLog.EventCategory.SYSTEM,
    event_type,
    status=AuditLog.EventStatus.SUCCESS,
    username_attempted="",
    app_label="",
    resource="",
    action="",
    object_id="",
    request=None,
    metadata=None,
):
    return AuditLog.objects.create(
        user=user,
        target_user=target_user,
        event_category=event_category,
        event_type=event_type,
        status=status,
        username_attempted=username_attempted or "",
        app_label=app_label or "",
        resource=resource or "",
        action=action or "",
        object_id=str(object_id) if object_id else "",
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        metadata=metadata or {},
    )


def create_password_reset_code(user, request=None, purpose=None):
    """
    Creates one active password reset token for a user.
    Old unused tokens for that user are deleted first.
    Raw token is returned only once so it can be emailed.
    """

    if purpose is None:
        purpose = PasswordResetCode.Purpose.DEFAULT_PASSWORD_CHANGE

    PasswordResetCode.objects.filter(
        user=user,
        used_at__isnull=True,
    ).delete()

    raw_token = secrets.token_urlsafe(48)
    token_hash = hash_reset_token(raw_token)

    reset_code = PasswordResetCode.objects.create(
        user=user,
        token_hash=token_hash,
        purpose=purpose,
        expires_at=timezone.now()
        + timedelta(minutes=settings.PASSWORD_RESET_EXPIRY_MINUTES),
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )

    return reset_code, raw_token


def get_valid_password_reset_code(raw_token):
    token_hash = hash_reset_token(raw_token)

    reset_code = (
        PasswordResetCode.objects.select_related("user")
        .filter(
            token_hash=token_hash,
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .first()
    )

    return reset_code


def build_password_reset_link(raw_token):
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    return f"{frontend_url}/resetpassword?code={raw_token}"


def send_password_reset_email(user, raw_token):
    reset_link = build_password_reset_link(raw_token)

    subject = "Set your HolanSL Admin password"

    message = f"""
Hello {user.get_full_name() or user.username},

Your HolanSL Admin account requires a password change before you can login.

Please use the link below to set your password:

{reset_link}

This link expires in {settings.PASSWORD_RESET_EXPIRY_MINUTES} minutes.

If you did not request this, please contact the administrator.

HolanSL Admin
"""

    send_mail(
        subject=subject,
        message=message.strip(),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )

    return reset_link