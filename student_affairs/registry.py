from core.modules import module_label

from .forms import (
    ClubForm,
    ClubMembershipForm,
    DisciplineCaseForm,
    EventForm,
    EventRegistrationForm,
)
from .models import Club, ClubMembership, DisciplineCase, Event, EventRegistration

MODULES = {
    "student_affairs": {
        "title": module_label("student_affairs"),
        "description": "Discipline, clubs, events and student life.",
        "permissions": ("student_affairs.manage",),
        "kinds": [
            {
                "slug": "discipline",
                "title": "Discipline cases",
                "description": "Cases, hearings and outcomes.",
                "model": DisciplineCase,
                "form": DisciplineCaseForm,
                "search": ["student__student_no", "category", "description"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("Date", lambda o: o.date),
                    ("Category", lambda o: o.category),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "clubs",
                "title": "Clubs",
                "description": "Clubs and their patrons.",
                "model": Club,
                "form": ClubForm,
                "search": ["name", "description"],
                "columns": [
                    ("Club", lambda o: o.name),
                    ("Patron", lambda o: o.patron or "-"),
                    ("Meeting day", lambda o: o.meeting_day or "-"),
                    ("Active", lambda o: "Yes" if o.is_active else "No"),
                ],
            },
            {
                "slug": "memberships",
                "title": "Club memberships",
                "description": "Students in each club.",
                "model": ClubMembership,
                "form": ClubMembershipForm,
                "search": ["club__name", "student__student_no"],
                "columns": [
                    ("Club", lambda o: o.club),
                    ("Student", lambda o: o.student),
                    ("Role", lambda o: o.get_role_display()),
                    ("Joined", lambda o: o.joined_on),
                ],
            },
            {
                "slug": "events",
                "title": "Events",
                "description": "School events and registrations.",
                "model": Event,
                "form": EventForm,
                "search": ["title", "venue", "description"],
                "columns": [
                    ("Event", lambda o: o.title),
                    ("Date", lambda o: o.date),
                    ("Venue", lambda o: o.venue or "-"),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "event-registrations",
                "title": "Event registrations",
                "description": "Students registered for events.",
                "model": EventRegistration,
                "form": EventRegistrationForm,
                "search": ["event__title", "student__student_no"],
                "columns": [
                    ("Event", lambda o: o.event),
                    ("Student", lambda o: o.student),
                    ("Registered", lambda o: o.registered_on),
                    ("Attended", lambda o: "Yes" if o.attended else "No"),
                ],
            },
        ],
    }
}
