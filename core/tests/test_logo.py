"""Part 1: institution logos, branding and the pages that show them.

The logo pipeline is deliberately strict: only a real PNG, JPG or WebP is
accepted, the file is re-encoded with Pillow so nothing hidden travels with it,
and each institution's files live in their own folder under a random name.

These tests cover the validation, the stored sizes, the URL helpers, the
template tags, the permission gate, the tenant isolation and the audit trail.
"""

import shutil
import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils.functional import empty
from PIL import Image, PngImagePlugin

from core import logo as logo_module
from core.logo import (
    absolute_logo_url,
    institution_logo_bytes,
    institution_logo_png_url,
    institution_logo_url,
    logo_flowable,
    open_logo,
    remove_logo,
    save_logo,
)
from core.models import AuditEntry, Institution, Permission, Role, RolePermission
from core.roles import ensure_roles

User = get_user_model()

YEAR = {
    "year_name": "2026",
    "year_start": "2026-01-15",
    "year_end": "2026-12-10",
    "term_name": "Term 1",
    "term_start": "2026-01-15",
    "term_end": "2026-04-10",
}
ADMIN = {
    "username": "mhofu_admin",
    "first_name": "Tatenda",
    "last_name": "Moyo",
    "email": "tatenda@mhofu.test",
    "password": "school12345",
    "password_confirm": "school12345",
}


def _image_bytes(fmt="PNG", size=(400, 400), colour=(20, 58, 138)):
    """A small, real picture in the requested format."""
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, format=fmt)
    return buffer.getvalue()


def _upload(name="logo.png", data=None, content_type="image/png"):
    return SimpleUploadedFile(name, data if data is not None else _image_bytes(), content_type)


class MediaTestCase(TestCase):
    """Run each test against a private media folder.

    Django only rebuilds the default storage when ``STORAGES`` changes, so the
    wrapped storage is reset by hand after the ``MEDIA_ROOT`` override.
    """

    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp(prefix="edusphere-media-")
        self._media_override = override_settings(MEDIA_ROOT=self.media_root)
        self._media_override.enable()
        default_storage._wrapped = empty

    def tearDown(self):
        self._media_override.disable()
        default_storage._wrapped = empty
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()


