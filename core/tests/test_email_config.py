"""Provider selection and the production backend guard."""

from django.test import SimpleTestCase, override_settings

from config.email_config import select_email_settings
from core.checks import check_email_backend


class ProviderSelectionTests(SimpleTestCase):
    def test_no_keys_defaults_to_console(self):
        result = select_email_settings({})
        self.assertEqual(result["EMAIL_PROVIDER"], "console")
        self.assertEqual(
            result["EMAIL_BACKEND"], "django.core.mail.backends.console.EmailBackend"
        )

    def test_explicit_mailjet_uses_anymail_backend_and_keys(self):
        result = select_email_settings({
            "EMAIL_PROVIDER": "Mailjet",
            "MAILJET_API_KEY": "key",
            "MAILJET_SECRET_KEY": "secret",
        }, timeout=7)
        self.assertEqual(result["EMAIL_PROVIDER"], "mailjet")
        self.assertEqual(
            result["EMAIL_BACKEND"], "anymail.backends.mailjet.EmailBackend"
        )
        self.assertEqual(result["ANYMAIL"]["MAILJET_API_KEY"], "key")
        self.assertEqual(result["ANYMAIL"]["MAILJET_SECRET_KEY"], "secret")
        self.assertEqual(
            result["ANYMAIL"]["MAILJET_API_URL"], "https://api.mailjet.com/v3.1/"
        )
        self.assertEqual(result["ANYMAIL"]["REQUESTS_TIMEOUT"], 7)
        self.assertEqual(result["warnings"], [])

    def test_missing_mailjet_keys_produce_a_warning(self):
        result = select_email_settings({"EMAIL_PROVIDER": "mailjet"})
        self.assertTrue(
            any("MAILJET_SECRET_KEY" in warning for warning in result["warnings"])
        )
        self.assertEqual(
            result["EMAIL_BACKEND"], "anymail.backends.mailjet.EmailBackend"
        )

    def test_legacy_resend_key_selects_resend(self):
        result = select_email_settings({"RESEND_API_KEY": "re_key"})
        self.assertEqual(result["EMAIL_PROVIDER"], "resend")
        self.assertEqual(
            result["ANYMAIL"]["RESEND_API_KEY"], "re_key"
        )

    def test_explicit_provider_wins_over_legacy_key(self):
        result = select_email_settings({
            "EMAIL_PROVIDER": "mailjet",
            "RESEND_API_KEY": "re_key",
            "MAILJET_API_KEY": "mj_key",
            "MAILJET_SECRET_KEY": "mj_secret",
        })
        self.assertEqual(result["EMAIL_PROVIDER"], "mailjet")

    def test_brevo_can_be_selected(self):
        result = select_email_settings({
            "EMAIL_PROVIDER": "brevo", "BREVO_API_KEY": "br_key"
        })
        self.assertEqual(result["EMAIL_PROVIDER"], "brevo")
        self.assertEqual(
            result["EMAIL_BACKEND"], "anymail.backends.brevo.EmailBackend"
        )
        self.assertEqual(result["ANYMAIL"]["BREVO_API_KEY"], "br_key")

    def test_unknown_provider_falls_back_to_console_with_warning(self):
        result = select_email_settings({"EMAIL_PROVIDER": "carrier-pigeon"})
        self.assertEqual(result["EMAIL_PROVIDER"], "console")
        self.assertTrue(any("Unknown EMAIL_PROVIDER" in w for w in result["warnings"]))

    def test_console_in_production_warns(self):
        result = select_email_settings({}, debug=False)
        self.assertTrue(any("console" in w for w in result["warnings"]))


class BackendGuardTests(SimpleTestCase):
    def _ids(self, settings):
        return {error.id for error in check_email_backend(None)}

    def test_smtp_is_rejected_even_in_development(self):
        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"
        ):
            self.assertIn("core.email.E001", self._ids(None))

    @override_settings(
        DEBUG=False,
        EMAIL_PROVIDER="mailjet",
        EMAIL_BACKEND="anymail.backends.mailjet.EmailBackend",
        MAILJET_API_KEY="key",
        MAILJET_SECRET_KEY="secret",
    )
    def test_valid_mailjet_production_configuration_passes(self):
        self.assertEqual(check_email_backend(None), [])

    @override_settings(
        DEBUG=False,
        EMAIL_PROVIDER="mailjet",
        EMAIL_BACKEND="anymail.backends.mailjet.EmailBackend",
        MAILJET_API_KEY="",
        MAILJET_SECRET_KEY="",
    )
    def test_production_mailjet_requires_both_keys(self):
        self.assertIn("core.email.E004", self._ids(None))

    @override_settings(
        DEBUG=False,
        EMAIL_PROVIDER="mailjet",
        EMAIL_BACKEND="anymail.backends.resend.EmailBackend",
    )
    def test_backend_must_match_the_selected_provider(self):
        self.assertIn("core.email.E003", self._ids(None))

    @override_settings(
        DEBUG=False,
        EMAIL_PROVIDER="console",
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
    )
    def test_console_is_rejected_in_production(self):
        self.assertIn("core.email.E002", self._ids(None))
