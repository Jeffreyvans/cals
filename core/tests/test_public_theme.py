"""The public surface uses one all-brown palette.

The checks match the brief:

* every colour in ``static/css/public.css`` and the public templates is one of
  the defined brown values, so nothing outside the palette is hard-coded;
* the main text, button and border pairs reach the WCAG AA contrast ratios;
* the dark variant follows the system setting and the remembered toggle;
* every public and access page renders with the shared stylesheet, never a 500;
* institution branding appears without applying the institution's colours;
* photos get a CSS-only warm duotone and keep meaningful alt text;
* the verification, invitation and reset emails carry the brown palette and a
  plain-text version.

The scan reads the stylesheet and the templates as text, so it also covers the
HTML email templates that carry inline CSS.
"""

import re
import shutil
import tempfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.functional import empty
from django.utils.http import urlsafe_base64_encode
from PIL import Image

from accounts.models import EmailVerification, SignupLink, UserInvitation
from core.branding import contrast_ratio
from core.logo import save_logo
from core.models import Institution, OutboundMessage

ROOT = Path(__file__).resolve().parents[2]
CSS_PATH = ROOT / "static" / "css" / "public.css"
TEMPLATES_DIR = ROOT / "templates"

User = get_user_model()

# The only colours the public surface may use.
BROWN = {
    "950": "#2b1a12",
    "900": "#3e2723",
    "800": "#4e342e",
    "700": "#5d4037",
    "600": "#6f4a36",
    "500": "#8d6e63",
    "400": "#a1887f",
    "300": "#c4a998",
    "200": "#dcc8b6",
    "100": "#efe2d3",
    "50": "#faf4ec",
}
ESPRESSO = {"espresso": "#1e120c", "espresso-soft": "#2b1a12"}
PALETTE_HEX = set(BROWN.values()) | set(ESPRESSO.values())
PALETTE_RGB = {
    tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    for value in PALETTE_HEX
}

# Text and background pairs that must reach 4.5:1, in both themes.
CONTRAST_PAIRS = [
    ("--ink", "--bg"),
    ("--ink", "--surface"),
    ("--ink", "--surface-2"),
    ("--muted", "--bg"),
    ("--muted", "--surface"),
    ("--muted", "--surface-2"),
    ("--brand", "--bg"),
    ("--brand", "--surface"),
    ("--brand-strong", "--bg"),
    ("--on-brand", "--brand"),
    ("--on-brand-strong", "--brand-strong"),
    ("--on-band", "--band"),
    ("--on-band", "--band-strong"),
    ("--bad", "--bad-bg"),
    ("--ok", "--bad-bg"),
]

# UI borders need 3:1 rather than 4.5:1.
BORDER_PAIRS = [
    ("--line-strong", "--field"),
    ("--line-strong", "--bg"),
]

HEX_RE = re.compile(r"#([0-9a-fA-F]{3,8})\b")
FUNC_RE = re.compile(r"\b(rgba?|hsla?)\(([^)]*)\)")
NAMED_COLOURS = {
    "red", "green", "blue", "orange", "yellow", "purple", "pink", "teal",
    "cyan", "magenta", "lime", "navy", "maroon", "gold", "silver", "gray",
    "grey", "black", "white", "aqua", "fuchsia", "olive",
}
COLOUR_PROP_RE = re.compile(
    r"(?:^|[;{\s])(?:color|background|background-color|background-image|border|"
    r"border-color|border-top|border-bottom|border-left|border-right|outline|"
    r"outline-color|fill|stroke|box-shadow|text-shadow)\s*:\s*([^;{}]+)",
    re.IGNORECASE,
)


def _public_templates():
    """Every template that belongs to the public, signed-out surface."""
    found = []
    for path in TEMPLATES_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if (
            path.name == "public_base.html"
            or "public_base.html" in text
            or "css/public.css" in text
            or path.parent.name in {"public", "emails"}
        ):
            found.append(path)
    return found


def _hex_variants(raw):
    """Every spelling of a hex literal that counts as the same colour."""
    raw = raw.lower()
    if len(raw) in (3, 4):
        raw = "".join(char * 2 for char in raw)
    return raw[:6]


