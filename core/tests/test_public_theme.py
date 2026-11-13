"""Part 3B: every public and access page uses one all-brown palette.

Three checks, matching the brief:

* every colour in ``static/css/public.css`` and the public templates is one of
  the defined brown palette values, so nothing is hard-coded outside them;
* the main text and button pairs reach the WCAG AA contrast ratios;
* every public page renders with the shared stylesheet and never a 500.

The scan reads the stylesheet and the templates as text, so it also covers the
brown email templates that carry inline CSS.
"""

import re
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import EmailVerification, UserInvitation
from core.branding import contrast_ratio
from core.models import Institution, OutboundMessage

ROOT = Path(__file__).resolve().parents[2]
CSS_PATH = ROOT / "static" / "css" / "public.css"
TEMPLATES_DIR = ROOT / "templates"

User = get_user_model()

# The one palette every public page is allowed to use.
PALETTE = {
    "--brown-950": "#2b1a12",
    "--brown-900": "#3e2723",
    "--brown-800": "#4e342e",
    "--brown-700": "#5d4037",
    "--brown-600": "#6f4a36",
    "--brown-500": "#8d6e63",
    "--brown-400": "#a1887f",
    "--brown-300": "#c4a998",
    "--brown-200": "#dcc8b6",
    "--brown-100": "#efe2d3",
    "--brown-50": "#faf4ec",
    "--espresso": "#1e120c",
    "--espresso-soft": "#2b1a12",
}
ALLOWED_HEX = {value.lstrip("#").lower() for value in PALETTE.values()}
ALLOWED_RGB = {
    tuple(int(value[i : i + 2], 16) for i in (1, 3, 5)) for value in PALETTE.values()
}

# Text and background pairs that must reach 4.5:1, in both themes.
CONTRAST_PAIRS = [
    ("--ink", "--bg"),
    ("--ink", "--surface"),
    ("--ink", "--field"),
    ("--muted", "--bg"),
    ("--muted", "--surface"),
    ("--on-brand", "--brand"),
    ("--on-brand-strong", "--brand-strong"),
    ("--bad", "--bad-bg"),
    ("--ok", "--bad-bg"),
    ("--on-band", "--band"),
    ("--on-band", "--band-strong"),
]

# Tokens the dark toggle and the system setting must agree on.
DARK_TOKENS = [name for name, _ in CONTRAST_PAIRS]

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
    """Colour literals in ``text`` that are not one of the brown palette values."""
    problems = []
    for match in HEX_RE.finditer(text):
        value = _hex_variants(match.group(1))
        if value not in ALLOWED_HEX:
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
        if triple not in ALLOWED_RGB:
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


