from datetime import date, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from academics.factories import StudentFactory
from accounts.factories import UserFactory
from core.models import Institution
from core.modules import set_module
from core.roles import ensure_roles
from transport.models import (
    FuelLog,
    Route,
    Stop,
    TransportAssignment,
    Vehicle,
)
from transport.views import fuel_rows


class TransportTests(TestCase):
    def test_a_stop_must_belong_to_the_assigned_route(self):
        first = Route.objects.create(name="North")
        second = Route.objects.create(name="South")
        stop = Stop.objects.create(route=second, name="Market")
        assignment = TransportAssignment(route=first, stop=stop)
        with self.assertRaises(ValidationError):
            assignment.clean()

        assignment.route = second
        assignment.clean()

    def test_a_route_keeps_its_stops_in_order(self):
        route = Route.objects.create(name="East")
        Stop.objects.create(route=route, name="Second", order=2)
        Stop.objects.create(route=route, name="First", order=1)
        self.assertEqual([stop.name for stop in route.stops.all()], ["First", "Second"])


class TransportReportingTests(TestCase):
    def setUp(self):
        self.institution = Institution.objects.get(slug="default")
        ensure_roles(self.institution)
        set_module(self.institution, "transport", True)
        self.admin = UserFactory(
            username="transport_admin", role="admin", institution=self.institution
        )
        self.route = Route.objects.create(
            institution=self.institution, name="North", code="N1"
        )
        self.stop = Stop.objects.create(
            institution=self.institution,
            route=self.route,
            name="Market Square",
            pickup_time=time(6, 45),
            dropoff_time=time(16, 30),
            order=1,
        )
        self.vehicle = Vehicle.objects.create(
            institution=self.institution, registration="ABC 123", capacity=40
        )
        user = UserFactory(
            username="transport_student", role="student", institution=self.institution
        )
        self.student = StudentFactory(
            user=user, status="active", guardian_phone="0770000000"
        )
        TransportAssignment.objects.create(
            institution=self.institution,
            student=self.student,
            route=self.route,
            stop=self.stop,
            vehicle=self.vehicle,
        )

    def test_the_pickup_list_orders_students_by_stop(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("transport:pickup_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Market Square")
        self.assertContains(response, "06:45")
        self.assertContains(response, "0770000000")

    def test_the_pickup_list_exports_a_csv(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("transport:pickup_export"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("Market Square", response.content.decode())

    def test_fuel_purchases_are_totalled_per_vehicle(self):
        FuelLog.objects.create(
            institution=self.institution,
            vehicle=self.vehicle,
            date=date(2026, 3, 1),
            litres=Decimal("40.50"),
            cost=Decimal("65.00"),
        )
        FuelLog.objects.create(
            institution=self.institution,
            vehicle=self.vehicle,
            date=date(2026, 3, 2),
            litres=Decimal("20.00"),
            cost=Decimal("32.00"),
        )
        rows = fuel_rows(date(2026, 3, 1), date(2026, 3, 31), self.vehicle)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["litres"], Decimal("60.50"))
        self.assertEqual(rows[0]["cost"], Decimal("97.00"))

    def test_the_fuel_report_and_export_show_the_totals(self):
        FuelLog.objects.create(
            institution=self.institution,
            vehicle=self.vehicle,
            date=date(2026, 3, 1),
            litres=Decimal("40.50"),
            cost=Decimal("65.00"),
        )
        self.client.force_login(self.admin)
        page = self.client.get(
            reverse("transport:fuel_report"),
            {"start": "2026-03-01", "end": "2026-03-31"},
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "40.50")
        exported = self.client.get(
            reverse("transport:fuel_export"),
            {"start": "2026-03-01", "end": "2026-03-31"},
        )
        self.assertEqual(exported.status_code, 200)
        self.assertIn("ABC 123", exported.content.decode())
