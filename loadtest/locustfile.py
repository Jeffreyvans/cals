"""Read-only EduSphere load test: login, dashboard, search, payment page.

Run it against a staging copy, never the live school database. See
LOAD_TESTING.md for the exact commands, sizing table and pass criteria.
"""

import os
import re

from locust import HttpUser, between, task

CSRF_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')
USERNAME = os.environ.get("LOAD_USERNAME", "")
PASSWORD = os.environ.get("LOAD_PASSWORD", "")
STUDENT_ID = os.environ.get("LOAD_STUDENT_ID", "")
SEARCH_TERM = os.environ.get("LOAD_SEARCH_TERM", "a")


class StaffUser(HttpUser):
    wait_time = between(1, 4)
    host = os.environ.get("LOAD_HOST", "http://127.0.0.1:8000")

    def on_start(self):
        page = self.client.get("/login/", name="login page")
        match = CSRF_RE.search(page.text)
        token = match.group(1) if match else ""
        self.client.post(
            "/login/",
            data={
                "username": USERNAME,
                "password": PASSWORD,
                "csrfmiddlewaretoken": token,
            },
            headers={"Referer": f"{self.host}/login/"},
            name="login submit",
        )

    @task(4)
    def dashboard(self):
        self.client.get("/dashboard/", name="dashboard")

    @task(3)
    def student_search(self):
        self.client.get(f"/students/?q={SEARCH_TERM}", name="student search")

    @task(2)
    def admissions(self):
        self.client.get("/admissions/applications/", name="admissions list")

    @task(2)
    def student_record(self):
        if STUDENT_ID:
            self.client.get(f"/students/{STUDENT_ID}/", name="student record")

    @task(1)
    def payment_page(self):
        if STUDENT_ID:
            self.client.get(f"/finance/pay/{STUDENT_ID}/", name="payment page")

    @task(1)
    def public_home(self):
        self.client.get("/", name="public home")
