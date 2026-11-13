"""The setup wizard.

A new institution is configured in six short steps: who they are, their
academic year and term, the modules they use, the grading scale, the first
administrator and, if they want it, demo data.

The answers live in the session until the last step, where they are turned
into real rows. Everything created belongs to the new institution, so a
wizard never touches the rows of another one.
"""

import base64
from datetime import date

from django.core.exceptions import ValidationError
from django.utils.text import slugify

from core.models import Institution
from core.modules import ALL_MODULES
from core.tenant_defaults import DEFAULT_MODULES

SESSION_KEY = "setup_wizard"

STEPS = [
    ("details", "Institution details"),
    ("year", "Academic year and term"),
    ("modules", "Modules"),
    ("grading", "Grading template"),
    ("admin", "First administrator"),
    ("demo", "Demo data"),
]
STEP_NAMES = [name for name, _label in STEPS]

DEFAULT_GRADES = [
    {"letter": "A", "min_mark": "75", "points": "4"},
    {"letter": "B", "min_mark": "65", "points": "3"},
    {"letter": "C", "min_mark": "50", "points": "2"},
    {"letter": "D", "min_mark": "40", "points": "1"},
    {"letter": "F", "min_mark": "0", "points": "0"},
]

SCHOOL_TYPES = ("primary", "secondary")


class StepError(Exception):
    """Raised when a step cannot be saved."""


# ---------- session state ----------
def get_state(request):
    """The answers collected so far."""
    return request.session.get(SESSION_KEY) or {}


def set_state(request, state):
    request.session[SESSION_KEY] = state
    request.session.modified = True
    return state


def clear_state(request):
    request.session.pop(SESSION_KEY, None)
    request.session.modified = True


# ---------- the logo chosen in the details step ----------
def set_logo(state, uploaded):
    """Validate a logo upload and keep a small PNG copy in the session state.

    The file itself cannot live across steps, so a re-encoded 512 px PNG is
    held as base64 until the last step writes the institution.
    """
    from core.logo import preview_png_bytes

    data = preview_png_bytes(uploaded)
    state["logo"] = {
        "data": base64.b64encode(data).decode("ascii"),
        "name": getattr(uploaded, "name", "logo.png")[:120],
    }
    return state


def clear_logo(state):
    state.pop("logo", None)
    return state


def _store_wizard_logo(institution, state):
    """Write the logo chosen in the details step, if there was one."""
    payload = (state.get("logo") or {}).get("data")
    if not payload:
        return
    from core.logo import save_logo

    try:
        save_logo(institution, base64.b64decode(payload))
    except ValidationError:  # pragma: no cover - already validated in the view
        pass


def current_step(request):
    """The step the wizard is on."""
    state = get_state(request)
    step = state.get("step") or STEP_NAMES[0]
    if step not in STEP_NAMES:
        step = STEP_NAMES[0]
    return step


def next_step(step):
    index = STEP_NAMES.index(step)
    return STEP_NAMES[index + 1] if index + 1 < len(STEP_NAMES) else None


def previous_step(step):
    index = STEP_NAMES.index(step)
    return STEP_NAMES[index - 1] if index > 0 else None


def default_state():
    return {
        "step": STEP_NAMES[0],
        "details": {},
        "year": {},
        "modules": [],
        "grading": [dict(row) for row in DEFAULT_GRADES],
        "admin": {},
        "demo": {"enabled": False, "prefix": "demo_"},
    }


# ---------- helpers ----------
def _clean(value):
    return (value or "").strip()


def _parse_date(value, label, errors):
    value = _clean(value)
    if not value:
        errors.append(f"{label} is required.")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors.append(f"{label} must be a date like 2026-01-13.")
        return None


def _parse_number(value, label, errors, minimum=None, maximum=None):
    value = _clean(value)
    if not value:
        errors.append(f"{label} is required.")
        return None
    try:
        number = float(value)
    except ValueError:
        errors.append(f"{label} must be a number.")
        return None
    if minimum is not None and number < minimum:
        errors.append(f"{label} must be at least {minimum}.")
        return None
    if maximum is not None and number > maximum:
        errors.append(f"{label} must be at most {maximum}.")
        return None
    return number


