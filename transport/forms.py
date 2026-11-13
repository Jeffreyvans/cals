from core.forms import form_for

from .models import FuelLog, Route, Stop, TransportAssignment, TripLog, Vehicle

RouteForm = form_for(Route)
VehicleForm = form_for(Vehicle)
StopForm = form_for(Stop)
TransportAssignmentForm = form_for(TransportAssignment, exclude=["fee_invoice"])
TripLogForm = form_for(TripLog)
FuelLogForm = form_for(FuelLog)
