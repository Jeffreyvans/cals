"""Uploads: validation, resizing, storage safety and URL generation.

Every upload path in EduSphere goes through ``core.uploads``. These tests pin
down the behaviour a person sees when a file is rejected (type, size or
dimensions), prove that large pictures are shrunk before they are stored, and
check that production cannot silently write to an ephemeral disk.
"""

import shutil
import tempfile
from io import BytesIO

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.utils.functional import empty
from PIL import Image

from core.uploads import (
    DOCUMENT_MAX_BYTES,
    IMAGE_MAX_BYTES,
    IMAGE_MAX_DIMENSION,
    document_upload_validator,
    media_url,
    open_image,
    processed_image,
    validate_document,
    validate_storage_configuration,
)

MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def _image_bytes(size=(200, 120), image_format="PNG", colour=(30, 120, 200)):
    output = BytesIO()
    Image.new("RGB", size, colour).save(output, format=image_format)
    return output.getvalue()


def _upload(name, data, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def _png(size=(200, 120)):
    return _upload("photo.png", _image_bytes(size=size), "image/png")


class ImageValidationTests(SimpleTestCase):
    def test_a_real_picture_is_accepted(self):
        image = open_image(_png())
        self.assertEqual(image.size, (200, 120))

    def test_a_text_file_with_an_image_name_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            open_image(_upload("fake.png", b"not a picture", "image/png"))
        self.assertIn("not a picture", str(caught.exception))

    def test_an_svg_is_refused(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        with self.assertRaises(ValidationError):
            open_image(_upload("logo.svg", svg, "image/svg+xml"))

    def test_a_picture_larger_than_the_limit_is_refused(self):
        data = _image_bytes() + b"\x00" * IMAGE_MAX_BYTES
        with self.assertRaises(ValidationError) as caught:
            open_image(_upload("big.png", data, "image/png"))
        self.assertIn("5 MB", str(caught.exception))

    def test_a_picture_smaller_than_the_minimum_side_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            open_image(_png(size=(20, 20)))
        self.assertIn("at least 64x64", str(caught.exception))

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            open_image(_upload("empty.png", b"", "image/png"))
        self.assertIn("Choose", str(caught.exception))


class ImageResizeTests(SimpleTestCase):
    def test_a_large_photo_is_shrunk_and_re_encoded_as_webp(self):
        upload = _png(size=(3000, 2000))
        processed = processed_image(upload, max_dimension=IMAGE_MAX_DIMENSION)
        self.assertTrue(processed.name.endswith(".webp"))
        stored = Image.open(BytesIO(processed.read()))
        self.assertLessEqual(max(stored.size), IMAGE_MAX_DIMENSION)
        self.assertEqual(stored.format, "WEBP")

    def test_a_small_photo_is_not_upscaled(self):
        processed = processed_image(_png(size=(320, 240)))
        stored = Image.open(BytesIO(processed.read()))
        self.assertEqual(stored.size, (320, 240))

    def test_resizing_keeps_the_aspect_ratio(self):
        processed = processed_image(_png(size=(3200, 1600)))
        stored = Image.open(BytesIO(processed.read()))
        self.assertEqual(stored.size, (1600, 800))

    def test_the_stored_name_is_safe(self):
        processed = processed_image(_png(), stem="../../My Photo!.png")
        self.assertNotIn("..", processed.name)
        self.assertNotIn("/", processed.name)
        self.assertTrue(processed.name.startswith("my-photo"))


class DocumentValidationTests(SimpleTestCase):
    def test_a_real_pdf_is_accepted(self):
        validate_document(_upload("id.pdf", MINIMAL_PDF, "application/pdf"))
        document_upload_validator(_upload("id.pdf", MINIMAL_PDF, "application/pdf"))

    def test_a_fake_pdf_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            validate_document(_upload("id.pdf", b"identity", "application/pdf"))
        self.assertIn("not a valid PDF", str(caught.exception))

    def test_a_dangerous_extension_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            validate_document(_upload("virus.exe", b"MZ", "application/octet-stream"))
        self.assertIn(".exe files are not accepted", str(caught.exception))

    def test_an_unknown_extension_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            validate_document(_upload("notes.abc", b"hello"))
        self.assertIn("Upload a PDF", str(caught.exception))

    def test_a_missing_extension_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            validate_document(_upload("passport", b"%PDF-1.4"))
        self.assertIn("file extension", str(caught.exception))

    def test_a_damaged_office_file_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            validate_document(_upload("report.docx", b"not a zip"))
        self.assertIn("damaged", str(caught.exception))

    def test_a_valid_office_zip_is_accepted(self):
        validate_document(_upload("report.docx", b"PK\x03\x04rest", "application/zip"))

    def test_a_document_larger_than_the_limit_is_refused(self):
        data = b"%PDF-1.4\n" + b"\x00" * DOCUMENT_MAX_BYTES
        with self.assertRaises(ValidationError) as caught:
            validate_document(_upload("big.pdf", data, "application/pdf"))
        self.assertIn("10 MB", str(caught.exception))

    def test_an_empty_document_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            validate_document(_upload("empty.pdf", b"", "application/pdf"))
        self.assertIn("Choose", str(caught.exception))


class StorageSafetyTests(SimpleTestCase):
    def test_production_refuses_local_disk_storage(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            validate_storage_configuration(
                debug=False, testing=False, backend="local")
        self.assertIn("S3-compatible object storage", str(caught.exception))

    def test_development_may_use_local_disk(self):
        validate_storage_configuration(debug=True, testing=False, backend="local")

    def test_the_test_suite_may_use_local_disk(self):
        validate_storage_configuration(debug=False, testing=True, backend="local")

    def test_production_accepts_each_object_store(self):
        for backend in ("s3", "r2", "b2", "supabase", "object"):
            with self.subTest(backend=backend):
                validate_storage_configuration(
                    debug=False, testing=False, backend=backend)


class MediaUrlTests(SimpleTestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="edusphere-uploads-")
        self._override = override_settings(
            MEDIA_ROOT=self.media_root, MEDIA_URL="/media/")
        self._override.enable()
        default_storage._wrapped = empty

    def tearDown(self):
        self._override.disable()
        default_storage._wrapped = empty
        shutil.rmtree(self.media_root, ignore_errors=True)

    def test_local_storage_returns_a_media_url(self):
        self.assertEqual(media_url("institutions/a/photo.webp"),
                         "/media/institutions/a/photo.webp")

    def test_an_empty_name_has_no_url(self):
        self.assertEqual(media_url(""), "")

    def test_saving_to_local_storage_survives_for_the_process(self):
        name = default_storage.save("uploads/photo.webp", BytesIO(b"data"))
        self.assertTrue(default_storage.exists(name))
        self.assertIn("/media/uploads/", media_url(name))

    def test_s3_storage_returns_an_absolute_signed_url(self):
        from storages.backends.s3 import S3Storage

        storage = S3Storage(
            bucket_name="edusphere-media",
            access_key="key",
            secret_key="secret",
            endpoint_url="https://example.supabase.co/storage/v1/s3",
            region_name="auto",
            querystring_auth=True,
        )
        url = storage.url("uploads/photo.webp")
        self.assertTrue(url.startswith("https://"))
        self.assertIn("edusphere-media", url)
        self.assertIn("X-Amz-Signature", url)

    def test_s3_public_bucket_can_skip_signing(self):
        from storages.backends.s3 import S3Storage

        storage = S3Storage(
            bucket_name="edusphere-media",
            access_key="key",
            secret_key="secret",
            custom_domain="cdn.example.com",
            querystring_auth=False,
        )
        url = storage.url("uploads/photo.webp")
        self.assertEqual(url, "https://cdn.example.com/uploads/photo.webp")