# ---------- step validation ----------
def clean_details(data):
    """Institution name, slug, type, currency and branding."""
    errors = []
    about = _clean(data.get("about"))
    clean = {
        "name": _clean(data.get("name"))[:200],
        "slug": _clean(data.get("slug"))[:100],
        "type": _clean(data.get("type")) or Institution.Type.COLLEGE,
        "tagline": _clean(data.get("tagline"))[:200],
        "about": about[:600],
        "website": _clean(data.get("website"))[:200],
        "currency": _clean(data.get("currency"))[:10] or "$",
        "timezone": _clean(data.get("timezone"))[:64] or "Africa/Harare",
        "language": _clean(data.get("language"))[:10] or "en",
        "address": _clean(data.get("address"))[:2000],
        "contact_email": _clean(data.get("contact_email"))[:200],
        "contact_phone": _clean(data.get("contact_phone"))[:30],
        "primary_color": _clean(data.get("primary_color"))[:20],
        "accent_color": _clean(data.get("accent_color"))[:20],
    }
    if len(about) > 600:
        errors.append("The About text must be 600 characters or fewer.")
    if not clean["name"]:
        errors.append("The institution needs a name.")
    if not clean["slug"]:
        clean["slug"] = slugify(clean["name"])[:100]
    if not clean["slug"]:
        errors.append("The institution needs a web name (slug).")
    elif not slugify(clean["slug"]) == clean["slug"]:
        errors.append("The web name may only contain small letters, numbers and dashes.")
    elif Institution.objects.filter(slug=clean["slug"]).exists():
        errors.append(f"The web name '{clean['slug']}' is already taken.")
    if clean["type"] not in Institution.Type.values:
        errors.append("Choose the kind of institution.")
    if clean["contact_email"] and "@" not in clean["contact_email"]:
        errors.append("The contact email does not look like an email address.")
    return clean, errors


def clean_year(data):
    """The first academic year and the first term."""
    errors = []
    clean = {
        "year_name": _clean(data.get("year_name"))[:20],
        "year_start": _clean(data.get("year_start")),
        "year_end": _clean(data.get("year_end")),
        "term_name": _clean(data.get("term_name"))[:40] or "Term 1",
        "term_start": _clean(data.get("term_start")),
        "term_end": _clean(data.get("term_end")),
    }
    if not clean["year_name"]:
        errors.append("The academic year needs a name, for example 2026.")
    start = _parse_date(clean["year_start"], "The start of the academic year", errors)
    end = _parse_date(clean["year_end"], "The end of the academic year", errors)
    if start and end and end <= start:
        errors.append("The academic year must end after it starts.")
    term_start = _parse_date(clean["term_start"], "The start of the term", errors)
    term_end = _parse_date(clean["term_end"], "The end of the term", errors)
    if term_start and term_end and term_end <= term_start:
        errors.append("The term must end after it starts.")
    if start and term_start and term_start < start:
        errors.append("The term must start inside the academic year.")
    if end and term_end and term_end > end:
        errors.append("The term must end inside the academic year.")
    return clean, errors


def clean_modules(data, institution_type=None):
    """Which modules this institution uses."""
    errors = []
    if isinstance(data, dict):
        chosen = data.get("modules") or []
    else:
        chosen = data or []
    if isinstance(chosen, str):
        chosen = [chosen]
    chosen = [c for c in chosen if c in ALL_MODULES]
    if not chosen:
        chosen = list(DEFAULT_MODULES.get(institution_type or Institution.Type.COLLEGE, []))
        if not chosen:
            errors.append("Switch on at least one module.")
    return {"modules": chosen}, errors


def clean_grading(data):
    """The grade bands: letter, lowest mark and points."""
    errors = []
    if isinstance(data, dict):
        letters = data.get("grading") or []
    else:
        letters = data or []
    clean = []
    seen = set()
    for row in letters:
        letter = _clean(row.get("letter"))[:5].upper()
        if not letter:
            continue
        if letter in seen:
            errors.append(f"The grade '{letter}' is listed twice.")
            continue
        seen.add(letter)
        min_mark = _parse_number(row.get("min_mark"), f"The lowest mark for grade {letter}",
                                 errors, minimum=0, maximum=100)
        points = _parse_number(row.get("points"), f"The points for grade {letter}",
                               errors, minimum=0, maximum=100)
        if min_mark is None or points is None:
            continue
        clean.append({"letter": letter, "min_mark": f"{min_mark:g}", "points": f"{points:g}"})
    if not clean:
        errors.append("Add at least one grade band.")
    return {"grading": clean}, errors


def clean_admin(data):
    """The first administrator account."""
    from accounts.models import User

    errors = []
    clean = {
        "username": _clean(data.get("username"))[:150],
        "first_name": _clean(data.get("first_name"))[:150],
        "last_name": _clean(data.get("last_name"))[:150],
        "email": _clean(data.get("email"))[:200],
        "password": data.get("password") or "",
    }
    if not clean["username"]:
        errors.append("The administrator needs a username.")
    elif not clean["username"].replace(".", "").replace("_", "").replace("-", "").isalnum():
        errors.append("The username may only contain letters, numbers, dots, dashes and underscores.")
    elif User.objects.filter(username=clean["username"]).exists():
        errors.append(f"The username '{clean['username']}' is already taken.")
    if not clean["first_name"]:
        errors.append("The administrator needs a first name.")
    if not clean["last_name"]:
        errors.append("The administrator needs a last name.")
    if clean["email"] and "@" not in clean["email"]:
        errors.append("The email address does not look like an email address.")
    if len(clean["password"]) < 8:
        errors.append("The password must be at least 8 characters long.")
    confirmation = data.get("password_confirm")
    if confirmation and clean["password"] and confirmation != clean["password"]:
        errors.append("The two passwords are not the same.")
    return clean, errors


