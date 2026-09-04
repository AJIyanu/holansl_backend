from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import TokenError, UntypedToken


class LogoutTokenError(ValueError):
    pass


def _validated_payload(raw_token, expected_type):
    try:
        token = UntypedToken(raw_token)
    except TokenError as exc:
        raise LogoutTokenError("Token is invalid or expired.") from exc

    token_type_claim = api_settings.TOKEN_TYPE_CLAIM
    if token.get(token_type_claim) != expected_type:
        raise LogoutTokenError(f"Expected a {expected_type} token.")

    return token


def _resolve_user(access_token, refresh_token):
    user_id_claim = api_settings.USER_ID_CLAIM
    access_user_id = access_token.get(user_id_claim)
    refresh_user_id = refresh_token.get(user_id_claim)

    if not access_user_id or access_user_id != refresh_user_id:
        raise LogoutTokenError("Access and refresh tokens do not match.")

    User = get_user_model()
    try:
        return User.objects.get(
            **{api_settings.USER_ID_FIELD: access_user_id},
        )
    except User.DoesNotExist as exc:
        raise LogoutTokenError("Token user does not exist.") from exc


def _blacklist_token(token, user):
    jti = token[api_settings.JTI_CLAIM]
    expires_at = datetime.fromtimestamp(token["exp"], tz=UTC)
    issued_at = token.get("iat")
    created_at = datetime.fromtimestamp(issued_at, tz=UTC) if issued_at else None

    outstanding_token, _ = OutstandingToken.objects.get_or_create(
        jti=jti,
        defaults={
            "user": user,
            # The blacklist lookup needs the JTI, not the reusable raw token.
            "token": "",
            "created_at": created_at,
            "expires_at": expires_at,
        },
    )
    BlacklistedToken.objects.get_or_create(token=outstanding_token)


@transaction.atomic
def revoke_token_pair(*, access, refresh):
    access_token = _validated_payload(access, "access")
    refresh_token = _validated_payload(refresh, "refresh")
    user = _resolve_user(access_token, refresh_token)

    _blacklist_token(access_token, user)
    _blacklist_token(refresh_token, user)

    return user
