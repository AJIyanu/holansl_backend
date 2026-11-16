"""Procurement application configuration."""

from django.apps import AppConfig


class ProcurementConfig(AppConfig):
    """Configure item requests and activity tracking."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "procurement"
    verbose_name = "Procurement"

    def ready(self):
        from . import signals  # noqa: F401
