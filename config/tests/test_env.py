import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from config.env import env_bool, env_int, env_list, env_str, load_dotenv

BASE = Path(__file__).resolve().parents[2]


class EnvHelperTests(SimpleTestCase):
    keys = ("EDUSPHERE_TEST_FLAG", "EDUSPHERE_TEST_LIST", "EDUSPHERE_TEST_NUM")

    def setUp(self):
        for key in self.keys:
            os.environ.pop(key, None)

    def tearDown(self):
        for key in self.keys:
            os.environ.pop(key, None)

    def test_env_str_returns_default_when_unset(self):
        self.assertEqual(env_str("EDUSPHERE_TEST_FLAG", "fallback"), "fallback")
        os.environ["EDUSPHERE_TEST_FLAG"] = "set"
        self.assertEqual(env_str("EDUSPHERE_TEST_FLAG", "fallback"), "set")

    def test_env_bool_parses_truthy_and_falsy_spellings(self):
        for raw in ("1", "true", "TRUE", "Yes", "on"):
            os.environ["EDUSPHERE_TEST_FLAG"] = raw
            self.assertTrue(env_bool("EDUSPHERE_TEST_FLAG", False), raw)
        for raw in ("0", "false", "FALSE", "No", "off"):
            os.environ["EDUSPHERE_TEST_FLAG"] = raw
            self.assertFalse(env_bool("EDUSPHERE_TEST_FLAG", True), raw)

    def test_env_bool_uses_default_when_unset(self):
        self.assertTrue(env_bool("EDUSPHERE_TEST_FLAG", True))
        self.assertFalse(env_bool("EDUSPHERE_TEST_FLAG", False))

    def test_env_bool_rejects_typos(self):
        os.environ["EDUSPHERE_TEST_FLAG"] = "tru"
        with self.assertRaisesMessage(ValueError, "EDUSPHERE_TEST_FLAG"):
            env_bool("EDUSPHERE_TEST_FLAG")

    def test_env_list_splits_and_trims(self):
        os.environ["EDUSPHERE_TEST_LIST"] = " one , two,,three "
        self.assertEqual(env_list("EDUSPHERE_TEST_LIST"),
                         ["one", "two", "three"])

    def test_env_list_empty_uses_default(self):
        self.assertEqual(env_list("EDUSPHERE_TEST_LIST", ["a", "b"]), ["a", "b"])
        os.environ["EDUSPHERE_TEST_LIST"] = "   "
        self.assertEqual(env_list("EDUSPHERE_TEST_LIST", ["a"]), ["a"])

    def test_env_int_parses_and_rejects_garbage(self):
        os.environ["EDUSPHERE_TEST_NUM"] = "31536000"
        self.assertEqual(env_int("EDUSPHERE_TEST_NUM"), 31536000)
        self.assertEqual(env_int("EDUSPHERE_TEST_NUM", 7), 31536000)
        os.environ["EDUSPHERE_TEST_NUM"] = "soon"
        with self.assertRaisesMessage(ValueError, "EDUSPHERE_TEST_NUM"):
            env_int("EDUSPHERE_TEST_NUM")
        os.environ["EDUSPHERE_TEST_NUM"] = "  "
        self.assertEqual(env_int("EDUSPHERE_TEST_NUM", 5), 5)

    def load(self, content):
        fd, name = tempfile.mkstemp(suffix=".env")
        os.close(fd)
        try:
            Path(name).write_text(content, encoding="utf-8")
            load_dotenv(name)
        finally:
            os.unlink(name)

    def test_load_dotenv_sets_missing_variables(self):
        self.load("EDUSPHERE_TEST_FLAG=from-file\n"
                  '# a comment\n'
                  'EDUSPHERE_TEST_LIST="one, two"\n')
        self.assertEqual(os.environ["EDUSPHERE_TEST_FLAG"], "from-file")
        self.assertEqual(os.environ["EDUSPHERE_TEST_LIST"], "one, two")

    def test_load_dotenv_keeps_existing_environment(self):
        os.environ["EDUSPHERE_TEST_FLAG"] = "from-shell"
        self.load("EDUSPHERE_TEST_FLAG=from-file\n")
        self.assertEqual(os.environ["EDUSPHERE_TEST_FLAG"], "from-shell")

    def test_load_dotenv_strips_quotes_and_trailing_comments(self):
        self.load("EDUSPHERE_TEST_FLAG='quoted'\n"
                  "EDUSPHERE_TEST_NUM=9000 # seconds\n")
        self.assertEqual(os.environ["EDUSPHERE_TEST_FLAG"], "quoted")
        self.assertEqual(os.environ["EDUSPHERE_TEST_NUM"], "9000")

    def test_load_dotenv_missing_file_is_harmless(self):
        load_dotenv(BASE / "no-such-file.env")   # must not raise


class SettingsBootTests(SimpleTestCase):
    """The production fail-fast rules, exercised through a real process."""

    def run_manage(self, **overrides):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("EDUSPHERE_")}
        env.pop("DJANGO_SETTINGS_MODULE", None)
        env.update({k: str(v) for k, v in overrides.items()})
        return subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=BASE, env=env, capture_output=True, text=True, timeout=60)

    def test_dev_boots_with_no_environment_at_all(self):
        result = self.run_manage()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_without_secret_key_refuses_to_boot(self):
        result = self.run_manage(EDUSPHERE_DEBUG="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EDUSPHERE_SECRET_KEY", result.stderr)

    def test_production_without_allowed_hosts_refuses_to_boot(self):
        result = self.run_manage(EDUSPHERE_DEBUG="0",
                                 EDUSPHERE_SECRET_KEY="x" * 50)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EDUSPHERE_ALLOWED_HOSTS", result.stderr)

    def test_production_boots_once_key_and_hosts_are_set(self):
        result = self.run_manage(EDUSPHERE_DEBUG="0",
                                 EDUSPHERE_SECRET_KEY="x" * 50,
                                 EDUSPHERE_ALLOWED_HOSTS="fees.school.example",
                                 EDUSPHERE_INSTITUTION_NAME="Test Academy",
                                 EDUSPHERE_CURRENCY="R")
        self.assertEqual(result.returncode, 0, result.stderr)
