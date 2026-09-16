"""Exercise real PDF/JPG bytes and mocked private storage, without online access."""

from io import BytesIO
from unittest.mock import patch
import pypdfium2 as pdfium
from PIL import Image
from django.test import SimpleTestCase, TestCase, override_settings
from crm.storage.base import StoredDocument
from .document_layout import render_pdf
from . import rendering, models as m
from . import test_document_registry as registry_tests

COMPANY = {
    "address": "Current Office, New Street, Ibadan",
    "website": "www.holansl.com",
    "email": "operations@holansl.com",
    "phone": "",
}


class LayoutTests(SimpleTestCase):
    def test_all_sample_layouts_and_dynamic_address(self):
        for kind in ["QT", "WB", "IN"]:
            snapshot = dict(
                kind=kind,
                number=f"Hol-TEST-{kind}-2026-00001",
                client="Test client",
                date="2026-09-04",
                lines=[
                    dict(
                        description="Test <item>",
                        quantity="2",
                        unit_price="7.50",
                        unit="pieces",
                    )
                ],
            )
            content = render_pdf(snapshot, company=COMPANY, draft=True)
            with pdfium.PdfDocument(content) as doc:
                text = doc[0].get_textpage().get_text_range()
                self.assertIn(COMPANY["address"], text)
                self.assertIn("Test <item>", text)
                self.assertIn("DRAFT", text)
                self.assertNotIn("Damin Plaza", text)
                self.assertNotIn("Ida Avenue", text)
                if kind != "WB":
                    self.assertIn("15.00", text)

    def test_long_documents_repeat_headers(self):
        snapshot = dict(
            kind="WB",
            number="Hol-TEST-WB-2026-00001",
            client="Test",
            date="2026-09-04",
            lines=[
                dict(description=f"Product {i}", quantity="1", unit="piece")
                for i in range(90)
            ],
        )
        with pdfium.PdfDocument(render_pdf(snapshot, company=COMPANY)) as doc:
            self.assertGreater(len(doc), 1)
            self.assertIn("DESCRIPTION", doc[1].get_textpage().get_text_range())