class BrownPaletteScanTests(TestCase):
    """No colour outside the defined brown variables may appear."""

    def test_the_stylesheet_defines_every_palette_value(self):
        text = CSS_PATH.read_text(encoding="utf-8")
        for name, value in PALETTE.items():
            with self.subTest(variable=name):
                self.assertIn(f"{name}: {value};", text)

    def test_the_stylesheet_uses_only_palette_colours(self):
        problems = _bad_colours(CSS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(problems, [], f"Non-brown colours in public.css: {problems}")

    def test_the_public_templates_use_only_palette_colours(self):
        templates = _public_templates()
        self.assertIn("public_base.html", {path.name for path in templates})
        for path in templates:
            with self.subTest(template=path.name):
                problems = _bad_colours(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    problems, [], f"Non-brown colours in {path.name}: {problems}"
                )

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


class BrownContrastTests(TestCase):
    """The main text and button pairs must be readable in both themes."""

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

    def _check(self, variables, label):
        for foreground, background in CONTRAST_PAIRS:
            with self.subTest(theme=label, pair=f"{foreground} on {background}"):
                ratio = contrast_ratio(
                    _resolve(variables, foreground),
                    _resolve(variables, background),
                )
                self.assertIsNotNone(ratio)
                self.assertGreaterEqual(
                    round(ratio, 2), 4.5, f"{foreground} on {background} is {ratio}"
                )

    def test_light_mode_pairs_meet_aa(self):
        self._check(self.light, "light")

    def test_dark_mode_pairs_meet_aa(self):
        self._check(self.dark, "dark")

    def test_the_system_setting_matches_the_toggle(self):
        for token in DARK_TOKENS:
            with self.subTest(token=token):
                self.assertEqual(
                    _resolve(self.dark, token), _resolve(self.media, token)
                )

    def test_buttons_use_the_readable_text_token(self):
        block = re.search(r"\.btn\s*\{(.*?)\}", self.text, re.DOTALL).group(1)
        self.assertIn("var(--on-brand)", block)


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

    def test_every_public_page_loads_with_the_stylesheet(self):
        urls = [
            reverse("landing"),
            reverse("login"),
            reverse("learn_index"),
            reverse("institution_public", args=[self.institution.slug]),
            reverse("register_choice"),
            reverse("register_student"),
            reverse("register_staff"),
            reverse("register_staff_sent"),
            reverse("register_contact", args=[self.institution.pk]),
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
    """The institution's words appear, but its colours stay off public pages."""

    def test_the_institution_page_shows_name_tagline_and_about(self):
        response = self.client.get(
            reverse("institution_public", args=[self.institution.slug])
        )
        self.assertPublic(response)
        self.assertContains(response, "Mavambo College")
        self.assertContains(response, "Learn. Serve. Lead.")
        self.assertContains(response, "A community college in Harare.")
        self.assertContains(response, "brand-badge")

    def test_the_institution_colours_are_not_applied_publicly(self):
        response = self.client.get(
            reverse("institution_public", args=[self.institution.slug])
        )
        self.assertNotContains(response, "#123456")
        self.assertNotContains(response, "#abcdef")
        self.assertNotContains(response, "abcdef")

    def test_a_logo_gets_a_cream_backing_and_initials_cover_the_rest(self):
        response = self.client.get(
            reverse("institution_public", args=[self.institution.slug])
        )
        # No logo is set, so the initials fall back inside the cream badge.
        self.assertContains(response, "brand-badge")
        self.assertContains(response, "MC")


class PublicThemeToggleTests(PublicPageRenderTests):
    """The dark variant is switched by the system and by a remembered toggle."""

    def test_public_pages_offer_the_theme_toggle(self):
        for url in (
            reverse("landing"),
            reverse("login"),
            reverse("register_choice"),
            reverse("learn_index"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, "data-theme-toggle")
                self.assertContains(response, "edusphere.public.theme")

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


class BrownEmailTests(PublicPageRenderTests):
    """Verification, invitation and reset emails carry the brown palette."""

    def test_the_verification_email_has_html_and_plain_text(self):
        from accounts.registration import send_email_verification

        verification = send_email_verification(self.user)
        self.assertIsNotNone(verification)
        message = OutboundMessage.all_objects.filter(
            recipient=self.user.email
        ).latest("pk")
        self.assertIn(str(verification.token), message.body)
        self.assertIn("#5d4037", message.html_body)
        self.assertIn("#3e2723", message.html_body)

    def test_the_invitation_email_has_html_and_plain_text(self):
        from accounts.user_admin import create_invitation

        invitation = create_invitation(self.user)
        message = OutboundMessage.all_objects.filter(
            recipient=self.user.email
        ).latest("pk")
        self.assertIn(str(invitation.token), message.body)
        self.assertIn("#5d4037", message.html_body)

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
        self.assertIn("#5d4037", html)
        self.assertIn("/reset/", html)

    def test_a_password_reset_sends_an_html_alternative(self):
        mail.outbox = []
        response = self.client.post(
            reverse("password_reset"), {"email": self.user.email}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        alternatives = [content for content, _ in message.alternatives]
        self.assertTrue(alternatives)
        self.assertIn("#5d4037", alternatives[0])
        # The plain-text version is kept alongside the rich one.
        self.assertIn("/reset/", message.body)