# ---------- validation and re-encoding ----------
class LogoValidationTests(TestCase):
    def test_a_png_is_accepted(self):
        self.assertEqual(open_logo(_upload("crest.png")).mode, "RGBA")

    def test_a_jpeg_is_accepted(self):
        upload = _upload("photo.jpg", _image_bytes("JPEG"), "image/jpeg")
        self.assertEqual(open_logo(upload).mode, "RGBA")

    def test_a_webp_is_accepted(self):
        upload = _upload("art.webp", _image_bytes("WEBP"), "image/webp")
        self.assertEqual(open_logo(upload).mode, "RGBA")

    def test_a_non_picture_is_refused(self):
        with self.assertRaises(ValidationError):
            open_logo(_upload("notes.txt", b"just some text", "text/plain"))

    def test_an_empty_upload_is_refused(self):
        with self.assertRaises(ValidationError):
            open_logo(_upload("empty.png", b"", "image/png"))

    def test_a_file_larger_than_two_megabytes_is_refused(self):
        data = _image_bytes() + b"\x00" * (2 * 1024 * 1024)
        with self.assertRaises(ValidationError) as caught:
            open_logo(_upload("big.png", data))
        self.assertIn("2 MB", str(caught.exception))

    def test_a_tiny_image_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            open_logo(_upload("small.png", _image_bytes(size=(120, 120))))
        self.assertIn("200", str(caught.exception))

    def test_a_bmp_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            open_logo(_upload("old.bmp", _image_bytes("BMP"), "image/bmp"))
        self.assertIn("PNG, JPG or WebP", str(caught.exception))

    def test_a_gif_is_refused(self):
        with self.assertRaises(ValidationError):
            open_logo(_upload("anim.gif", _image_bytes("GIF"), "image/gif"))

    def test_an_svg_is_refused(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"></svg>'
        with self.assertRaises(ValidationError):
            open_logo(_upload("logo.svg", svg, "image/svg+xml"))

    def test_a_renamed_script_is_refused(self):
        with self.assertRaises(ValidationError):
            open_logo(_upload("logo.png", b"#!/bin/sh\necho hi", "image/png"))

    def test_the_re_encoded_copy_drops_hidden_metadata(self):
        buffer = BytesIO()
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", "HIDDENPAYLOAD")
        Image.new("RGB", (400, 400), (10, 10, 10)).save(
            buffer, format="PNG", pnginfo=info)
        tagged = buffer.getvalue()
        self.assertIn(b"HIDDENPAYLOAD", tagged)
        clean = logo_module.preview_png_bytes(_upload("tagged.png", tagged))
        self.assertNotIn(b"HIDDENPAYLOAD", clean)


# ---------- storage, sizes and URLs ----------
class LogoStorageTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.inst = Institution.objects.create(
            name="Mhofu Primary School", slug="mhofu", type="primary")

    def test_every_size_and_the_png_fallback_are_stored(self):
        paths = save_logo(self.inst, _upload())
        self.assertEqual(set(paths), {"64", "160", "512", "png"})
        for name in paths.values():
            self.assertTrue(default_storage.exists(name), name)
        self.assertTrue(
            all(name.startswith("institutions/mhofu/logos/") for name in paths.values()))

    def test_the_canonical_field_points_at_the_largest_webp(self):
        paths = save_logo(self.inst, _upload())
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.logo.name, paths["512"])
        self.assertEqual(self.inst.settings["logo"], paths)

    def test_the_stored_files_are_the_requested_widths(self):
        save_logo(self.inst, _upload(data=_image_bytes(size=(900, 600))))
        for size in (64, 160, 512):
            data = institution_logo_bytes(self.inst, size)
            with Image.open(BytesIO(data)) as image:
                self.assertLessEqual(max(image.size), size)

    def test_the_file_names_are_random_and_never_the_upload_name(self):
        first = save_logo(self.inst, _upload("school-crest.png"))
        second = save_logo(self.inst, _upload("school-crest.png"))
        self.assertNotIn("school-crest", first["512"])
        self.assertNotEqual(first["512"], second["512"])

    def test_saving_again_removes_the_previous_files(self):
        first = save_logo(self.inst, _upload())
        save_logo(self.inst, _upload())
        for name in first.values():
            self.assertFalse(default_storage.exists(name), name)

    def test_removing_the_logo_deletes_the_files_and_clears_the_field(self):
        paths = save_logo(self.inst, _upload())
        remove_logo(self.inst)
        self.inst.refresh_from_db()
        self.assertFalse(self.inst.logo)
        self.assertNotIn("logo", self.inst.settings)
        for name in paths.values():
            self.assertFalse(default_storage.exists(name), name)

    def test_urls_point_at_the_stored_files(self):
        paths = save_logo(self.inst, _upload())
        self.assertIn(paths["64"], institution_logo_url(self.inst, 64))
        self.assertIn(paths["160"], institution_logo_url(self.inst, 160))
        self.assertIn(paths["png"], institution_logo_png_url(self.inst))

    def test_there_is_no_url_before_a_logo_is_set(self):
        self.assertEqual(institution_logo_url(self.inst, 160), "")
        self.assertEqual(institution_logo_png_url(self.inst), "")
        self.assertIsNone(institution_logo_bytes(self.inst, 160, png=True))

    def test_an_absolute_url_is_built_for_emails(self):
        save_logo(self.inst, _upload())
        request = RequestFactory().get("/")
        url = absolute_logo_url(self.inst, request, 160)
        self.assertTrue(url.startswith("http://testserver/"))

    def test_a_pdf_flowable_is_scaled_to_fit(self):
        save_logo(self.inst, _upload())
        flowable = logo_flowable(self.inst, 40)
        self.assertIsNotNone(flowable)
        self.assertLessEqual(flowable.drawWidth, 40)
        self.assertLessEqual(flowable.drawHeight, 40)

    def test_there_is_no_flowable_without_a_logo(self):
        self.assertIsNone(logo_flowable(self.inst, 40))

    def test_one_institution_cannot_see_anothers_logo_folder(self):
        other = Institution.objects.create(
            name="Beta College", slug="beta", type="college")
        alpha_paths = save_logo(self.inst, _upload())
        beta_paths = save_logo(other, _upload())
        self.assertTrue(
            all(p.startswith("institutions/mhofu/") for p in alpha_paths.values()))
        self.assertTrue(
            all(p.startswith("institutions/beta/") for p in beta_paths.values()))
        remove_logo(other)
        for name in alpha_paths.values():
            self.assertTrue(default_storage.exists(name), name)


