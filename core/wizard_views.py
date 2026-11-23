"""The setup wizard screens.

A new institution is set up in eight short steps. The answers are kept in the
session until the last step, where they become real rows. Only somebody who
may create institutions can open the wizard.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render

from core import wizard
from core.access import user_has_code
from core.modules import ALL_MODULES, module_label
from core.tenant_defaults import DEFAULT_MODULES

CREATE_INSTITUTIONS = "admin.create_institutions"


def may_create_institutions(user):
    """True when the user may run the setup wizard."""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user_has_code(user, CREATE_INSTITUTIONS)


def _state(request):
    """The answers collected so far, with the empty ones filled in."""
    state = wizard.get_state(request)
    fresh = wizard.default_state()
    for key, value in fresh.items():
        state.setdefault(key, value)
    return state


def _merged_values(state, values, step):
    """What the form should show: the saved answers, with any new ones on top."""
    saved = {"details": state.get("details"), "academic_type": state.get("academic_type"),
             "year": state.get("year"), "admin": state.get("admin")}
    merged = dict(saved.get(step) or {})
    if step == "admin":
        merged.pop("password", None)
        merged.pop("password_confirm", None)
    incoming = (values or {}).get(step)
    if isinstance(incoming, dict):
        for key, value in incoming.items():
            if step == "admin" and key in ("password", "password_confirm"):
                continue
            if value != "" or key not in merged:
                merged[key] = value
    return merged


def _structure_context(state, values=None):
    """Rows and tick boxes for the structure step."""
    from academics.hierarchy import LEVEL_SETS, TERM_SCHEMES, template_for

    details = state.get("details") or {}
    academic_type = state.get("academic_type") or {}
    level_set = academic_type.get("level_set") or ""
    institution_type = details.get("type") or "college"
    template = template_for(institution_type, level_set=level_set or None)
    stored = state.get("structure") or {}
    if values and values.get("structure") is not None:
        stored = values["structure"]
    selected = stored.get("selected") or {}
    renames = stored.get("renames") or {}

    def row(raw, group, extra=None):
        change = (renames.get(group) or {}).get(raw["code"]) or {}
        item = {
            "code": raw["code"],
            "name": change.get("name") or raw["name"],
            "new_code": change.get("code") or raw["code"],
            "description": change.get("description") or raw.get("description", ""),
            "checked": group not in selected or raw["code"] in selected[group],
            "group": group,
        }
        item.update(extra or {})
        return item

    schools = []
    top_departments = []
    for raw in template.get("schools") or []:
        schools.append(row(raw, "schools", {
            "departments": [row(d, "departments") for d in raw.get("departments") or []],
        }))
    top_departments = [row(d, "departments") for d in template.get("departments") or []]
    levels = [row(r, "levels") for r in template.get("levels") or []]
    return {
        "structure_schools": schools,
        "structure_departments": top_departments,
        "structure_levels": levels,
        "structure_streams": template.get("streams") or [],
        "structure_terms": template.get("terms") or [],
        "show_schools": institution_type not in wizard.SCHOOL_TYPES,
        "level_set_choices": [(key, value["label"]) for key, value in LEVEL_SETS.items()],
        "term_scheme_choices": [(key, ", ".join(names))
                                for key, names in TERM_SCHEMES.items()],
        "selected_level_set": level_set,
        "selected_term_scheme": academic_type.get("term_scheme") or "",
        "template_version": template.get("version"),
        "structure_additions": stored.get("additions") or {},
    }


def _step_context(request, state, step, errors=None, values=None):
    """Everything the wizard template needs."""
    from core.models import Institution

    index = wizard.STEP_NAMES.index(step)
    details = state.get("details") or {}
    chosen = list(state.get("modules") or [])
    if values and values.get("modules") is not None:
        chosen = list(values["modules"])
    if not chosen:
        chosen = list(DEFAULT_MODULES.get(details.get("type"), []))

    if step == "grading":
        grading = values.get("grading") if values and values.get("grading") is not None \
            else (state.get("grading") or wizard.DEFAULT_GRADES)
    else:
        grading = state.get("grading") or wizard.DEFAULT_GRADES

    if step == "demo":
        demo = values.get("demo") if values and values.get("demo") is not None \
            else (state.get("demo") or {"enabled": False, "prefix": ""})
    else:
        demo = state.get("demo") or {"enabled": False, "prefix": ""}

    module_rows = [
        {
            "code": code,
            "label": module_label(code),
            "on": code in chosen,
        }
        for code in ALL_MODULES
    ]
    logo = state.get("logo") or {}
    logo_preview = f"data:image/png;base64,{logo['data']}" if logo.get("data") else ""
    return {
        "steps": wizard.STEPS,
        "step": step,
        "step_index": index,
        "step_count": len(wizard.STEPS),
        "step_label": dict(wizard.STEPS)[step],
        "errors": errors or [],
        "form": _merged_values(state, values, step),
        "module_rows": module_rows,
        "institution_types": Institution.Type.choices,
        "grading": grading,
        "demo": demo,
        "details": details,
        "logo_preview": logo_preview,
        **_structure_context(state, values),
    }


def _read_details(request):
    return {
        "name": request.POST.get("name", ""),
        "slug": request.POST.get("slug", ""),
        "type": request.POST.get("type", ""),
        "tagline": request.POST.get("tagline", ""),
        "about": request.POST.get("about", ""),
        "website": request.POST.get("website", ""),
        "currency": request.POST.get("currency", ""),
        "timezone": request.POST.get("timezone", ""),
        "language": request.POST.get("language", ""),
        "address": request.POST.get("address", ""),
        "contact_email": request.POST.get("contact_email", ""),
        "contact_phone": request.POST.get("contact_phone", ""),
        "primary_color": request.POST.get("primary_color", ""),
        "accent_color": request.POST.get("accent_color", ""),
    }


def _read_academic_type(request):
    from core.terminology import FIELDS

    data = {"level_set": request.POST.get("level_set", ""),
            "term_scheme": request.POST.get("term_scheme", "")}
    for field, _label in FIELDS:
        data[field] = request.POST.get(field, "")
    return data


def _read_structure(request):
    from academics.hierarchy import template_for

    details = wizard.get_state(request).get("details") or {}
    academic_type = wizard.get_state(request).get("academic_type") or {}
    template = template_for(details.get("type") or "college",
                            level_set=academic_type.get("level_set") or None)
    groups = {
        "schools": list(template.get("schools") or []),
        "departments": list(template.get("departments") or []),
        "levels": list(template.get("levels") or []),
    }
    for school in template.get("schools") or []:
        groups["departments"].extend(school.get("departments") or [])
    data = {}
    for group, rows in groups.items():
        data[group] = request.POST.getlist(group)
        for raw in rows:
            code = raw["code"]
            data[f"{group}_name_{code}"] = request.POST.get(
                f"{group}_name_{code}", "")
            data[f"{group}_code_{code}"] = request.POST.get(
                f"{group}_code_{code}", "")
            if group != "levels":
                data[f"{group}_description_{code}"] = request.POST.get(
                    f"{group}_description_{code}", "")
    for singular in ("school", "department", "level"):
        for suffix in ("name", "code", "description", "school"):
            data[f"new_{singular}_{suffix}"] = request.POST.get(
                f"new_{singular}_{suffix}", "")
    return data


def _read_year(request):
    return {
        "year_name": request.POST.get("year_name", ""),
        "year_start": request.POST.get("year_start", ""),
        "year_end": request.POST.get("year_end", ""),
        "term_name": request.POST.get("term_name", ""),
        "term_start": request.POST.get("term_start", ""),
        "term_end": request.POST.get("term_end", ""),
    }


def _read_grading(request):
    letters = request.POST.getlist("letter")
    min_marks = request.POST.getlist("min_mark")
    points = request.POST.getlist("points")
    rows = []
    for index, letter in enumerate(letters):
        rows.append({
            "letter": letter,
            "min_mark": min_marks[index] if index < len(min_marks) else "",
            "points": points[index] if index < len(points) else "",
        })
    return rows


def _read_admin(request):
    return {
        "username": request.POST.get("username", ""),
        "first_name": request.POST.get("first_name", ""),
        "last_name": request.POST.get("last_name", ""),
        "email": request.POST.get("email", ""),
        "password": request.POST.get("password", ""),
        "password_confirm": request.POST.get("password_confirm", ""),
    }


def _read_demo(request):
    return {
        "enabled": request.POST.get("enabled") == "on",
        "prefix": request.POST.get("prefix", ""),
    }


@login_required
def setup_wizard(request):
    """Walk through the eight steps and create the institution at the end."""
    if not may_create_institutions(request.user):
        raise PermissionDenied("Only a platform administrator may create institutions.")

    state = _state(request)
    step = wizard.current_step(request)

    if request.method == "POST":
        action = request.POST.get("action", "next")

        if action == "back":
            previous = wizard.previous_step(step)
            state["step"] = previous or step
            wizard.set_state(request, state)
            return redirect("setup_wizard")

        values = {
            "details": _read_details(request),
            "academic_type": _read_academic_type(request),
            "structure": _read_structure(request),
            "year": _read_year(request),
            "modules": request.POST.getlist("modules"),
            "grading": _read_grading(request),
            "admin": _read_admin(request),
            "demo": _read_demo(request),
        }

        if step == "details":
            clean, errors = wizard.clean_details(values["details"])
            upload = request.FILES.get("logo")
            if upload is not None:
                try:
                    wizard.set_logo(state, upload)
                except ValidationError as exc:
                    errors.extend(exc.messages)
            elif request.POST.get("remove_logo"):
                wizard.clear_logo(state)
            # Keep the logo (a re-encoded PNG in the session) even when another
            # field still needs fixing, because a file input cannot be refilled.
            wizard.set_state(request, state)
        elif step == "academic_type":
            clean, errors = wizard.clean_academic_type(
                values["academic_type"], (state.get("details") or {}).get("type"))
        elif step == "structure":
            clean, errors = wizard.clean_structure(
                values["structure"], (state.get("details") or {}).get("type"),
                (state.get("academic_type") or {}).get("level_set"))
        elif step == "year":
            clean, errors = wizard.clean_year(values["year"])
        elif step == "modules":
            clean, errors = wizard.clean_modules(
                values["modules"], (state.get("details") or {}).get("type"))
        elif step == "grading":
            clean, errors = wizard.clean_grading(values["grading"])
        elif step == "admin":
            clean, errors = wizard.clean_admin(values["admin"])
        else:
            clean, errors = wizard.clean_demo(values["demo"])

        if errors:
            context = _step_context(request, state, step, errors=errors, values=values)
            return render(request, "core/setup_wizard.html", context)

        state[step] = clean
        if step == "modules":
            state["modules"] = clean["modules"]
        if step == "grading":
            state["grading"] = clean["grading"]

        if step == "demo":
            # The last step creates the institution.
            try:
                wizard.validate_state(state)
            except ValidationError as exc:
                problems = exc.messages if hasattr(exc, "messages") else [str(exc)]
                context = _step_context(request, state, step, errors=problems,
                                        values=values)
                return render(request, "core/setup_wizard.html", context)
            institution = wizard.build_institution(state)
            wizard.clear_state(request)
            request.session["setup_created"] = institution.pk
            request.session.modified = True
            messages.success(request, f"{institution.name} is ready.")
            return redirect("setup_done")

        following = wizard.next_step(step)
        state["step"] = following or step
        wizard.set_state(request, state)
        return redirect("setup_wizard")

    context = _step_context(request, state, step)
    return render(request, "core/setup_wizard.html", context)


@login_required
def setup_done(request):
    """What was built, and how to sign in."""
    if not may_create_institutions(request.user):
        raise PermissionDenied("Only a platform administrator may create institutions.")
    from core.models import Institution

    pk = request.session.get("setup_created")
    institution = Institution.objects.filter(pk=pk).first() if pk else None
    if institution is None:
        return redirect("setup_wizard")
    settings_row = institution.settings or {}
    context = {
        "inst": institution,
        "demo_enabled": bool(settings_row.get("demo_data")),
        "logins": settings_row.get("demo_logins") or [],
    }
    return render(request, "core/setup_done.html", context)