@override_settings(PROCUREMENT_DOCUMENT_COMPANY=COMPANY)
class GenerationTests(TestCase):
    doc = registry_tests.DocumentRegistryTests.doc
    offer = registry_tests.DocumentRegistryTests.offer
    po = registry_tests.DocumentRegistryTests.po
    purchase = registry_tests.DocumentRegistryTests.purchase
    receive_deliver = registry_tests.DocumentRegistryTests.receive_deliver
    post = registry_tests.DocumentRegistryTests.post
    quote_document = registry_tests.DocumentRegistryTests.quote_document
    setUp = registry_tests.DocumentRegistryTests.setUp

    @classmethod
    def setUpTestData(cls):
        registry_tests.DocumentRegistryTests.setUpTestData.__func__(cls)

    def test_real_pdf_and_jpg_retries_keep_assets(self):
        version = self.quote_document()
        captured = []

        def upload(**kwargs):
            captured.append(kwargs["file_object"].read())
            return StoredDocument(
                provider="supabase",
                external_file_id=str(kwargs["document_id"]),
                storage_path=kwargs["filename"],
            )

        with patch("procurement.rendering.get_document_storage") as storage:
            storage.return_value.upload.side_effect = upload
            for format in ["PDF", "JPG"]:
                rendering.generate(self.user, version, dict(format=format, draft=True))
                rendering.generate(self.user, version, dict(format=format, draft=True))
            self.assertEqual(storage.return_value.upload.call_count, 2)
        self.assertTrue(captured[0].startswith(b"%PDF"))
        self.assertEqual(Image.open(BytesIO(captured[1])).format, "JPEG")
        self.assertEqual(m.DocumentAsset.objects.count(), 2)

    @override_settings(PROCUREMENT_DOCUMENT_COMPANY={**COMPANY, "address": ""})
    def test_missing_current_address_blocks_official_waybill(self):
        attempt, offer = self.po()
        delivery = self.receive_deliver(self.purchase(attempt, offer))
        from .document_registry import prepare

        version = prepare(
            self.user, dict(request=self.request, kind="WB", delivery=delivery)
        )
        with self.assertRaises(rendering.TemplateUnavailable):
            rendering.generate(self.user, version, dict(format="PDF", draft=False))
        self.assertFalse(m.DocumentAsset.objects.exists())

    def test_official_waybill_attaches_pdf_and_keeps_previous_address_asset(self):
        from .document_registry import prepare

        attempt, offer = self.po()
        delivery = self.receive_deliver(self.purchase(attempt, offer))
        delivery.waybill = None
        delivery.dispatched_at = None
        delivery.save(update_fields=["waybill", "dispatched_at"])
        version = prepare(
            self.user, dict(request=self.request, kind="WB", delivery=delivery)
        )
        with patch("procurement.rendering.get_document_storage") as storage:
            storage.return_value.upload.side_effect = lambda **kwargs: StoredDocument(
                provider="supabase",
                external_file_id=str(kwargs["document_id"]),
                storage_path=kwargs["filename"],
            )
            rendering.generate(self.user, version, dict(format="PDF", draft=False))
            with override_settings(
                PROCUREMENT_DOCUMENT_COMPANY={
                    **COMPANY,
                    "address": "Another current office",
                }
            ):
                rendering.generate(self.user, version, dict(format="PDF", draft=False))
        delivery.refresh_from_db()
        self.assertIsNotNone(delivery.waybill_id)
        self.assertEqual(version.assets.count(), 2)

    def test_storage_failure_does_not_leave_database_assets(self):
        from crm.storage import DocumentStorageError
        from .documents import StorageUnavailable

        version = self.quote_document()
        with patch("procurement.rendering.get_document_storage") as storage:
            storage.return_value.upload.side_effect = DocumentStorageError(
                "unavailable"
            )
            with self.assertRaises(StorageUnavailable):
                rendering.generate(self.user, version, dict(format="PDF", draft=True))
        self.assertFalse(version.assets.exists())

    def test_draft_waybill_cannot_be_dispatch_evidence(self):
        from rest_framework.exceptions import ValidationError
        from .document_registry import prepare
        from . import services

        attempt, offer = self.po()
        delivery = self.receive_deliver(self.purchase(attempt, offer))
        version = prepare(
            self.user, dict(request=self.request, kind="WB", delivery=delivery)
        )
        with patch("procurement.rendering.get_document_storage") as storage:
            storage.return_value.upload.side_effect = lambda **kwargs: StoredDocument(
                provider="supabase",
                external_file_id=str(kwargs["document_id"]),
                storage_path=kwargs["filename"],
            )
            rendering.generate(self.user, version, dict(format="PDF", draft=True))
        delivery.waybill = version.assets.get().document
        delivery.dispatched_at = None
        delivery.save(update_fields=["waybill", "dispatched_at"])
        with self.assertRaisesMessage(ValidationError, "draft waybill"):
            services.dispatch(self.user, delivery)


class PrintOptionTests(SimpleTestCase):
    def test_charge_rows_are_independent_and_do_not_change_total(self):
        snapshot = dict(
            kind="QT",
            number="Hol-TEST-QT-2026-00001",
            client="Test",
            date="2026-09-04",
            lines=[
                dict(
                    description="Item",
                    quantity="1",
                    unit_price="100",
                    logistics="20",
                    additional_charges="5",
                )
            ],
        )
        for logistics, additional in [
            (False, False),
            (True, False),
            (False, True),
            (True, True),
        ]:
            raw = render_pdf(
                snapshot,
                company=COMPANY,
                show_logistics=logistics,
                show_additional_charges=additional,
            )
            with pdfium.PdfDocument(raw) as doc:
                page = doc[0]
                textpage = page.get_textpage()
                text = textpage.get_text_range()
                self.assertEqual("Logistics" in text, logistics)
                self.assertEqual("Additional Charges" in text, additional)
                self.assertIn("125.00", text)
                textpage.close()
                page.close()
