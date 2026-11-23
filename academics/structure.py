"""Academic structure settings: schools, levels, classes and templates.

These screens change the shape of the institution, so every write is checked
against ``academics.manage_structure`` and written to the audit log. Rows are
deactivated rather than deleted when they have history.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required
from core.access import user_has_code
from core.audit import log_action, log_permission_denied
from core.models import Department
from core.terminology import terminology_for
from platform_admin.models import AcademicTemplate

from .forms import AcademicSchoolForm, GradeLevelForm, SchoolClassForm
from .hierarchy import STREAM_SUGGESTIONS, apply_template
from .models import (
    AcademicSchool,
    GradeLevel,
    Programme,
    SchoolClass,
    Stream,
    Student,
)

CODE = "academics.manage_structure"


def _can_manage(user):
    return user.is_superuser or user_has_code(user, CODE)


def _require_manage(request):
    if _can_manage(request.user):
        return True
    log_permission_denied(request, [CODE])
    return False


def _deny():
    raise PermissionDenied(
        "Access problem: you do not have permission (PERM-DENIED). "
        "Ask your school administrator for the Registrar or Institution admin role."
    )


def _audit(request, action, instance=None, before=None, after=None, **extra):
    return log_action(
        request.user,
        action,
        instance,
        before=before,
        after=after,
        request=request,
        **extra,
    )


def _institution(request):
    return getattr(request, "institution", None)


def _labels(request):
    return terminology_for(_institution(request))


def _snapshot(row, fields):
    return {field: getattr(row, field, None) for field in fields}


def _level_rows():
    return list(GradeLevel.objects.all().order_by("order", "name"))


# ---------------------------------------------------------------------------
# Templates and top-level schools/faculties
# ---------------------------------------------------------------------------

@role_required("admin", "registrar")
def setup_home(request):
    if request.method == "POST":
        if not _require_manage(request):
            return HttpResponseForbidden("You do not have permission to manage the academic structure")
        action = request.POST.get("action", "")
        institution = _institution(request)
        template = get_object_or_404(AcademicTemplate, pk=request.POST.get("template"))
        if not template.is_active:
            messages.error(request, "That template version is no longer published.")
        elif action == "apply":
            result = apply_template(
                institution,
                template.code,
                actor=request.user,
                request=request,
            )
            messages.success(
                request,
                f"Applied {template.name} {template.version}: "
                f"{result['schools']} schools/faculties, {result['departments']} departments, "
                f"{result['levels']} grade levels and {result['terms']} terms were added. "
                "Existing rows were not changed.",
            )
        elif action == "preview":
            messages.info(
                request,
                f"{template.name} {template.version}: {template.description or 'No description'} "
                f"({template.level_count()} levels in the academic sequence).",
            )
        else:
            messages.error(request, "Unknown template action.")
        return redirect("academic_structure")
    institution = _institution(request)
    active = AcademicTemplate.objects.filter(is_active=True).select_related("updated_by")
    applied = (institution.settings or {}).get("academic_template") if institution else None
    return render(
        request,
        "academics/academic_structure.html",
        {
            "templates": active,
            "applied": applied,
            "schools": AcademicSchool.objects.select_related("parent", "head"),
            "departments": Department.objects.select_related("school"),
            "can_manage": _can_manage(request.user),
            "labels": _labels(request),
        },
    )


@role_required("admin", "registrar")
def school_list(request):
    return render(
        request,
        "academics/school_list.html",
        {
            "schools": AcademicSchool.objects.select_related("parent", "head").order_by(
                "sort_order", "name"
            ),
            "departments": Department.objects.select_related("school").order_by("name"),
            "can_manage": _can_manage(request.user),
            "labels": _labels(request),
        },
    )


@role_required("admin", "registrar")
def school_form(request, pk=None):
    school = get_object_or_404(AcademicSchool, pk=pk) if pk else None
    if request.method == "POST":
        if not _require_manage(request):
            return HttpResponseForbidden("You do not have permission to manage the academic structure")
        before = _snapshot(school, ["name", "code", "description", "school_id", "is_active",
                                    "sort_order", "parent_id"]) if school else None
        form = AcademicSchoolForm(request.POST, instance=school)
        if form.is_valid():
            school = form.save()
            _audit(request, "school.create" if not pk else "school.update", school,
                   before=before, after=_snapshot(school, [
                       "name", "code", "description", "school_id", "is_active",
                       "sort_order", "parent_id"]))
            messages.success(request, f"Saved {school.name}.")
            return redirect("school_list")
    else:
        form = AcademicSchoolForm(instance=school)
    return render(
        request,
        "academics/school_form.html",
        {"form": form, "school": school, "can_manage": _can_manage(request.user),
         "labels": _labels(request)},
    )


@role_required("admin", "registrar")
def school_delete(request, pk):
    if request.method != "POST":
        return HttpResponseForbidden("Use the form on the school page.")
    if not _require_manage(request):
        return HttpResponseForbidden("You do not have permission to manage the academic structure")
    school = get_object_or_404(AcademicSchool, pk=pk)
    blockers = []
    if Department.objects.filter(school=school).exists():
        blockers.append("departments")
    if Programme.objects.filter(school=school).exists():
        blockers.append("programmes")
    if SchoolClass.objects.filter(school=school).exists():
        blockers.append("classes")
    if Student.objects.filter(school=school).exists():
        blockers.append("students")
    if AcademicSchool.objects.filter(parent=school).exists():
        blockers.append("child schools")
    if blockers:
        messages.error(request, f"Cannot delete {school.name}: it still has {', '.join(blockers)}.")
    else:
        name = school.name
        before = _snapshot(school, ["name", "code"])
        school.delete()
        _audit(request, "school.delete", None, before=before, after={"name": name})
        messages.success(request, f"Deleted {name}.")
    return redirect("school_list")


# ---------------------------------------------------------------------------
# Levels, forms/classes and streams
# ---------------------------------------------------------------------------

@role_required("admin", "registrar")
def grades_and_classes(request):
    """The dedicated academic structure page for levels and classes."""
    if request.method == "POST":
        if not _require_manage(request):
            return HttpResponseForbidden("You do not have permission to manage the academic structure")
        action = request.POST.get("action", "")
        level = None
        school_class = None
        if request.POST.get("level_pk"):
            level = GradeLevel.objects.filter(pk=request.POST["level_pk"]).first()
        if request.POST.get("class_pk"):
            school_class = SchoolClass.objects.filter(pk=request.POST["class_pk"]).first()

        if action == "add_level":
            form = GradeLevelForm(request.POST)
            if form.is_valid():
                row = form.save()
                _audit(request, "level.create", row,
                       after=_snapshot(row, ["name", "code", "kind", "order", "is_active"]))
                messages.success(request, f"Added {row.name}.")
            else:
                messages.error(request, f"Could not add the level. {_err(form)}")
        elif action == "rename_level" and level:
            before = _snapshot(level, ["name", "code"])
            level.name = request.POST.get("name", level.name).strip()[:60]
            level.code = request.POST.get("code", level.code).strip()[:20]
            if not level.name:
                messages.error(request, "A level needs a name.")
            else:
                level.save(update_fields=["name", "code"])
                _audit(request, "level.rename", level, before=before,
                       after=_snapshot(level, ["name", "code"]))
                messages.success(request, f"Renamed to {level.name}.")
        elif action == "move_level" and level:
            direction = request.POST.get("direction")
            rows = _level_rows()
            index = next((i for i, row in enumerate(rows) if row.pk == level.pk), None)
            other = None
            if index is not None:
                if direction == "up" and index > 0:
                    other = rows[index - 1]
                elif direction == "down" and index < len(rows) - 1:
                    other = rows[index + 1]
            if other is None:
                messages.info(request, f"{level.name} is already at that end.")
            else:
                before_a, before_b = level.order, other.order
                with transaction.atomic():
                    GradeLevel.all_objects.filter(pk=level.pk).update(order=before_b)
                    GradeLevel.all_objects.filter(pk=other.pk).update(order=before_a)
                _audit(request, "level.reorder", level,
                       before={"order": before_a, "swapped_with": other.name},
                       after={"order": before_b, "swapped_with": other.name})
                messages.success(request, f"Moved {level.name}.")
        elif action in ("deactivate_level", "activate_level") and level:
            before = level.is_active
            level.is_active = action == "activate_level"
            level.save(update_fields=["is_active"])
            _audit(request, "level.deactivate" if not level.is_active else "level.activate",
                   level, before={"is_active": before}, after={"is_active": level.is_active})
            messages.success(request, f"{level.name} is now "
                             f"{'active' if level.is_active else 'inactive'}.")
        elif action == "set_promotion" and level:
            target = None
            if request.POST.get("promotion_to"):
                target = GradeLevel.objects.filter(pk=request.POST["promotion_to"]).first()
            if target and target.pk == level.pk:
                messages.error(request, "A level cannot promote to itself.")
            else:
                before = _snapshot(level, ["promotion_to_id"])
                level.promotion_to = target
                level.save(update_fields=["promotion_to"])
                _audit(request, "level.promotion", level, before=before,
                       after=_snapshot(level, ["promotion_to_id"]))
                messages.success(request, f"Promotion target for {level.name} saved.")
        elif action == "delete_level" and level:
            blockers = []
            if SchoolClass.objects.filter(grade_level=level).exists():
                blockers.append("classes")
            if GradeLevel.objects.filter(promotion_to=level).exists():
                blockers.append("promotion rules")
            if blockers:
                messages.error(
                    request,
                    f"Cannot delete {level.name}: it still has {', '.join(blockers)}. "
                    "Deactivate it instead.",
                )
            else:
                before = _snapshot(level, ["name", "code", "order"])
                name = level.name
                level.delete()
                _audit(request, "level.delete", None, before=before, after={"name": name})
                messages.success(request, f"Deleted {name}.")
        elif action == "add_class":
            form = SchoolClassForm(request.POST)
            if form.is_valid():
                row = form.save()
                _audit(request, "class.create", row,
                       after=_snapshot(row, ["name", "grade_level_id", "school_id",
                                             "department_id", "capacity", "room"]))
                messages.success(request, f"Added {row.name}.")
            else:
                messages.error(request, f"Could not add the class. {_err(form)}")
        elif action == "update_class" and school_class:
            form = SchoolClassForm(request.POST, instance=school_class)
            if form.is_valid():
                before = _snapshot(school_class, ["name", "grade_level_id", "school_id",
                                                  "department_id", "capacity", "room",
                                                  "is_active"])
                row = form.save()
                _audit(request, "class.update", row, before=before,
                       after=_snapshot(row, ["name", "grade_level_id", "school_id",
                                             "department_id", "capacity", "room", "is_active"]))
                messages.success(request, f"Saved {row.name}.")
            else:
                messages.error(request, f"Could not save the class. {_err(form)}")
        elif action in ("deactivate_class", "activate_class") and school_class:
            before = school_class.is_active
            school_class.is_active = action == "activate_class"
            school_class.save(update_fields=["is_active"])
            _audit(request, "class.deactivate" if not school_class.is_active else "class.activate",
                   school_class, before={"is_active": before},
                   after={"is_active": school_class.is_active})
            messages.success(request, f"{school_class.name} is now "
                             f"{'active' if school_class.is_active else 'inactive'}.")
        elif action == "bulk_streams":
            names = []
            for value in request.POST.getlist("stream_names"):
                names += [part.strip() for part in value.split(",") if part.strip()]
            custom = request.POST.get("new_streams", "")
            names += [part.strip() for part in custom.replace("\n", ",").split(",")
                      if part.strip()]
            names = list(dict.fromkeys(names))
            classes = SchoolClass.objects.filter(
                pk__in=request.POST.getlist("class_ids"))
            created = 0
            if not names:
                messages.error(request, "Choose at least one stream name.")
            elif not classes.exists():
                messages.error(request, "Choose at least one class.")
            else:
                with transaction.atomic():
                    for school_class in classes:
                        for name in names:
                            _, was_created = Stream.objects.get_or_create(
                                school_class=school_class, name=name[:30])
                            created += int(was_created)
                _audit(request, "stream.bulk_create", None,
                       after={"classes": [c.name for c in classes], "streams": names,
                              "created": created})
                messages.success(request, f"Added {created} streams.")
        elif action == "delete_class" and school_class:
            blockers = []
            if school_class.students.exists():
                blockers.append("students")
            if school_class.lessons.exists():
                blockers.append("timetable entries")
            if school_class.allocations.exists():
                blockers.append("subject allocations")
            if school_class.class_teachers.exists():
                blockers.append("class teachers")
            if blockers:
                messages.error(
                    request,
                    f"Cannot delete {school_class.name}: it still has {', '.join(blockers)}. "
                    "Deactivate it instead.",
                )
            else:
                before = _snapshot(school_class, ["name", "grade_level_id"])
                name = school_class.name
                school_class.delete()
                _audit(request, "class.delete", None, before=before, after={"name": name})
                messages.success(request, f"Deleted {name}.")
        else:
            messages.error(request, "Unknown action.")
        return redirect("grades_classes")

    labels = _labels(request)
    levels = GradeLevel.objects.prefetch_related("classes").order_by("order", "name")
    classes = SchoolClass.objects.select_related(
        "grade_level", "school", "department").order_by("name")
    return render(
        request,
        "academics/grades_and_classes.html",
        {
            "levels": levels,
            "classes": classes,
            "level_form": GradeLevelForm(),
            "class_form": SchoolClassForm(),
            "class_forms": [(row, SchoolClassForm(instance=row)) for row in classes],
            "stream_suggestions": STREAM_SUGGESTIONS,
            "labels": labels,
            "can_manage": _can_manage(request.user),
        },
    )


@role_required("admin", "registrar")
def grade_levels(request):
    """The original levels screen, kept working for existing links."""
    form = GradeLevelForm(request.POST or None)
    if request.method == "POST":
        if not _require_manage(request):
            return HttpResponseForbidden("You do not have permission to manage the academic structure")
        action = request.POST.get("action")
        if action == "new_level":
            if form.is_valid():
                row = form.save()
                _audit(request, "level.create", row,
                       after=_snapshot(row, ["name", "code", "kind", "order"]))
                messages.success(request, f"Level {row.name} added.")
                return redirect("grade_levels")
            messages.error(request, f"Could not add the level. {_err(form)}")
        elif action == "link":
            school_class = SchoolClass.objects.filter(pk=request.POST.get("class")).first()
            level = GradeLevel.objects.filter(pk=request.POST.get("level")).first()
            if school_class is None or level is None:
                messages.error(request, "Choose a class and a level.")
            else:
                before = _snapshot(school_class, ["grade_level_id"])
                school_class.grade_level = level
                school_class.save(update_fields=["grade_level"])
                _audit(request, "class.level_link", school_class, before=before,
                       after={"grade_level_id": level.pk})
                messages.success(request, f"{school_class.name} now sits on {level.name}.")
            return redirect("grade_levels")
        elif action == "unlink":
            school_class = SchoolClass.objects.filter(pk=request.POST.get("class")).first()
            if school_class is not None:
                before = _snapshot(school_class, ["grade_level_id"])
                school_class.grade_level = None
                school_class.save(update_fields=["grade_level"])
                _audit(request, "class.level_unlink", school_class, before=before,
                       after={"grade_level_id": None})
                messages.success(request, f"{school_class.name} no longer sits on a level.")
            return redirect("grade_levels")
    levels = GradeLevel.objects.prefetch_related("classes").order_by("order", "name")
    classes = SchoolClass.objects.select_related("grade_level").order_by("name")
    return render(request, "academics/grade_levels.html", {
        "levels": levels, "classes": classes, "form": form, "labels": _labels(request)})


def _err(form):
    return " ".join(message for messages in form.errors.values() for message in messages)
