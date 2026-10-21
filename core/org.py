"""Departments and staff profiles.

A department groups staff and courses. A staff profile is the extra
information about a user account that the HR file needs: employee number,
job title, phone and the departments the person belongs to.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.audit import log_action
from core.models import Department, Institution, StaffProfile


def _institution(request):
    inst = getattr(request, "institution", None)
    if inst is None:
        inst = Institution.objects.filter(slug="default").first()
    return inst


def _require(request, code):
    from core.access import user_has_code

    if not user_has_code(request.user, code):
        raise PermissionDenied("Your role does not allow this page.")


def _staff_users(institution):
    """The user accounts of this institution that may be staff."""
    from accounts.models import User

    return (
        User.tenant_objects.filter(institution=institution, is_active=True)
        .exclude(role=User.Role.STUDENT)
        .exclude(role=User.Role.PARENT)
        .select_related("primary_role")
        .order_by("last_name", "first_name")
    )


@login_required
def department_list(request):
    """The departments of this institution."""
    _require(request, "admin.manage_departments")
    inst = _institution(request)
    rows = Department.objects.filter(institution=inst).select_related("head", "parent")
    for row in rows:
        row.staff_count = row.staff.count()
    return render(request, "core/department_list.html", {
        "inst": inst,
        "rows": rows,
        "heads": _staff_users(inst),
    })


@login_required
def department_create(request):
    """Add a department."""
    _require(request, "admin.manage_departments")
    inst = _institution(request)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:120]
        code = request.POST.get("code", "").strip()[:20]
        if not name or not code:
            messages.error(request, "A department needs a name and a code.")
        elif Department.objects.filter(institution=inst, code=code).exists():
            messages.error(request, f"The code '{code}' is already used.")
        else:
            dept = Department.objects.create(
                institution=inst,
                name=name,
                code=code,
                parent_id=request.POST.get("parent") or None,
                head_id=request.POST.get("head") or None,
            )
            log_action(request.user, "department.created", dept, request=request)
            messages.success(request, f"Department {dept.name} added.")
            return redirect("department_list")
    return render(request, "core/department_form.html", {
        "inst": inst,
        "departments": Department.objects.filter(institution=inst),
        "heads": _staff_users(inst),
    })


@login_required
def department_detail(request, pk):
    """Rename a department or change who leads it."""
    _require(request, "admin.manage_departments")
    inst = _institution(request)
    dept = get_object_or_404(Department, pk=pk, institution=inst)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:120]
        code = request.POST.get("code", "").strip()[:20]
        clash = Department.objects.filter(institution=inst, code=code).exclude(pk=dept.pk)
        if not name or not code:
            messages.error(request, "A department needs a name and a code.")
        elif clash.exists():
            messages.error(request, f"The code '{code}' is already used.")
        else:
            before = {"name": dept.name, "code": dept.code}
            dept.name = name
            dept.code = code
            dept.parent_id = request.POST.get("parent") or None
            dept.head_id = request.POST.get("head") or None
            dept.save()
            log_action(request.user, "department.updated", dept, before=before, request=request)
            messages.success(request, "Department saved.")
            return redirect("department_list")
    return render(request, "core/department_form.html", {
        "inst": inst,
        "dept": dept,
        "departments": Department.objects.filter(institution=inst).exclude(pk=dept.pk),
        "heads": _staff_users(inst),
    })


@login_required
def department_delete(request, pk):
    """Remove a department that nothing depends on."""
    _require(request, "admin.manage_departments")
    inst = _institution(request)
    dept = get_object_or_404(Department, pk=pk, institution=inst)
    if dept.child_departments.exists():
        messages.error(request, "Move the sub-departments first.")
        return redirect("department_list")
    if request.method == "POST":
        log_action(request.user, "department.deleted", dept, request=request)
        dept.delete()
        messages.success(request, "Department removed.")
        return redirect("department_list")
    return render(request, "core/confirm_delete.html", {
        "inst": inst,
        "title": f"Remove the department {dept.name}?",
        "detail": "The staff profiles in it keep their accounts but lose this link.",
        "cancel": reverse("department_list"),
    })


@login_required
def staff_list(request):
    """The staff of this institution."""
    _require(request, "admin.manage_departments")
    inst = _institution(request)
    query = request.GET.get("q", "").strip()
    rows = StaffProfile.objects.filter(institution=inst).select_related("user")
    if query:
        rows = rows.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(employee_no__icontains=query)
            | Q(job_title__icontains=query)
        )
    for row in rows:
        row.department_list = ", ".join(row.departments.values_list("name", flat=True))
    without = _staff_users(inst).exclude(
        pk__in=StaffProfile.objects.filter(institution=inst).values_list("user_id", flat=True)
    )
    return render(request, "core/staff_list.html", {
        "inst": inst,
        "rows": rows,
        "query": query,
        "without_profile": without,
    })


@login_required
def staff_detail(request, pk):
    """The staff profile of one user account."""
    _require(request, "admin.manage_departments")
    from accounts.models import User

    inst = _institution(request)
    user = get_object_or_404(User, pk=pk, institution=inst)
    profile, _created = StaffProfile.objects.get_or_create(
        institution=inst, user=user, defaults={"job_title": user.get_role_display()}
    )
    if request.method == "POST":
        profile.employee_no = request.POST.get("employee_no", "").strip()[:30]
        profile.job_title = request.POST.get("job_title", "").strip()[:120]
        profile.phone = request.POST.get("phone", "").strip()[:30]
        profile.date_joined = request.POST.get("date_joined") or None
        profile.save()
        profile.departments.set(
            Department.objects.filter(
                institution=inst, pk__in=request.POST.getlist("departments")
            )
        )
        log_action(request.user, "staff.updated", profile, request=request)
        messages.success(request, "Staff profile saved.")
        return redirect("staff_list")
    profile.department_ids = list(profile.departments.values_list("pk", flat=True))
    return render(request, "core/staff_form.html", {
        "inst": inst,
        "profile": profile,
        "user_obj": user,
        "departments": Department.objects.filter(institution=inst),
    })
