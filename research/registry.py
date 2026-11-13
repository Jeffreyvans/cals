from core.modules import module_label

from .forms import (
    EthicsApplicationForm,
    MilestoneForm,
    PublicationForm,
    ResearchProjectForm,
    SupervisionMeetingForm,
    ThesisForm,
)
from .models import (
    EthicsApplication,
    Milestone,
    Publication,
    ResearchProject,
    SupervisionMeeting,
    Thesis,
)

MODULES = {
    "research": {
        "title": module_label("research"),
        "description": "Research projects, supervision, ethics and publications.",
        "permissions": ("research.manage",),
        "kinds": [
            {
                "slug": "projects",
                "title": "Projects",
                "description": "Student research projects.",
                "model": ResearchProject,
                "form": ResearchProjectForm,
                "search": ["title", "student__student_no", "keywords"],
                "columns": [
                    ("Project", lambda o: o.title),
                    ("Student", lambda o: o.student),
                    ("Supervisor", lambda o: o.supervisor or "-"),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "milestones",
                "title": "Milestones",
                "description": "Dated checkpoints for each project.",
                "model": Milestone,
                "form": MilestoneForm,
                "search": ["project__title", "title", "notes"],
                "columns": [
                    ("Project", lambda o: o.project),
                    ("Milestone", lambda o: o.title),
                    ("Due", lambda o: o.due_date),
                    ("Status", lambda o: o.get_status_display()),
                    ("Overdue", lambda o: "Yes" if o.is_overdue else "No"),
                ],
            },
            {
                "slug": "theses",
                "title": "Theses",
                "description": "Final written theses and their review status.",
                "model": Thesis,
                "form": ThesisForm,
                "search": ["title", "project__title", "abstract"],
                "columns": [
                    ("Thesis", lambda o: o.title),
                    ("Project", lambda o: o.project),
                    ("Submitted", lambda o: o.submitted_on or "-"),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "meetings",
                "title": "Supervision meetings",
                "description": "Supervision notes and next dates.",
                "model": SupervisionMeeting,
                "form": SupervisionMeetingForm,
                "search": ["project__title", "agenda", "notes"],
                "columns": [
                    ("Project", lambda o: o.project),
                    ("Date", lambda o: o.date),
                    ("Next meeting", lambda o: o.next_meeting or "-"),
                ],
            },
            {
                "slug": "ethics",
                "title": "Ethics applications",
                "description": "Ethics review and approvals.",
                "model": EthicsApplication,
                "form": EthicsApplicationForm,
                "search": ["project__title", "reference"],
                "columns": [
                    ("Project", lambda o: o.project),
                    ("Reference", lambda o: o.reference or "-"),
                    ("Submitted", lambda o: o.submitted_on),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "publications",
                "title": "Publications",
                "description": "Outputs from research projects.",
                "model": Publication,
                "form": PublicationForm,
                "search": ["title", "venue", "doi"],
                "columns": [
                    ("Title", lambda o: o.title),
                    ("Project", lambda o: o.project),
                    ("Kind", lambda o: o.get_kind_display()),
                    ("Published", lambda o: o.published_on or "-"),
                ],
            },
        ],
    }
}
