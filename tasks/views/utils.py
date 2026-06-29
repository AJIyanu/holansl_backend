from django.core.exceptions import (
    ValidationError as DjangoValidationError,
)
from rest_framework.exceptions import (
    ValidationError as DRFValidationError,
)

from tasks.constants import TaskActivityType


def raise_api_validation_error(exc):
    if isinstance(exc, DjangoValidationError):
        if hasattr(exc, "message_dict"):
            raise DRFValidationError(exc.message_dict) from exc

        if hasattr(exc, "messages"):
            raise DRFValidationError(exc.messages) from exc

    raise exc


def validate_activity_type(value):
    if value in (None, ""):
        return None

    value = value.strip().upper()

    valid_values = {choice for choice, _label in TaskActivityType.choices}

    if value not in valid_values:
        raise DRFValidationError({"activity_type": ("Unsupported task activity type.")})

    return value
