from core.modules import module_label

from .forms import BoarderForm, ExeatForm, HostelForm, RollCallForm, RoomForm
from .models import Boarder, Exeat, Hostel, RollCall, Room

MODULES = {
    "boarding": {
        "title": module_label("boarding"),
        "description": "Houses, rooms, boarders, roll call and exeats.",
        "permissions": ("boarding.manage",),
        "links": [("Automatic room allocation", "boarding:allocate")],
        "kinds": [
            {
                "slug": "hostels",
                "title": "Hostels",
                "description": "Boarding houses.",
                "model": Hostel,
                "form": HostelForm,
                "search": ["name"],
                "columns": [
                    ("Hostel", lambda o: o.name),
                    ("Gender", lambda o: o.get_gender_display()),
                    ("Capacity", lambda o: o.capacity),
                    ("Matron", lambda o: o.matron or "-"),
                ],
            },
            {
                "slug": "rooms",
                "title": "Rooms",
                "description": "Rooms in each hostel.",
                "model": Room,
                "form": RoomForm,
                "search": ["hostel__name", "name"],
                "columns": [
                    ("Room", lambda o: o.name),
                    ("Hostel", lambda o: o.hostel),
                    ("Capacity", lambda o: o.capacity),
                ],
            },
            {
                "slug": "boarders",
                "title": "Boarders",
                "description": "Students living in boarding.",
                "model": Boarder,
                "form": BoarderForm,
                "search": ["student__student_no", "student__user__last_name", "room__name"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("Room", lambda o: o.room),
                    ("Bed", lambda o: o.bed or "-"),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "roll-calls",
                "title": "Roll call",
                "description": "Daily boarding attendance.",
                "model": RollCall,
                "form": RollCallForm,
                "search": ["boarder__student__student_no", "hostel__name", "note"],
                "columns": [
                    ("Date", lambda o: o.date),
                    ("Hostel", lambda o: o.hostel),
                    ("Boarder", lambda o: o.boarder),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "exeats",
                "title": "Exeats",
                "description": "Approved nights away.",
                "model": Exeat,
                "form": ExeatForm,
                "search": ["boarder__student__student_no", "reason", "destination"],
                "columns": [
                    ("Boarder", lambda o: o.boarder),
                    ("Reason", lambda o: o.reason),
                    ("Starts", lambda o: o.starts_at),
                    ("Ends", lambda o: o.ends_at),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
        ],
    }
}
