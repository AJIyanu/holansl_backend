"""Ledger Django application configuration."""

from django.apps import AppConfig


class LedgerConfig(AppConfig):
    """Register the ledger and its schema integration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "ledger"
    verbose_name = "Ledger"

    def ready(self):
        from . import schema  # noqa: F401
