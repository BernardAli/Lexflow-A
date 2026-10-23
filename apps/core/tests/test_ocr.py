import io
import subprocess
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from pypdf import PdfReader, PdfWriter

from apps.core.testing import make_office, property_matter, reset_vault
from apps.core.models import Membership
from apps.matters import services
from apps.matters.engine import ocr
from apps.matters.engine.text import extract_text


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ScanControlsTests(TestCase):
    def setUp(self):
        reset_vault()
        self.office, self.users = make_office()

    def test_mixed_pdf_never_hides_unread_pages(self):
        matter = property_matter(self.office, blatt_contract="812")
        reader = PdfReader(io.BytesIO(matter.message.files.first().read_bytes()))
        writer = PdfWriter()
        writer.add_page(reader.pages[0])
        writer.add_blank_page(width=595, height=842)
        content = io.BytesIO()
        writer.write(content)
        with override_settings(LEXFLOW_OCR_ENABLED=False):
            text, method, count = extract_text("mixed.pdf", "application/pdf", content.getvalue())
        self.assertTrue(text)
        self.assertEqual((method, count), ("needs_ocr", 2))

    def test_ocr_failure_keeps_original_for_manual_review(self):
        with override_settings(LEXFLOW_OCR_ENABLED=True), mock.patch.object(ocr, "image_text", side_effect=subprocess.TimeoutExpired("tesseract", 1)):
            text, method, count = extract_text("scan.png", "image/png", b"sample")
        self.assertEqual((text, method, count), ("", "needs_ocr", 1))

    def test_multiframe_image_cannot_silently_drop_unread_frame(self):
        with override_settings(LEXFLOW_OCR_ENABLED=True), mock.patch.object(ocr, "image_text", return_value=["Käufer: Anna Schmidt", ""]):
            text, method, count = extract_text("scan.tiff", "image/tiff", b"sample")
        self.assertIn("Anna", text)
        self.assertEqual((method, count), ("ocr_partial", 2))

    def test_scan_review_blocks_approval_until_acknowledged(self):
        matter = property_matter(self.office, blatt_contract="812")
        source = matter.message.files.first()
        source.text_method = "pdf_ocr"
        source.save(update_fields=["text_method"])
        services.confirm_field(matter.fields.get(definition__key="purchase_price"), self.users["clerk"])
        with self.assertRaises(ValidationError):
            services.approve(matter, Membership.objects.get(tenant=self.office, user=self.users["notary"]))
        self.client.force_login(self.users["clerk"])
        self.assertEqual(self.client.post(reverse("matters:acknowledge_source", args=[matter.ref, source.pk]),
                                         {"reviewed": "yes"}).status_code, 302)
        matter.refresh_from_db()
        services.approve(matter, Membership.objects.get(tenant=self.office, user=self.users["notary"]))
        self.assertTrue(matter.is_locked)

    def test_pixel_limit_is_enforced_before_starting_ocr(self):
        image = io.BytesIO()
        Image.new("RGB", (200, 200), "white").save(image, format="PNG")
        with override_settings(LEXFLOW_OCR_MAX_PIXELS=100):
            with mock.patch.object(ocr, "_run") as run:
                with self.assertRaises(ValueError):
                    ocr.image_text(image.getvalue())
                run.assert_not_called()