def _bad_colours(text):
    """Colour literals in ``text`` that are not one of the brown values."""
    problems = []
    for match in HEX_RE.finditer(text):
        value = "#" + _hex_variants(match.group(1))
        if value not in PALETTE_HEX:
            problems.append(f"#{match.group(1)}")
    for match in FUNC_RE.finditer(text):
        name, args = match.group(1).lower(), match.group(2)
        if name.startswith("hsl"):
            problems.append(match.group(0))
            continue
        numbers = [part for part in re.split(r"[,\s/]+", args.strip()) if part]
        try:
            triple = tuple(int(float(numbers[index])) for index in range(3))
        except (ValueError, IndexError):
            problems.append(match.group(0))
            continue
        if triple not in PALETTE_RGB:
            problems.append(match.group(0))
    for match in COLOUR_PROP_RE.finditer(text):
        for token in re.split(r"[\s,]+", match.group(1).strip()):
            if token.lower() in NAMED_COLOURS:
                problems.append(match.group(0).strip())
    return problems


def _block_with(text, selector_re, needle="--bg"):
    for match in re.finditer(selector_re + r"\s*\{(.*?)\}", text, re.DOTALL):
        if needle in match.group(1):
            return match.group(1)
    return ""


def _variables(block):
    return {
        name: value.strip()
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block)
    }


def _resolve(variables, token, depth=0):
    value = variables.get(token, "")
    match = re.fullmatch(r"var\((--[\w-]+)\)", value.strip())
    if match and depth < 10:
        return _resolve(variables, match.group(1), depth + 1)
    return value.strip()


def _rule(text, selector):
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\}", text, re.DOTALL)
    return match.group(1) if match else ""


