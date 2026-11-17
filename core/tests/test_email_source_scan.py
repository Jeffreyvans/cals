"""Safeguard: no code may bypass the central OutboundMessage email pipeline."""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

# These files are allowed to touch Django's mail primitives: the central
# provider builds the EmailMultiAlternatives, configuration names backends,
# and tests inspect the in-memory outbox.
ALLOWED = {
    Path("core/notifications.py"),
    Path("config/email_config.py"),
    Path("config/settings.py"),
    Path("core/checks.py"),
}

FORBIDDEN = {
    "direct send_mail": re.compile(r"\bsend_mail\s*\("),
    "direct send_mass_mail": re.compile(r"\bsend_mass_mail\s*\("),
    "direct mail_admins": re.compile(r"\bmail_admins\s*\("),
    "direct mail_managers": re.compile(r"\bmail_managers\s*\("),
    "raw EmailMessage": re.compile(r"\bEmailMessage\s*\("),
    "raw EmailMultiAlternatives": re.compile(r"\bEmailMultiAlternatives\s*\("),
    "SMTP client": re.compile(r"\b(?:import\s+smtplib|from\s+smtplib\s+import)\b"),
    "direct mail import": re.compile(
        r"^\s*from\s+django\.core\.mail\s+import\s+"
        r"(?:send_mail|send_mass_mail|mail_admins|mail_managers)\b",
        re.MULTILINE,
    ),
    "hard-coded email API call": re.compile(
        r"https?://api\.(?:mailjet|resend|brevo)\.com"
    ),
}


class NoStrayEmailSendersTests(SimpleTestCase):
    def test_every_python_file_uses_the_central_email_pipeline(self):
        root = Path(settings.BASE_DIR)
        offenders = []
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(root)
            if "tests" in relative.parts or relative in ALLOWED:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):  # pragma: no cover
                continue
            for label, pattern in FORBIDDEN.items():
                if pattern.search(text):
                    offenders.append(f"{relative}: {label}")
        self.assertEqual(
            offenders, [],
            "Email must go through core.notifications.send_email. "
            "Found: " + ", ".join(offenders),
        )

    def test_config_and_central_provider_are_the_only_allowed_exceptions(self):
        self.assertTrue((Path(settings.BASE_DIR) / "core/notifications.py").exists())
        self.assertTrue((Path(settings.BASE_DIR) / "core/email_types.py").exists())