# ---------- template tags ----------
class BrandingTemplateTagTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.inst = Institution.objects.create(
            name="Mhofu Primary School", slug="mhofu", type="primary",
            primary_color="#1E3A8A")

    def _render(self, source):
        template = Template("{% load branding %}" + source)
        return template.render(Context({"institution": self.inst}))

    def test_the_logo_tag_returns_the_url(self):
        save_logo(self.inst, _upload())
        self.inst.refresh_from_db()
        out = self._render("{% institution_logo institution 160 as url %}{{ url }}")
        self.assertIn("institutions/mhofu/", out)

    def test_the_logo_tag_is_empty_without_a_logo(self):
        out = self._render("{% institution_logo institution 160 as url %}[{{ url }}]")
        self.assertEqual(out, "[]")

    def test_the_png_tag_returns_the_fallback(self):
        save_logo(self.inst, _upload())
        self.inst.refresh_from_db()
        self.assertIn(".png", self._render("{% institution_logo_png institution %}"))

    def test_the_initials_tag_uses_two_letters(self):
        self.assertEqual(
            self._render("{% institution_initials institution %}"), "MP")

    def test_the_brand_colour_tag_returns_the_primary_colour(self):
        self.assertEqual(
            self._render("{% institution_brand_colour institution %}"), "#1E3A8A")

    def test_the_brand_text_tag_stays_readable(self):
        self.assertEqual(
            self._render("{% institution_brand_text institution %}"), "#ffffff")


# ---------- the model ----------
class InstitutionModelTests(TestCase):
    def test_about_is_capped_at_600_characters(self):
        self.assertEqual(Institution._meta.get_field("about").max_length, 600)

    def test_website_is_a_field(self):
        self.assertEqual(Institution._meta.get_field("website").max_length, 200)

    def test_initials_come_from_the_first_two_words(self):
        self.assertEqual(Institution(name="Mhofu Primary School").initials(), "MP")

    def test_initials_fall_back_for_an_empty_name(self):
        self.assertEqual(Institution(name="").initials(), "?")


