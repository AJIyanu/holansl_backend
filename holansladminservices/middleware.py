import logging

logger = logging.getLogger(__name__)

def header_debug_middleware(get_response):
    def middleware(request):
        # Log request method, path, and headers
        logger.warning("🔍 DEBUG HEADERS START 🔍")
        logger.warning(f"Method: {request.method} | Path: {request.path}")
        for k, v in request.headers.items():
            logger.warning(f"{k}: {v}")
        logger.warning("🔍 DEBUG HEADERS END 🔍\n")

        response = get_response(request)

        # Optionally log response status
        logger.warning(f"Response Status: {response.status_code}\n")

        return response
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