class PublicPaletteScanTests(TestCase):
    """No colour outside the brown palette may appear."""

    def test_the_stylesheet_defines_the_brown_palette(self):
        text = CSS_PATH.read_text(encoding="utf-8")
        for shade, value in BROWN.items():
            with self.subTest(shade=shade):
                self.assertIn(f"--brown-{shade}: {value};", text)

    def test_the_stylesheet_defines_the_espresso_backgrounds(self):
        text = CSS_PATH.read_text(encoding="utf-8")
        for name, value in ESPRESSO.items():
            with self.subTest(variable=name):
                self.assertIn(f"--{name}: {value};", text)

    def test_the_stylesheet_uses_only_palette_colours(self):
        problems = _bad_colours(CSS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(problems, [], f"Non-palette colours in public.css: {problems}")

    def test_the_public_templates_use_only_palette_colours(self):
        templates = _public_templates()
        self.assertIn("public_base.html", {path.name for path in templates})
        for path in templates:
            with self.subTest(template=path.name):
                problems = _bad_colours(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    problems, [], f"Non-palette colours in {path.name}: {problems}"
                )

    def test_no_green_or_marigold_token_remains(self):
        banned = (
            "--green", "--marigold", "btn-marigold",
            "#12403c", "#e8a317", "#f7f5ef", "#0b2b28",
        )
        for path in [CSS_PATH, *_public_templates()]:
            text = path.read_text(encoding="utf-8")
            for token in banned:
                with self.subTest(file=path.name, token=token):
                    self.assertNotIn(token, text)

    def test_the_public_base_loads_the_shared_stylesheet(self):
        text = (TEMPLATES_DIR / "public_base.html").read_text(encoding="utf-8")
        self.assertIn("css/public.css", text)
        self.assertNotIn("css/landing.css", text)

    def test_the_login_page_loads_the_shared_stylesheet(self):
        text = (TEMPLATES_DIR / "registration" / "login.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("css/public.css", text)
        self.assertNotIn("css/landing.css", text)


class PublicContrastTests(TestCase):
    """The main text, button and border pairs must be readable in both themes."""

    def setUp(self):
        self.text = CSS_PATH.read_text(encoding="utf-8")
        self.light = _variables(_block_with(self.text, r":root"))
        self.dark = dict(self.light)
        self.dark.update(
            _variables(_block_with(self.text, r':root\[data-theme="dark"\]'))
        )
        self.media = dict(self.light)
        self.media.update(
            _variables(
                _block_with(self.text, r':root:not\(\[data-theme="light"\]\)')
            )
        )

    def _check(self, variables, label, pairs, minimum):
        for foreground, background in pairs:
            with self.subTest(theme=label, pair=f"{foreground} on {background}"):
                ratio = contrast_ratio(
                    _resolve(variables, foreground),
                    _resolve(variables, background),
                )
                self.assertIsNotNone(ratio)
                self.assertGreaterEqual(
                    round(ratio, 2),
                    minimum,
                    f"{foreground} on {background} is {ratio}",
                )

    def test_light_mode_pairs_meet_aa(self):
        self._check(self.light, "light", CONTRAST_PAIRS, 4.5)

    def test_dark_mode_pairs_meet_aa(self):
        self._check(self.dark, "dark", CONTRAST_PAIRS, 4.5)

    def test_ui_borders_meet_three_to_one(self):
        self._check(self.light, "light", BORDER_PAIRS, 3.0)
        self._check(self.dark, "dark", BORDER_PAIRS, 3.0)

    def test_the_system_setting_matches_the_toggle(self):
        self.assertEqual(set(self.dark), set(self.media))
        for token in self.dark:
            with self.subTest(token=token):
                self.assertEqual(
                    _resolve(self.dark, token), _resolve(self.media, token)
                )

    def test_the_primary_button_pair_is_readable(self):
        block = _rule(self.text, ".btn")
        self.assertIn("background: var(--brand)", block)
        self.assertIn("color: var(--on-brand)", block)
        for label, theme in (("light", self.light), ("dark", self.dark)):
            with self.subTest(theme=label):
                ratio = contrast_ratio(
                    _resolve(theme, "--on-brand"), _resolve(theme, "--brand")
                )
                self.assertGreaterEqual(round(ratio, 2), 4.5)

    def test_the_deep_button_pair_is_readable(self):
        block = _rule(self.text, ".btn-deep")
        self.assertIn("background: var(--brand-strong)", block)
        self.assertIn("color: var(--on-brand-strong)", block)
        for label, theme in (("light", self.light), ("dark", self.dark)):
            with self.subTest(theme=label):
                ratio = contrast_ratio(
                    _resolve(theme, "--on-brand-strong"),
                    _resolve(theme, "--brand-strong"),
                )
                self.assertGreaterEqual(round(ratio, 2), 4.5)

    def test_focus_is_a_thick_dark_brown_outline_with_a_cream_offset(self):
        self.assertIn("outline: 4px solid var(--brown-900);", self.text)
        self.assertIn("outline-offset: 3px;", self.text)
        # The dark variant flips the outline to cream for the espresso page.
        self.assertIn("outline-color: var(--brown-50);", self.text)
        self.assertGreaterEqual(
            round(contrast_ratio(BROWN["900"], BROWN["50"]), 2), 4.5
        )

    def test_alerts_carry_an_icon_as_well_as_colour(self):
        self.assertIn('content: "i";', self.text)
        self.assertIn('content: "!";', self.text)
        self.assertIn('content: "\\2713";', self.text)


class PublicPageRenderTests(TestCase):
    """Every public page loads with the stylesheet and never a 500."""

    @classmethod
    def setUpTestData(cls):
        cls.institution = Institution.objects.create(
            name="Mavambo College",
            slug="mavambo",
            type="college",
            tagline="Learn. Serve. Lead.",
            about="A community college in Harare.",
            public_registration=True,
            staff_positions=["teacher"],
            contact_email="registrar@mavambo.test",
            contact_phone="+263771000111",
            primary_color="#123456",
            accent_color="#abcdef",
        )
        cls.user = User.objects.create_user(
            "tariro",
            password="Str0ngPassw0rd",
            role="student",
            institution=cls.institution,
            email="tariro@example.test",
            first_name="Tariro",
            last_name="Moyo",
        )

    def assertPublic(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "css/public.css")
        self.assertContains(response, 'name="theme-color" content="#3e2723"')

    def test_every_public_page_loads_with_the_stylesheet(self):
        link = SignupLink.objects.create(
            institution=self.institution, kind=SignupLink.Kind.ANY
        )
        urls = [
            reverse("landing"),
            reverse("login"),
            reverse("learn_index"),
            reverse("learn_features"),
            reverse("learn_for_schools"),
            reverse("learn_for_colleges"),
            reverse("learn_for_parents_and_students"),
            reverse("learn_security_and_privacy"),
            reverse("learn_pricing_and_contact"),
            reverse("learn_faq"),
            reverse("institution_public", args=[self.institution.slug]),
            reverse("institution_login", args=[self.institution.slug]),
            reverse("register_choice"),
            reverse("register_student"),
            reverse("register_staff"),
            reverse("register_staff_sent"),
            reverse("register_contact", args=[self.institution.pk]),
            reverse("signup_link", args=[link.token]),
            reverse("password_reset"),
            reverse("password_reset_done"),
            reverse("password_reset_complete"),
            reverse("privacy_notice"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertPublic(self.client.get(url))

    def test_password_reset_confirm_page_loads(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        url = reverse(
            "password_reset_confirm", kwargs={"uidb64": uid, "token": token}
        )
        self.assertPublic(self.client.get(url, follow=True))

    def test_verify_email_page_loads(self):
        verification = EmailVerification.objects.create(user=self.user)
        response = self.client.get(
            reverse("register_verify_email", args=[verification.token])
        )
        self.assertPublic(response)

    def test_invitation_page_loads(self):
        invitation = UserInvitation.objects.create(
            user=self.user, expires_at=timezone.now() + timedelta(hours=72)
        )
        response = self.client.get(
            reverse("accept_invitation", args=[invitation.token])
        )
        self.assertPublic(response)

    def test_force_password_change_page_loads(self):
        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])
        self.client.force_login(self.user)
        self.assertPublic(self.client.get(reverse("password_change")))

    def test_the_landing_page_redirects_signed_in_visitors_to_the_dashboard(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("landing"))
        self.assertRedirects(
            response, reverse("dashboard"), fetch_redirect_response=False
        )

    def test_unknown_institution_is_a_404_not_a_500(self):
        response = self.client.get(
            reverse("institution_public", args=["does-not-exist"])
        )
        self.assertEqual(response.status_code, 404)


class InstitutionBrandingOnPublicPagesTests(PublicPageRenderTests):
    """Institution branding stays, but the page colours stay brown."""

    def test_the_institution_page_shows_name_tagline_and_about(self):
        response = self.client.get(
            reverse("institution_public", args=[self.institution.slug])
        )
        self.assertPublic(response)
        self.assertContains(response, "Mavambo College")
        self.assertContains(response, "Learn. Serve. Lead.")
        self.assertContains(response, "A community college in Harare.")
        self.assertContains(response, "brand-badge")
        self.assertContains(response, "Powered by EduSphere")

    def test_the_institution_colours_are_not_applied_to_public_pages(self):
        for url in (
            reverse("institution_public", args=[self.institution.slug]),
            reverse("institution_login", args=[self.institution.slug]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertNotContains(response, "#123456")
                self.assertNotContains(response, "#abcdef")
                self.assertNotContains(response, "--inst-primary")

    def test_the_institution_page_keeps_the_brown_palette(self):
        response = self.client.get(
            reverse("institution_public", args=[self.institution.slug])
        )
        self.assertContains(response, "institution-page")
        self.assertIn("css/public.css", response.content.decode())

    def test_a_logo_gets_a_soft_cream_backing(self):
        text = CSS_PATH.read_text(encoding="utf-8")
        badge = _rule(text, ".brand-badge")
        self.assertIn("background: var(--brown-50)", badge)
        self.assertIn("border: 1px solid var(--brown-200)", badge)
        contact = _rule(text, ".contact-logo")
        self.assertIn("background: var(--brown-50)", contact)
        self.assertIn("object-fit: contain", contact)

    def test_a_very_light_and_a_very_dark_logo_render_on_the_badge(self):
        for label, colour in (
            ("light", (250, 247, 240)),
            ("dark", (12, 9, 7)),
        ):
            with self.subTest(logo=label):
                with self._logo_media():
                    self._store_logo(colour)
                    response = self.client.get(
                        reverse("institution_public", args=[self.institution.slug])
                    )
                    self.assertPublic(response)
                    body = response.content.decode()
                    self.assertIn("institutions/mavambo/logos/", body)
                    self.assertIn('alt="Mavambo College logo"', body)
                    self.assertIn("brand-badge", body)

    def _logo_media(self):
        return _MediaRoot()

    def _store_logo(self, colour):
        buffer = BytesIO()
        Image.new("RGB", (400, 400), colour).save(buffer, format="PNG")
        save_logo(
            self.institution,
            SimpleUploadedFile("crest.png", buffer.getvalue(), "image/png"),
        )
        self.institution.refresh_from_db()


class _MediaRoot:
    """Point file storage at a throwaway folder for one test."""

    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="edusphere-public-media-")
        self.override = override_settings(MEDIA_ROOT=self.path)
        self.override.enable()
        default_storage._wrapped = empty
        return self

    def __exit__(self, *exc_info):
        self.override.disable()
        default_storage._wrapped = empty
        shutil.rmtree(self.path, ignore_errors=True)
        return False


class PublicPhotoTreatmentTests(PublicPageRenderTests):
    """Photos get a CSS-only warm overlay and keep meaningful alt text."""

    def test_the_photos_use_a_css_only_warm_duotone(self):
        text = CSS_PATH.read_text(encoding="utf-8")
        image_rule = _rule(text, ".media img")
        self.assertIn("filter: sepia(", image_rule)
        self.assertIn("saturate(", image_rule)
        overlay = re.search(r"\.media::after\s*\{(.*?)\}", text, re.DOTALL).group(1)
        self.assertIn("linear-gradient", overlay)
        self.assertIn("mix-blend-mode: multiply", overlay)
        self.assertIn("rgba(62, 39, 35", overlay)
        self.assertIn("rgba(43, 26, 18", overlay)

    def test_photos_keep_meaningful_alt_text(self):
        response = self.client.get(reverse("login"))
        body = response.content.decode()
        self.assertIn(
            'alt="Students working together at desks in a bright classroom."',
            body,
        )
        self.assertIn(
            'alt="Secondary school students at work in a classroom."', body
        )
        self.assertNotIn('alt=""', body)

    def test_reduced_motion_is_respected(self):
        text = CSS_PATH.read_text(encoding="utf-8")
        self.assertIn("@media (prefers-reduced-motion: reduce)", text)
        self.assertIn("animation-duration: 0.001ms !important", text)


class PublicThemeToggleTests(PublicPageRenderTests):
    """The dark variant is switched by the system and by a remembered toggle."""

    def test_public_pages_offer_the_theme_toggle(self):
        for url in (
            reverse("landing"),
            reverse("login"),
            reverse("register_choice"),
            reverse("learn_index"),
            reverse("institution_public", args=[self.institution.slug]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, "data-theme-toggle")
                self.assertContains(response, "edusphere.public.theme")

    def test_the_stylesheet_honours_a_remembered_choice(self):
        text = CSS_PATH.read_text(encoding="utf-8")
        self.assertIn(':root[data-theme="dark"]', text)
        self.assertIn(':root:not([data-theme="light"])', text)

    def test_the_signed_in_app_keeps_its_own_theme(self):
        staff = User.objects.create_user(
            "registrar",
            password="Str0ngPassw0rd",
            role="registrar",
            institution=self.institution,
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "css/app.css")
        self.assertNotContains(response, "css/public.css")


class PublicEmailTests(PublicPageRenderTests):
    """Verification, invitation and reset emails carry the brown palette."""

    def assertBrownEmail(self, html):
        self.assertIn("#faf4ec", html)
        self.assertIn("#3e2723", html)
        self.assertIn("#5d4037", html)
        for banned in ("#12403c", "#e8a317", "#f7f5ef"):
            self.assertNotIn(banned, html)

    def test_the_verification_email_has_html_and_plain_text(self):
        from accounts.registration import send_email_verification

        verification = send_email_verification(self.user)
        self.assertIsNotNone(verification)
        message = OutboundMessage.all_objects.filter(
            recipient=self.user.email
        ).latest("pk")
        self.assertIn(str(verification.token), message.body)
        self.assertNotIn("<", message.body)
        self.assertBrownEmail(message.html_body)

    def test_the_invitation_email_has_html_and_plain_text(self):
        from accounts.user_admin import create_invitation

        invitation = create_invitation(self.user)
        message = OutboundMessage.all_objects.filter(
            recipient=self.user.email
        ).latest("pk")
        self.assertIn(str(invitation.token), message.body)
        self.assertNotIn("<", message.body)
        self.assertBrownEmail(message.html_body)

    def test_the_password_reset_email_template_renders_in_brown(self):
        html = render_to_string(
            "emails/password_reset.html",
            {
                "user": self.user,
                "protocol": "https",
                "domain": "example.test",
                "uid": "abc",
                "token": "xyz",
            },
        )
        self.assertBrownEmail(html)
        self.assertIn("/reset/", html)

    def test_a_password_reset_sends_an_html_alternative_and_plain_text(self):
        mail.outbox = []
        response = self.client.post(
            reverse("password_reset"), {"email": self.user.email}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        alternatives = [content for content, _ in message.alternatives]
        self.assertTrue(alternatives)
        self.assertBrownEmail(alternatives[0])
        # The plain-text version is kept alongside the rich one.
        self.assertIn("/reset/", message.body)
        self.assertNotIn("<", message.body)
