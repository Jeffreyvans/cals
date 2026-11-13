from core.access import user_has_code
from core.modules import module_label

from .forms import CounsellingSessionForm, ReferralForm
from .models import CounsellingSession, Referral


def _session_rows(request):
    rows = CounsellingSession.objects.all()
    if user_has_code(request.user, "counselling.view_all_notes"):
        return rows
    return rows.filter(counsellor=request.user)


MODULES = {
    "counselling": {
        "title": module_label("counselling"),
        "description": "Confidential counselling sessions and referrals.",
        "permissions": ("counselling.view_notes", "counselling.manage"),
        "sensitive": True,
        "kinds": [
            {
                "slug": "sessions",
                "title": "Sessions",
                "description": "Counselling notes. Every open is logged.",
                "model": CounsellingSession,
                "form": CounsellingSessionForm,
                "queryset": _session_rows,
                "sensitive": True,
                "search": ["student__student_no", "topic", "notes"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("When", lambda o: o.session_at),
                    ("Topic", lambda o: o.topic),
                    ("Counsellor", lambda o: o.counsellor or "-"),
                ],
            },
            {
                "slug": "referrals",
                "title": "Referrals",
                "description": "Students referred to outside support.",
                "model": Referral,
                "form": ReferralForm,
                "sensitive": True,
                "search": ["student__student_no", "referred_to", "reason"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("Referred to", lambda o: o.referred_to),
                    ("Date", lambda o: o.referred_on),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
        ],
    }
}
