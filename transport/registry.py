from core.modules import module_label

from .forms import (
    FuelLogForm,
    RouteForm,
    StopForm,
    TransportAssignmentForm,
    TripLogForm,
    VehicleForm,
)
from .models import FuelLog, Route, Stop, TransportAssignment, TripLog, Vehicle

MODULES = {
    "transport": {
        "title": module_label("transport"),
        "description": "Routes, vehicles, stops and student transport.",
        "permissions": ("transport.manage",),
        "links": [
            ("Pick-up lists", "transport:pickup_list"),
            ("Fuel log report", "transport:fuel_report"),
        ],
        "kinds": [
            {
                "slug": "routes",
                "title": "Routes",
                "description": "Bus routes and fees.",
                "model": Route,
                "form": RouteForm,
                "search": ["name", "code", "description"],
                "columns": [
                    ("Route", lambda o: o.name),
                    ("Code", lambda o: o.code or "-"),
                    ("Fee", lambda o: o.fee),
                    ("Active", lambda o: "Yes" if o.is_active else "No"),
                ],
            },
            {
                "slug": "vehicles",
                "title": "Vehicles",
                "description": "Buses and their drivers.",
                "model": Vehicle,
                "form": VehicleForm,
                "search": ["registration", "driver_name"],
                "columns": [
                    ("Registration", lambda o: o.registration),
                    ("Capacity", lambda o: o.capacity),
                    ("Driver", lambda o: o.driver_name or "-"),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "stops",
                "title": "Stops",
                "description": "Pick-up and drop-off points.",
                "model": Stop,
                "form": StopForm,
                "search": ["route__name", "name"],
                "columns": [
                    ("Route", lambda o: o.route),
                    ("Stop", lambda o: o.name),
                    ("Pick-up", lambda o: o.pickup_time or "-"),
                    ("Drop-off", lambda o: o.dropoff_time or "-"),
                ],
            },
            {
                "slug": "assignments",
                "title": "Student transport",
                "description": "Students assigned to routes.",
                "model": TransportAssignment,
                "form": TransportAssignmentForm,
                "search": ["student__student_no", "route__name", "stop__name"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("Route", lambda o: o.route),
                    ("Stop", lambda o: o.stop or "-"),
                    ("Term", lambda o: o.term or "-"),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "fuel",
                "title": "Fuel log",
                "description": "Fuel purchases per vehicle.",
                "model": FuelLog,
                "form": FuelLogForm,
                "search": ["vehicle__registration", "driver", "reference"],
                "columns": [
                    ("Date", lambda o: o.date),
                    ("Vehicle", lambda o: o.vehicle),
                    ("Litres", lambda o: o.litres),
                    ("Cost", lambda o: o.cost),
                    ("Driver", lambda o: o.driver or "-"),
                ],
            },
            {
                "slug": "trips",
                "title": "Trip log",
                "description": "Completed trips.",
                "model": TripLog,
                "form": TripLogForm,
                "search": ["route__name", "vehicle__registration", "driver"],
                "columns": [
                    ("Date", lambda o: o.date),
                    ("Route", lambda o: o.route),
                    ("Vehicle", lambda o: o.vehicle),
                    ("Passengers", lambda o: o.passengers),
                ],
            },
        ],
    }
}