# ---------- the settings screen ----------
class BrandingSettingsTests(MediaTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha College", slug="alpha", type="college")
        cls.other = Institution.objects.create(
            name="Beta College", slug="beta", type="college")
        ensure_roles(cls.inst)
        ensure_roles(cls.other)

        cls.admin = User.objects.create_user(
            "alpha_admin", password="pw123456", role="admin",
            institution=cls.inst)
        cls.admin.primary_role = Role.all_objects.get(
            institution=cls.inst, code="institution_admin")
        cls.admin.save(update_fields=["primary_role"])

        cls.other_admin = User.objects.create_user(
            "beta_admin", password="pw123456", role="admin",
            institution=cls.other)
        cls.other_admin.primary_role = Role.all_objects.get(
            institution=cls.other, code="institution_admin")
        cls.other_admin.save(update_fields=["primary_role"])

        # A role that may edit settings but not the logo or the colours.
        settings_only = Role.all_objects.create(
            institution=cls.inst, code="office", name="Office")
        RolePermission.objects.create(
            role=settings_only,
            permission=Permission.objects.get(code="admin.manage_settings"))
        cls.office = User.objects.create_user(
            "alpha_office", password="pw123456", role="student",
            institution=cls.inst)
        cls.office.primary_role = settings_only
        cls.office.save(update_fields=["primary_role"])

    def test_an_admin_saves_the_colours_and_a_logo(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("institution_settings"), {
            "action": "branding",
            "primary_color": "#123456",
            "accent_color": "#654321",
            "logo": _upload("crest.png"),
        })
        self.assertEqual(resp.status_code, 302)
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.primary_color, "#123456")
        self.assertEqual(self.inst.accent_color, "#654321")
        self.assertTrue(self.inst.logo)
        entry = AuditEntry.all_objects.filter(
            action="institution.branding_updated", institution=self.inst).first()
        self.assertIsNotNone(entry)
        self.assertTrue(entry.after["logo"])

    def test_removing_the_logo_is_audited(self):
        save_logo(self.inst, _upload())
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("institution_settings"), {
            "action": "branding", "remove_logo": "1",
            "primary_color": "", "accent_color": "",
        })
        self.assertEqual(resp.status_code, 302)
        self.inst.refresh_from_db()
        self.assertFalse(self.inst.logo)
        self.assertTrue(AuditEntry.all_objects.filter(
            action="institution.logo_removed", institution=self.inst).exists())

    def test_a_bad_logo_is_refused_with_a_message(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("institution_settings"), {
            "action": "branding",
            "primary_color": "", "accent_color": "",
            "logo": _upload("tiny.png", _image_bytes(size=(50, 50))),
        }, follow=True)
        self.assertContains(resp, "at least 200")
        self.inst.refresh_from_db()
        self.assertFalse(self.inst.logo)

    def test_a_role_without_the_branding_permission_is_refused(self):
        self.client.force_login(self.office)
        resp = self.client.post(reverse("institution_settings"), {
            "action": "branding", "primary_color": "#123456", "accent_color": "",
        })
        self.assertEqual(resp.status_code, 403)
        self.inst.refresh_from_db()
        self.assertNotEqual(self.inst.primary_color, "#123456")

    def test_that_role_may_still_edit_the_details(self):
        self.client.force_login(self.office)
        resp = self.client.post(reverse("institution_settings"), {
            "action": "details", "name": "Alpha College", "tagline": "Hello",
        })
        self.assertEqual(resp.status_code, 302)
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.tagline, "Hello")

    def test_the_page_hides_the_branding_form_from_that_role(self):
        self.client.force_login(self.office)
        resp = self.client.get(reverse("institution_settings"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "not the logo or colours")
        self.assertNotContains(resp, 'name="logo"')

    def test_the_page_shows_the_branding_form_to_the_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("institution_settings"))
        self.assertContains(resp, "Logo and branding")
        self.assertContains(resp, 'name="logo"')

    def test_branding_only_touches_the_users_own_institution(self):
        self.client.force_login(self.other_admin)
        self.client.post(reverse("institution_settings"), {
            "action": "branding",
            "primary_color": "#010203",
            "accent_color": "",
            "logo": _upload("beta.png"),
        })
        self.inst.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.other.primary_color, "#010203")
        self.assertNotEqual(self.inst.primary_color, "#010203")
        self.assertFalse(self.inst.logo)

    def test_the_logo_lands_in_the_institutions_own_folder(self):
        self.client.force_login(self.other_admin)
        self.client.post(reverse("institution_settings"), {
            "action": "branding", "primary_color": "", "accent_color": "",
            "logo": _upload("beta.png"),
        })
        self.other.refresh_from_db()
        self.assertTrue(self.other.logo.name.startswith("institutions/beta/"))


