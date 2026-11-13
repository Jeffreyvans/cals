from core.modules import module_label

from .forms import (
    AttachmentGradeForm,
    AttachmentLogForm,
    CareerEventForm,
    EmployerForm,
    PlacementForm,
    SupervisorReportForm,
)
from .models import (
    AttachmentGrade,
    AttachmentLog,
    CareerEvent,
    Employer,
    Placement,
    SupervisorReport,
)

MODULES = {
    "careers": {
        "title": module_label("careers_attachment"),
        "description": "Employers, placements, attachment logs and career events.",
        "flag": "careers_attachment",
        "permissions": ("careers.manage",),
        "kinds": [
            {
                "slug": "employers",
                "title": "Employers",
                "description": "Partner organisations.",
                "model": Employer,
                "form": EmployerForm,
                "search": ["name", "sector", "contact_name"],
                "columns": [
                    ("Employer", lambda o: o.name),
                    ("Sector", lambda o: o.sector or "-"),
                    ("Contact", lambda o: o.contact_name or "-"),
                    ("Phone", lambda o: o.phone or "-"),
                ],
            },
            {
                "slug": "placements",
                "title": "Placements",
                "description": "Work placements and attachments.",
                "model": Placement,
                "form": PlacementForm,
                "search": ["student__student_no", "employer__name", "title"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("Employer", lambda o: o.employer),
                    ("Title", lambda o: o.title),
                    ("Start", lambda o: o.start_date),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "logs",
                "title": "Attachment logs",
                "description": "Daily attachment records.",
                "model": AttachmentLog,
                "form": AttachmentLogForm,
                "search": ["placement__student__student_no", "activity"],
                "columns": [
                    ("Placement", lambda o: o.placement),
                    ("Date", lambda o: o.date),
                    ("Hours", lambda o: o.hours),
                    ("Activity", lambda o: o.activity),
                ],
            },
            {
                "slug": "supervisor-reports",
                "title": "Supervisor reports",
                "description": "Employer assessments of attachments.",
                "model": SupervisorReport,
                "form": SupervisorReportForm,
                "search": ["placement__student__student_no", "supervisor_name", "strengths"],
                "columns": [
                    ("Placement", lambda o: o.placement),
                    ("Date", lambda o: o.date),
                    ("Supervisor", lambda o: o.supervisor_name),
                    ("Rating", lambda o: o.rating or "-"),
                ],
            },
            {
                "slug": "grades",
                "title": "Attachment grades",
                "description": "Final marks and letters for placements.",
                "model": AttachmentGrade,
                "form": AttachmentGradeForm,
                "search": ["placement__student__student_no", "comments"],
                "columns": [
                    ("Placement", lambda o: o.placement),
                    ("Mark", lambda o: o.mark),
                    ("Letter", lambda o: o.letter),
                    ("Graded on", lambda o: o.graded_on),
                ],
            },
            {
                "slug": "events",
                "title": "Career events",
                "description": "Career fairs and talks.",
                "model": CareerEvent,
                "form": CareerEventForm,
                "search": ["title", "venue", "description"],
                "columns": [
                    ("Event", lambda o: o.title),
                    ("Date", lambda o: o.date),
                    ("Venue", lambda o: o.venue or "-"),
                ],
            },
        ],
    }
}
