from core.forms import form_for

from .models import Boarder, Exeat, Hostel, RollCall, Room

HostelForm = form_for(Hostel)
RoomForm = form_for(Room)
BoarderForm = form_for(Boarder)
RollCallForm = form_for(RollCall, exclude=["checked_by"])
ExeatForm = form_for(Exeat, exclude=["approved_by", "returned_at"])
