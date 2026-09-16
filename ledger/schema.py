"""Document the existing blacklist-aware JWT authentication without changing it."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class BlacklistJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "accounts.authentication.BlacklistAwareJWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
