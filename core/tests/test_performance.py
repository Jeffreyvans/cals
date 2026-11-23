"""Query-count and pagination guards for the busiest list screens.

The numbers are deliberately exact: a new N+1 in these views makes the test
fail instead of quietly making every page slower.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Institution, StaffProfile
from core.roles import ensure_roles
from core.tenancy import use_institution

User = get_user_model()


class StaffListPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha School", slug="alpha", type="secondary")
        ensure_roles(cls.inst)
        cls.admin = User.objects.create_user(
            "boss", password="pw123456", role="admin", is_superuser=True,
            institution=cls.inst)
        with use_institution(cls.inst):
            for index in range(30):
                user = User.objects.create_user(
                    f"teacher{index}", password="pw123456", role="lecturer",
                    first_name=f"Teacher{index:02d}", last_name="Zulu",
                    institution=cls.inst)
                StaffProfile.objects.create(
                    institution=cls.inst, user=user,
                    employee_no=f"EMP-{index:03d}", job_title="Teacher")

    def test_the_staff_list_is_paginated_at_25(self):
        self.client.force_login(self.admin)

        first = self.client.get(reverse("staff_list"))
        second = self.client.get(reverse("staff_list"), {"page": 2})

        self.assertEqual(len(first.context["page_obj"].object_list), 25)
        self.assertEqual(len(second.context["page_obj"].object_list), 5)

    def test_the_staff_list_uses_a_fixed_number_of_queries(self):
        # Start from a cold cache so an earlier test cannot hide a query: the
        # institution/branding lookups are cached for real requests.
        cache.clear()
        self.client.force_login(self.admin)

        # 25 profiles, their departments and the permission checks: the count
        # must not grow when the page has more rows. One extra query loads
        # the optional platform logo settings on a cold cache, and four more
        # load the school/department/level/class filter choices added with the
        # configurable academic hierarchy. A new N+1 still breaks this guard.
        with self.assertNumQueries(21):
            self.client.get(reverse("staff_list"))

    def test_the_measurement_command_reports_tables_without_a_user(self):
        from io import StringIO

        output = StringIO()
        call_command("measure_performance", "--no-pages", stdout=output)

        self.assertIn("Large tables", output.getvalue())
        self.assertIn("accounts_user", output.getvalue())
