import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
import logging

import resend
from django.utils import timezone

from .models import AuditLog, PasswordResetCode

logger = logging.getLogger(__name__)


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


def get_resend_sender():
    """
    Prefer the complete Resend sender value:
    'HolanSL Admin <noreply@holansl.com>'

    Fall back to EMAIL_HOST_USER when RESEND_FROM_EMAIL is not configured.
    """
    if settings.RESEND_FROM_EMAIL:
        return settings.RESEND_FROM_EMAIL

    if settings.EMAIL_HOST_USER:
        return (
            f"{settings.RESEND_FROM_NAME} "
            f"<{settings.EMAIL_HOST_USER}>"
        )

    raise RuntimeError(
        "RESEND_FROM_EMAIL or EMAIL_HOST_USER must be configured."
    )


def send_password_reset_email(
    user,
    raw_token,
    *,
    purpose=PasswordResetCode.Purpose.PASSWORD_RESET,
):
    if not settings.RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not configured.")

    resend.api_key = settings.RESEND_API_KEY

    reset_link = build_password_reset_link(raw_token)
    display_name = user.get_full_name() or user.username

    if purpose == PasswordResetCode.Purpose.DEFAULT_PASSWORD_CHANGE:
        subject = "Set your HolanSL Admin password"
        heading = "Set your account password"
        introduction = (
            "Your HolanSL Admin account requires a password change "
            "before you can sign in."
        )
    else:
        subject = "Reset your HolanSL Admin password"
        heading = "Reset your password"
        introduction = (
            "We received a request to reset the password for your "
            "HolanSL Admin account."
        )

    html = f"""
    <!doctype html>
    <html>
      <body style="font-family: Arial, sans-serif; color: #1f2937;">
        <div style="max-width: 600px; margin: 0 auto; padding: 24px;">
          <h2>{heading}</h2>

          <p>Hello {display_name},</p>

          <p>{introduction}</p>

          <p style="margin: 28px 0;">
            <a
              href="{reset_link}"
              style="
                display: inline-block;
                padding: 12px 20px;
                background: #F46C0B;
                color: #ffffff;
                text-decoration: none;
                border-radius: 6px;
              "
            >
              Reset password
            </a>
          </p>

          <p>
            This link expires in
            {settings.PASSWORD_RESET_EXPIRY_MINUTES} minutes
            and can only be used once.
          </p>

          <p>
            If you did not request this change, you may ignore this email
            or contact your administrator.
          </p>

          <p>HolanSL Admin</p>
        </div>
      </body>
    </html>
    """

    text = f"""
Hello {display_name},

{introduction}

Reset your password using this link:

{reset_link}

This link expires in {settings.PASSWORD_RESET_EXPIRY_MINUTES} minutes
and can only be used once.

If you did not request this change, you may ignore this email or
contact your administrator.

HolanSL Admin
""".strip()

    try:
        response = resend.Emails.send(
            {
                "from": get_resend_sender(),
                "to": [user.email],
                "subject": subject,
                "html": html,
                "text": text,
            }
        )
    except Exception:
        logger.exception(
            "Resend failed to send password reset email to user %s",
            user.pk,
        )
        raise

    return {
        "reset_link": reset_link,
        "email_id": response.get("id") if response else None,
    }