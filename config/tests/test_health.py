import json
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from config.health import health_check


class HealthCheckTests(TestCase):
    def test_health_reports_ok_when_the_database_answers(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_health_reports_503_when_the_database_is_unreachable(self):
        request = RequestFactory().get("/health/")
        with patch("config.health.connection.cursor", side_effect=Exception("down")):
            response = health_check(request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content)["database"], "error")
