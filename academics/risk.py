"""At-risk scoring shared by the dashboard and the nightly command.

A student is at risk when any of three plain rules fails: fees are still owed,
attendance is below 75%, or the average mark is below 50. The score is the
number of rules that failed, so the dashboard can put the most urgent students
at the top without inventing a weighting.
"""

from decimal import Decimal

from django.db.models import Avg, Count, Q
from django.urls import reverse
from django.utils import timezone

from .models import (
    AcademicYear,
    AtRiskScore,
    Attendance,
    ClassTeacher,
    Enrollment,
    Student,
    Term,
    balances_by_student,
)

ATTENDANCE_FLOOR = 75
MARK_FLOOR = 50
MIN_ATTENDANCE_SESSIONS = 3
RISK_STATUSES = (Student.Status.ACTIVE, Student.Status.ENROLLED)


def _students(students=None):
    if students is not None:
        return students
    return Student.objects.filter(status__in=RISK_STATUSES).select_related(
        "user", "school_class", "stream", "programme")


def _attendance_totals(term=None):
    rows = Attendance.objects.all()
    if term is not None:
        rows = rows.filter(enrollment__term=term)
    return {
        row["enrollment__student_id"]: row
        for row in rows.values("enrollment__student_id").annotate(
            total=Count("id"), present=Count("id", filter=Q(present=True)))
    }


def _mark_averages():
    return {
        row["student_id"]: row["average"]
        for row in Enrollment.objects.filter(mark__isnull=False)
        .values("student_id").annotate(average=Avg("mark"))
    }


def compute_at_risk(students=None, as_of=None):
    """Return the at-risk rows, most urgent first.

    ``as_of`` is accepted so callers can label a run without changing the
    rules; the rules themselves always read the current records.
    """
    students = list(_students(students))
    billed, paid, waived = balances_by_student()
    term = Term.objects.filter(is_current=True).first()
    attendance = _attendance_totals(term)
    averages = _mark_averages()
    rows = []
    for student in students:
        reasons = []
        owed = (
            billed.get(student.pk, Decimal("0"))
            - paid.get(student.pk, Decimal("0"))
            - waived.get(student.pk, Decimal("0"))
        )
        if owed > 0:
            reasons.append(f"Fees outstanding: {owed:.2f}")
        seen = attendance.get(student.pk)
        if seen and seen["total"] >= MIN_ATTENDANCE_SESSIONS:
            pct = seen["present"] * 100 / seen["total"]
            if pct < ATTENDANCE_FLOOR:
                reasons.append(f"Attendance {pct:.0f}%")
        average = averages.get(student.pk)
        if average is not None and average < MARK_FLOOR:
            reasons.append(f"Average mark {average:.0f}")
        if reasons:
            rows.append({
                "student": student,
                "reasons": reasons,
                "score": len(reasons),
                "risk": len(reasons),
            })
    rows.sort(key=lambda row: row["score"], reverse=True)
    return rows


def persist_scores(rows, as_of=None):
    """Store one score per student for the run date."""
    as_of = as_of or timezone.localdate()
    saved = []
    for row in rows:
        score, _created = AtRiskScore.objects.update_or_create(
            student=row["student"],
            computed_on=as_of,
            defaults={"score": row["score"], "reasons": row["reasons"]},
        )
        saved.append(score)
    return saved


def notify_class_teachers(rows, as_of=None):
    """Tell each class teacher about their at-risk students, once a day."""
    from core.models import Notification
    from core.notifications import notify

    as_of = as_of or timezone.localdate()
    academic_year = AcademicYear.objects.filter(is_current=True).first()
    sent = 0
    for row in rows:
        student = row["student"]
        if not student.school_class_id:
            continue
        teachers = ClassTeacher.objects.filter(
            school_class_id=student.school_class_id).select_related("teacher")
        if academic_year is not None:
            teachers = teachers.filter(academic_year=academic_year)
        link = reverse("student_detail", args=[student.pk])
        for class_teacher in teachers:
            teacher = class_teacher.teacher
            if teacher is None:
                continue
            already = Notification.objects.filter(
                user=teacher, category="at_risk", link=link,
                created__date=as_of).exists()
            if already:
                continue
            notify(
                teacher,
                f"{student.name} is at risk",
                "; ".join(row["reasons"]),
                link=link,
                category="at_risk",
            )
            sent += 1
    return sent
