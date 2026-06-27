from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .constants import NotificationProcessingMode


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"
    verbose_name = "Notifications"

    def ready(self):
        mode = getattr(
            settings,
            "NOTIFICATION_PROCESSING_MODE",
            NotificationProcessingMode.INLINE,
        )

        valid_modes = {choice for choice, _label in NotificationProcessingMode.choices}

        if mode not in valid_modes:
            raise ImproperlyConfigured(
                "NOTIFICATION_PROCESSING_MODE must be one of: "
                f"{', '.join(sorted(valid_modes))}."
            )
