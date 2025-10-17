import logging

logger = logging.getLogger(__name__)

def header_debug_middleware(get_response):
    def middleware(request):
        def safe_value(key, value):
            if key.lower() in ("authorization", "x-auth-token"):
                return value[:10] + "...[redacted]"
            return value

        print("🔍 DEBUG HEADERS START 🔍")
        for k, v in request.headers.items():
            print(f"{k}: {safe_value(k, v)}")
        print("🔍 DEBUG HEADERS END 🔍\n")

        return get_response(request)
    return middleware


def auth_header_fallback_middleware(get_response):
    """
    If Cloudflare or any proxy strips the standard Authorization header,
    this middleware restores it from X-Auth-Token if available.
    """
    def middleware(request):
        if 'HTTP_AUTHORIZATION' not in request.META:
            alt_token = request.META.get('HTTP_X_AUTH_TOKEN')
            if alt_token:
                request.META['HTTP_AUTHORIZATION'] = alt_token
        return get_response(request)

    return middleware
