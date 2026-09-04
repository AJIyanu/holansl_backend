from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken


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

        return validated_token
