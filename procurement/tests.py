"""Tests for the empty Procurement application placeholder."""

from django.apps import apps
from django.test import SimpleTestCase


class ProcurementResetTests(SimpleTestCase):
    """Verify that no legacy Procurement models remain registered."""

    def test_procurement_has_no_models(self) -> None:
        """
        Confirm that the Procurement application has no models.

        Returns:
            None.
        """

        app_config = apps.get_app_config(
            "procurement",
        )

        self.assertEqual(
            list(app_config.get_models()),
            [],
        )
