"""Ledger Django application configuration."""

from django.apps import AppConfig


class LedgerConfig(AppConfig):
    """Configure the empty Ledger application placeholder."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "ledger"
    verbose_name = "Ledger"