def clean_demo(data):
    """Whether to fill the institution with demo data."""
    errors = []
    if isinstance(data, dict) and "enabled" in data:
        enabled = bool(data.get("enabled"))
        prefix = _clean(data.get("prefix"))[:20]
    else:
        enabled = data.get("demo_data") in ("on", "true", "1", True)
        prefix = _clean(data.get("prefix"))[:20]
    if not enabled:
        prefix = ""
    return {"enabled": enabled, "prefix": prefix}, errors


# ---------- applying the answers ----------
def build_institution(state):
    """Create the institution and everything the answers asked for."""
    from django.db import transaction

    from academics.models import AcademicYear, GradeBand, SchoolSettings, Term
    from accounts.models import User
    from core.audit import log_action
    from core.roles import ensure_roles
    from core.tenancy import use_institution
    from core.terminology import save_terminology

    details = state["details"]
    year = state["year"]
    admin = state["admin"]
    demo = state.get("demo") or {}

    with transaction.atomic():
        institution = Institution.objects.create(
            name=details["name"],
            slug=details["slug"],
            type=details["type"],
            tagline=details.get("tagline", ""),
            about=details.get("about", ""),
            website=details.get("website", ""),
            currency=details["currency"],
            timezone=details["timezone"],
            language=details["language"],
            address=details["address"],
            contact_email=details["contact_email"],
            contact_phone=details["contact_phone"],
            primary_color=details["primary_color"],
            accent_color=details["accent_color"],
        )

        _store_wizard_logo(institution, state)

        with use_institution(institution):
            # Every module gets a row, so the switches are explicit and the
            # type defaults never creep back in.
            from core.models import InstitutionModule

            chosen = set(state.get("modules") or [])
            for code in ALL_MODULES:
                InstitutionModule.objects.create(
                    institution=institution, code=code, enabled=code in chosen
                )

            save_terminology(institution, {})

            academic_year = AcademicYear.objects.create(
                institution=institution,
                name=year["year_name"],
                start_date=date.fromisoformat(year["year_start"]),
                end_date=date.fromisoformat(year["year_end"]),
                is_current=True,
            )
            term = Term.objects.create(
                institution=institution,
                academic_year=academic_year,
                name=year["term_name"],
                start_date=date.fromisoformat(year["term_start"]),
                end_date=date.fromisoformat(year["term_end"]),
                is_current=True,
            )

            for row in state.get("grading") or []:
                GradeBand.objects.create(
                    institution=institution,
                    letter=row["letter"],
                    min_mark=row["min_mark"],
                    points=row["points"],
                )

            SchoolSettings.get(institution)
            roles = ensure_roles(institution)

            user = User.objects.create_user(
                username=admin["username"],
                password=admin["password"],
                first_name=admin["first_name"],
                last_name=admin["last_name"],
                email=admin["email"],
                role=User.Role.ADMIN,
                is_staff=True,
                institution=institution,
            )
            admin_role = roles.get("institution_admin")
            if admin_role is not None:
                user.primary_role = admin_role
                user.save(update_fields=["primary_role"])

            demo_logins = []
            if demo.get("enabled"):
                from academics.demo import DEMO_PASSWORD, seed_demo

                mode = "school" if details["type"] in SCHOOL_TYPES else "college"
                result = seed_demo(institution, prefix=demo.get("prefix") or "", mode=mode,
                                   set_password=False, academic_year=academic_year, term=term)
                demo_logins = [{"username": name, "password": DEMO_PASSWORD}
                               for name in result.get("users", [])]

            institution.settings = {**(institution.settings or {}),
                                    "demo_data": bool(demo.get("enabled")),
                                    "demo_logins": demo_logins}
            institution.save(update_fields=["settings"])

            log_action(user, "institution.created", institution,
                       after={"slug": institution.slug, "type": institution.type,
                              "demo": bool(demo.get("enabled"))})
    return institution


def validate_state(state):
    """Check the whole answer set again before anything is written."""
    errors = []
    for name, cleaner, extra in (
        ("details", clean_details, ()),
        ("year", clean_year, ()),
        ("modules", clean_modules, (state["details"].get("type"),)),
        ("grading", clean_grading, ()),
        ("admin", clean_admin, ()),
        ("demo", clean_demo, ()),
    ):
        _clean_value, problems = cleaner(state.get(name) or {}, *extra)
        for problem in problems:
            errors.append(f"{name}: {problem}")
    if errors:
        raise ValidationError(errors)