# ---------- the setup wizard ----------
class WizardLogoTests(MediaTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.platform = User.objects.create_user(
            "platform", password="pw123456", role="admin",
            is_staff=True, is_superuser=True)

    def setUp(self):
        super().setUp()
        self.client.force_login(self.platform)

    def _details(self, **overrides):
        data = {
            "name": "Mhofu Primary School", "slug": "mhofu", "type": "primary",
            "currency": "$", "timezone": "Africa/Harare", "language": "en",
            "address": "Box 42, Mhofu", "contact_email": "office@mhofu.test",
            "contact_phone": "+263 77 000 0001", "primary_color": "#1E3A8A",
            "accent_color": "#3B82F6",
        }
        data.update(overrides)
        return data

    def _run_to_the_end(self, logo=None):
        details = self._details()
        if logo is not None:
            details["logo"] = logo
        self.client.post(reverse("setup_wizard"), details)
        self.client.post(reverse("setup_wizard"), {
            "level_set": "primary", "term_scheme": "terms",
            "class_label": "Class", "grade_label": "Grade", "pupil_label": "Pupil",
            "teacher_label": "Teacher", "unit_label": "School", "term_label": "Term",
        })
        self.client.post(reverse("setup_wizard"), {"schools": [], "departments": [],
                                                    "levels": []})
        self.client.post(reverse("setup_wizard"), YEAR)
        self.client.post(reverse("setup_wizard"), {"modules": ["core_academics"]})
        self.client.post(reverse("setup_wizard"), {
            "letter": ["A"], "min_mark": ["75"], "points": ["4"]})
        self.client.post(reverse("setup_wizard"), ADMIN)
        return self.client.post(reverse("setup_wizard"), {})

    def test_a_logo_chosen_in_the_details_step_reaches_the_institution(self):
        self._run_to_the_end(logo=_upload("crest.png"))
        institution = Institution.objects.get(slug="mhofu")
        self.assertTrue(institution.logo)
        self.assertTrue(institution.logo.name.startswith("institutions/mhofu/"))
        self.assertTrue(institution.settings["logo"]["png"])

    def test_a_bad_logo_is_refused_on_the_details_step(self):
        details = self._details()
        details["logo"] = _upload("tiny.png", _image_bytes(size=(10, 10)))
        resp = self.client.post(reverse("setup_wizard"), details)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "at least 200")
        self.assertFalse(Institution.objects.filter(slug="mhofu").exists())

    def test_the_session_keeps_the_logo_across_steps(self):
        details = self._details()
        details["logo"] = _upload("crest.png")
        self.client.post(reverse("setup_wizard"), details)
        stored = self.client.session["setup_wizard"]["logo"]
        self.assertTrue(stored["data"])

    def test_the_chosen_logo_can_be_removed_before_the_end(self):
        details = self._details()
        details["logo"] = _upload("crest.png")
        self.client.post(reverse("setup_wizard"), details)
        self.client.post(reverse("setup_wizard"), {"action": "back"})
        removal = self._details()
        removal["remove_logo"] = "1"
        self.client.post(reverse("setup_wizard"), removal)
        self.assertNotIn("logo", self.client.session["setup_wizard"])

    def test_the_details_step_shows_a_logo_field(self):
        resp = self.client.get(reverse("setup_wizard"))
        self.assertContains(resp, 'type="file" name="logo"')


# ---------- the sidebar ----------
class SidebarLogoRenderTests(MediaTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha College", slug="alpha", type="college")
        cls.admin = User.objects.create_user(
            "alpha_admin", password="pw123456", role="admin",
            is_staff=True, is_superuser=True, institution=cls.inst)

    def test_the_sidebar_shows_the_logo_when_there_is_one(self):
        save_logo(self.inst, _upload())
        self.inst.refresh_from_db()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "institutions/alpha/logos/")
        self.assertContains(resp, "Alpha College logo")

    def test_the_sidebar_falls_back_to_initials_without_a_logo(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "brand-mark initials")
        self.assertContains(resp, ">AC<")
