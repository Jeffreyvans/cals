from core.modules import module_label

from .forms import (
    EquipmentForm,
    EquipmentLoanForm,
    FixtureForm,
    HousePointForm,
    TeamForm,
    TeamMemberForm,
)
from .models import Equipment, EquipmentLoan, Fixture, HousePoint, Team, TeamMember

MODULES = {
    "sports": {
        "title": module_label("sports"),
        "description": "Teams, fixtures, results and house points.",
        "permissions": ("sports.manage",),
        "kinds": [
            {
                "slug": "teams",
                "title": "Teams",
                "description": "School teams and coaches.",
                "model": Team,
                "form": TeamForm,
                "search": ["name", "sport", "age_group"],
                "columns": [
                    ("Team", lambda o: o.name),
                    ("Sport", lambda o: o.sport),
                    ("Age group", lambda o: o.age_group or "-"),
                    ("Coach", lambda o: o.coach or "-"),
                ],
            },
            {
                "slug": "members",
                "title": "Team members",
                "description": "Players in each team.",
                "model": TeamMember,
                "form": TeamMemberForm,
                "search": ["team__name", "student__student_no"],
                "columns": [
                    ("Team", lambda o: o.team),
                    ("Student", lambda o: o.student),
                    ("Position", lambda o: o.position or "-"),
                ],
            },
            {
                "slug": "fixtures",
                "title": "Fixtures",
                "description": "Matches and results.",
                "model": Fixture,
                "form": FixtureForm,
                "search": ["team__name", "opponent", "venue"],
                "columns": [
                    ("Team", lambda o: o.team),
                    ("Opponent", lambda o: o.opponent),
                    ("Date", lambda o: o.date),
                    ("Score", lambda o: f"{o.score_for}-{o.score_against}"),
                    ("Result", lambda o: o.get_result_display()),
                ],
            },
            {
                "slug": "equipment",
                "title": "Equipment",
                "description": "Sports equipment stock and condition.",
                "model": Equipment,
                "form": EquipmentForm,
                "search": ["name", "sport", "code"],
                "columns": [
                    ("Equipment", lambda o: o.name),
                    ("Sport", lambda o: o.sport or "-"),
                    ("Code", lambda o: o.code or "-"),
                    ("Available", lambda o: f"{o.available}/{o.total}"),
                    ("Condition", lambda o: o.get_condition_display()),
                ],
            },
            {
                "slug": "equipment-loans",
                "title": "Equipment loans",
                "description": "Equipment issued to students and returned.",
                "model": EquipmentLoan,
                "form": EquipmentLoanForm,
                "search": ["equipment__name", "student__student_no", "student__user__last_name"],
                "columns": [
                    ("Equipment", lambda o: o.equipment),
                    ("Student", lambda o: o.student),
                    ("Borrowed", lambda o: o.borrowed_on),
                    ("Due", lambda o: o.due_on),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "house-points",
                "title": "House points",
                "description": "Points awarded to houses.",
                "model": HousePoint,
                "form": HousePointForm,
                "search": ["house", "reason", "student__student_no"],
                "columns": [
                    ("House", lambda o: o.house),
                    ("Student", lambda o: o.student or "-"),
                    ("Points", lambda o: o.points),
                    ("Reason", lambda o: o.reason),
                ],
            },
        ],
    }
}
