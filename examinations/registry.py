from core.modules import module_label

from .forms import ExamCandidateForm, ExamForm, ExamPaperForm, InvigilationDutyForm
from .models import Exam, ExamCandidate, ExamPaper, InvigilationDuty

MODULES = {
    "examinations": {
        "title": module_label("exams"),
        "description": "Exam sittings, papers, candidates, marks and invigilation.",
        "flag": "exams",
        "permissions": ("exams.manage", "exams.invigilate"),
        "links": [
            ("Exam clash check", "examinations:clash_check"),
            ("Candidate export", "examinations:candidate_export"),
            ("ZIMSEC result export", "examinations:zimsec_export"),
            ("HEXCO result export", "examinations:hexco_export"),
        ],
        "kinds": [
            {
                "slug": "sittings",
                "title": "Exam sittings",
                "description": "Exam sessions for a term.",
                "model": Exam,
                "form": ExamForm,
                "search": ["name", "term__name"],
                "row_action": ("examinations:exam_manage", "Manage"),
                "columns": [
                    ("Exam", lambda o: o.name),
                    ("Term", lambda o: o.term),
                    ("Starts", lambda o: o.starts_on),
                    ("Ends", lambda o: o.ends_on),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "papers",
                "title": "Papers",
                "description": "Timetabled papers.",
                "model": ExamPaper,
                "form": ExamPaperForm,
                "search": ["exam__name", "subject__name", "course__title", "venue"],
                "row_action": ("examinations:seating_plan", "Seating"),
                "columns": [
                    ("Exam", lambda o: o.exam),
                    ("Subject / course", lambda o: o.subject or o.course),
                    ("Date", lambda o: o.date),
                    ("Time", lambda o: o.start_time),
                    ("Venue", lambda o: o.venue or "-"),
                ],
            },
            {
                "slug": "candidates",
                "title": "Candidates",
                "description": "Candidate numbers, seats and marks.",
                "model": ExamCandidate,
                "form": ExamCandidateForm,
                "search": ["student__student_no", "candidate_no", "paper__exam__name"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("Paper", lambda o: o.paper),
                    ("Candidate no", lambda o: o.candidate_no or "-"),
                    ("Mark", lambda o: o.mark if o.mark is not None else "-"),
                    ("Published", lambda o: "Yes" if o.published else "No"),
                ],
            },
            {
                "slug": "invigilation",
                "title": "Invigilation",
                "description": "Invigilator duties.",
                "model": InvigilationDuty,
                "form": InvigilationDutyForm,
                "search": ["paper__exam__name", "room", "invigilator__last_name"],
                "columns": [
                    ("Paper", lambda o: o.paper),
                    ("Invigilator", lambda o: o.invigilator),
                    ("Room", lambda o: o.room or "-"),
                    ("Confirmed", lambda o: "Yes" if o.confirmed else "No"),
                ],
            },
        ],
    }
}
