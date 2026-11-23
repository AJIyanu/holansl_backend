from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from django.utils import timezone

from .models import AuthenticationSession


class BlacklistAwareJWTAuthentication(JWTAuthentication):
    """Reject access tokens whose JWT IDs were revoked during logout."""

    def get_validated_token(self, raw_token):
        validated_token = super().get_validated_token(raw_token)
        jti = validated_token.get(api_settings.JTI_CLAIM)

        if jti and BlacklistedToken.objects.filter(token__jti=jti).exists():
            raise AuthenticationFailed(
                "Token has been revoked.",
                code="token_revoked",
            )

        session_id = validated_token.get("sid")
        if not session_id:
            raise AuthenticationFailed("Session is invalid.", code="session_invalid")
        if not AuthenticationSession.objects.filter(
            id=session_id,
            user_id=validated_token.get(api_settings.USER_ID_CLAIM),
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).exists():
            raise AuthenticationFailed("Session has expired.", code="session_expired")

        return validated_token
