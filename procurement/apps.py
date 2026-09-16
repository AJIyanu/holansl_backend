"""Procurement Django application configuration."""

from django.apps import AppConfig


class ProcurementConfig(AppConfig):
    """Configure item-level procurement and fulfilment tracking."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "procurement"
    verbose_name = "Procurement"
