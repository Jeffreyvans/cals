import csv

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from core.access import user_has_any_code
from core.audit import log_permission_denied
from core.modules import is_enabled

from .models import FuelLog, Route, TransportAssignment, Vehicle


def _guard(request):
    if not is_enabled("transport", request.institution):
        raise Http404("This module is not switched on for your institution.")
    if not user_has_any_code(request.user, ("transport.manage",)):
        log_permission_denied(request, ("transport.manage",))
        raise PermissionDenied(
            "Your role does not allow this action. Ask an administrator for access."
        )


def pickup_rows(route, direction="morning"):
    """Active students on a route, in stop order, with the relevant time."""
    time_field = "pickup_time" if direction == "morning" else "dropoff_time"
    assignments = (
        TransportAssignment.objects.filter(
            route=route, status=TransportAssignment.Status.ACTIVE
        )
        .select_related("student__user", "stop", "vehicle")
        .order_by("stop__order", "stop__name", "student__user__last_name")
    )
    rows = []
    for assignment in assignments:
        stop = assignment.stop
        rows.append(
            {
                "student": assignment.student,
                "stop": stop.name if stop else "Unassigned stop",
                "time": getattr(stop, time_field) if stop else None,
                "guardian_phone": assignment.student.guardian_phone,
                "vehicle": assignment.vehicle,
            }
        )
    return rows


@login_required
def pickup_list(request):
    _guard(request)
    direction = request.GET.get("direction") or "morning"
    if direction not in ("morning", "afternoon"):
        direction = "morning"
    route_id = request.GET.get("route") or ""
    route = None
    if route_id:
        route = get_object_or_404(Route.objects.all(), pk=route_id)
    elif Route.objects.exists():
        route = Route.objects.order_by("name").first()
    rows = pickup_rows(route, direction) if route else []
    return render(
        request,
        "transport/pickup_list.html",
        {
            "route": route,
            "routes": Route.objects.all(),
            "direction": direction,
            "rows": rows,
            "hub_url": reverse("transport:hub"),
            "export_url": reverse("transport:pickup_export")
            + (f"?route={route.pk}&direction={direction}" if route else ""),
        },
    )


@login_required
def pickup_export(request):
    _guard(request)
    direction = request.GET.get("direction") or "morning"
    route_id = request.GET.get("route") or ""
    if route_id:
        route = get_object_or_404(Route.objects.all(), pk=route_id)
    else:
        route = Route.objects.order_by("name").first()
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="pickup-list.csv"'
    writer = csv.writer(response)
    writer.writerow(["Route", "Direction", "Stop", "Time", "Student", "Guardian phone"])
    for row in pickup_rows(route, direction) if route else []:
        writer.writerow(
            [
                route.name,
                direction.title(),
                row["stop"],
                row["time"].strftime("%H:%M") if row["time"] else "",
                row["student"],
                row["guardian_phone"],
            ]
        )
    return response


def fuel_rows(start=None, end=None, vehicle=None):
    logs = FuelLog.objects.select_related("vehicle")
    if start:
        logs = logs.filter(date__gte=start)
    if end:
        logs = logs.filter(date__lte=end)
    if vehicle is not None:
        logs = logs.filter(vehicle=vehicle)
    rows = []
    for row in (
        logs.values("vehicle__registration", "vehicle__id")
        .annotate(litres=Sum("litres"), cost=Sum("cost"))
        .order_by("vehicle__registration")
    ):
        rows.append(row)
    return rows


@login_required
def fuel_report(request):
    _guard(request)
    today = timezone.localdate()
    start = _parse_date(request.GET.get("start")) or today.replace(day=1)
    end = _parse_date(request.GET.get("end")) or today
    vehicle_id = request.GET.get("vehicle") or ""
    vehicle = get_object_or_404(Vehicle.objects.all(), pk=vehicle_id) if vehicle_id else None
    rows = fuel_rows(start, end, vehicle)
    totals = {
        "litres": sum((row["litres"] or 0 for row in rows), 0),
        "cost": sum((row["cost"] or 0 for row in rows), 0),
    }
    return render(
        request,
        "transport/fuel_report.html",
        {
            "rows": rows,
            "totals": totals,
            "start": start,
            "end": end,
            "vehicle": vehicle,
            "vehicles": Vehicle.objects.all(),
            "hub_url": reverse("transport:hub"),
            "export_url": reverse("transport:fuel_export")
            + f"?start={start}&end={end}"
            + (f"&vehicle={vehicle.pk}" if vehicle else ""),
        },
    )


@login_required
def fuel_export(request):
    _guard(request)
    start = _parse_date(request.GET.get("start"))
    end = _parse_date(request.GET.get("end"))
    vehicle_id = request.GET.get("vehicle") or ""
    vehicle = get_object_or_404(Vehicle.objects.all(), pk=vehicle_id) if vehicle_id else None
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="fuel-log-report.csv"'
    writer = csv.writer(response)
    writer.writerow(["Vehicle", "Litres", "Cost"])
    for row in fuel_rows(start, end, vehicle):
        writer.writerow(
            [row["vehicle__registration"], row["litres"] or 0, row["cost"] or 0]
        )
    return response


def _parse_date(value):
    if not value:
        return None
    try:
        return timezone.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
