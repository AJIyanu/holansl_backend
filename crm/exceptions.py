"""
CRM API exception definitions.

This module contains reusable API exceptions returned when a CRM operation
cannot be completed because it conflicts with existing business records.

Exception classes accept an optional response detail and return a structured
Django REST Framework error response.
"""

from rest_framework.exceptions import APIException


class CRMConflict(APIException):
    """
    Represent a CRM operation that conflicts with existing business data.

    Accepts:
        An optional error detail and optional machine-readable error code.

    Returns:
        An HTTP 409 response when raised by a Django REST Framework view.
    """

    status_code = 409
    default_detail = "The requested CRM operation conflicts with existing records."
    default_code = "crm_conflict"
