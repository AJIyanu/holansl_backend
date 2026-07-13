"""Tests for the empty Ledger application placeholder."""

from django.apps import apps
from django.test import SimpleTestCase


class LedgerResetTests(SimpleTestCase):
    """Verify that no legacy Ledger models remain registered."""

    def test_ledger_has_no_models(self) -> None:
        """
        Confirm that the Ledger application has no models.

        Returns:
            None.
        """

        app_config = apps.get_app_config(
            "ledger",
        )

        self.assertEqual(
            list(app_config.get_models()),
            [],
        )
