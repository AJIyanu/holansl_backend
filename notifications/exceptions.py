class NotificationError(Exception):
    """Base exception for the notification subsystem."""


class NotificationConfigurationError(NotificationError):
    """Required notification configuration is missing or invalid."""


class TemporaryDeliveryError(NotificationError):
    """A delivery failed and may succeed when retried."""

    def __init__(
        self,
        message,
        *,
        code="temporary_error",
        metadata=None,
    ):
        super().__init__(message)
        self.code = code
        self.metadata = metadata or {}


class PermanentDeliveryError(NotificationError):
    """A delivery should not be retried automatically."""

    def __init__(
        self,
        message,
        *,
        code="permanent_error",
        metadata=None,
    ):
        super().__init__(message)
        self.code = code
        self.metadata = metadata or {}
