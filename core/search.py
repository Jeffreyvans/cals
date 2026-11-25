"""Permission-filtered record search shared by global search and AI search.

The AI assistant never writes a query. It may only choose from the fixed record
type codes below; this module decides which records a person may see and builds
the query with the same local rules the ordinary search screen uses.
"""

from __future__ import annotations

from django.urls import reverse

from core.access import user_has_code
from core.modules import is_enabled

STUDENTS = "students"
STAFF = "staff"
COURSES = "courses"
RECEIPTS = "receipts"
APPLICATIONS = "applications"

SEARCH_TYPES = (
    (STUDENTS, "Students by name or student number"),
    (STAFF, "Staff accounts by name or employee number"),
    (COURSES, "Courses by code or title"),
    (RECEIPTS, "Receipts by number or reference"),
    (APPLICATIONS, "Applications by reference or applicant name"),
)
SEARCH_CODES = tuple(code for code, _label in SEARCH_TYPES)


def number_pks(queryset, field, query, limit=20):
    """Primary keys whose number matches the query ignoring case/separators."""
    from core.numbering import matching_pks

    return matching_pks(queryset, field, query, limit=limit)


def search_groups(user, query, institution=None, codes=None):
    """Return permission-filtered groups for ``query``.

    ``codes`` is an optional allow-list chosen locally by a caller. Unknown
    codes are ignored; nothing outside this function's own queries can reach
    the result set.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return []
    allowed = set(codes if codes is not None else SEARCH_CODES)
    groups = []

    from django.db.models import Q

    from academics.models import Course, Student
    from accounts.models import User
    from admissions.models import Application
    from finance.models import Payment

    if STUDENTS in allowed and user_has_code(user, "students.view_all"):
        number_pks_found = number_pks(Student.objects.all(), "student_no", query)
        rows = (Student.objects.select_related(
                    "user", "programme", "school_class")
                .filter(Q(student_no__icontains=query)
                        | Q(pk__in=number_pks_found)
                        | Q(user__first_name__icontains=query)
                        | Q(user__last_name__icontains=query)
                        | Q(user__username__icontains=query))[:20])
        groups.append({"code": STUDENTS, "title": "Students", "items": [{
            "label": s.name,
            "meta": f"{s.student_no} - {s.programme.name}",
            "url": reverse("student_detail", args=[s.pk]),
        } for s in rows]})

    if (STAFF in allowed and (
            user_has_code(user, "admin.manage_users")
            or user_has_code(user, "hr.manage"))):
        staff_numbers = number_pks(
            User.tenant_objects.exclude(staff_profile__isnull=True),
            "staff_profile__employee_no", query)
        rows = (User.tenant_objects.select_related(
                    "primary_role", "staff_profile")
                .filter(Q(first_name__icontains=query)
                        | Q(last_name__icontains=query)
                        | Q(username__icontains=query)
                        | Q(email__icontains=query)
                        | Q(staff_profile__employee_no__icontains=query)
                        | Q(pk__in=staff_numbers))[:20])
        groups.append({"code": STAFF, "title": "Staff accounts", "items": [{
            "label": u.get_full_name() or u.username,
            "meta": " · ".join(filter(None, [
                getattr(getattr(u, "staff_profile", None), "employee_no", ""),
                u.username, u.get_role_display()])),
            "url": reverse("staff_detail", args=[u.pk]),
        } for u in rows]})

    if COURSES in allowed and user_has_code(user, "academics.view_reports"):
        rows = Course.objects.select_related("programme").filter(
            Q(code__icontains=query) | Q(title__icontains=query))[:20]
        groups.append({"code": COURSES, "title": "Courses", "items": [{
            "label": f"{c.code} - {c.title}",
            "meta": c.programme.name,
            "url": reverse("course_roster", args=[c.pk]),
        } for c in rows]})

    if RECEIPTS in allowed and user_has_code(user, "finance.view_reports"):
        receipt_pks = number_pks(Payment.objects.all(), "receipt_no", query)
        rows = (Payment.objects.select_related("invoice__student__user")
                .filter(Q(receipt_no__icontains=query)
                        | Q(pk__in=receipt_pks)
                        | Q(reference__icontains=query)
                        | Q(payer_name__icontains=query)
                        | Q(invoice__student__user__first_name__icontains=query)
                        | Q(invoice__student__user__last_name__icontains=query))[:20])
        groups.append({"code": RECEIPTS, "title": "Receipts", "items": [{
            "label": f"{p.receipt_no} - {p.invoice.student.name}",
            "meta": f"{p.get_method_display()} - {p.amount}",
            "url": reverse("receipt", args=[p.pk]),
        } for p in rows]})

    if (APPLICATIONS in allowed
            and is_enabled("admissions", institution)
            and (user_has_code(user, "admissions.manage")
                 or user_has_code(user, "admissions.review"))):
        reference_pks = number_pks(
            Application.objects.all(), "reference", query)
        rows = Application.objects.filter(
            Q(reference__icontains=query)
            | Q(pk__in=reference_pks)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query))[:20]
        groups.append({"code": APPLICATIONS, "title": "Applications", "items": [{
            "label": f"{a.reference} - {a.name}",
            "meta": a.get_status_display(),
            "url": reverse("admissions:application_detail", args=[a.pk]),
        } for a in rows]})

    return [group for group in groups if group["items"]]
