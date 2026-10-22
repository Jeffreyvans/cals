"""The academic structure: years, terms, levels and moving students up.

Everything that changes the shape of the school lives here, so the views stay
thin and the rules can be tested on their own. A closed year or term cannot be
changed any more, and a student is only promoted when there is a class waiting
for them.
"""

from django.utils import timezone

from core.audit import log_action

# ---------- years and terms ----------

def close_term(term, user=None, request=None):
    """Lock a term so that marks, attendance and registration stop changing."""
    if term.is_closed:
        return term
    term.is_closed = True
    term.closed_on = timezone.localdate()
    term.save(update_fields=["is_closed", "closed_on"])
    log_action(user, "academics.term_closed", term, request=request)
    return term


def reopen_term(term, user=None, request=None):
    """Open a closed term again."""
    if not term.is_closed:
        return term
    term.is_closed = False
    term.closed_on = None
    term.save(update_fields=["is_closed", "closed_on"])
    log_action(user, "academics.term_reopened", term, request=request)
    return term


def close_year(year, user=None, request=None):
    """Lock a year and every term in it."""
    for term in year.terms.all():
        close_term(term, user=user, request=request)
    if year.is_closed:
        return year
    year.is_closed = True
    year.closed_on = timezone.localdate()
    year.save(update_fields=["is_closed", "closed_on"])
    log_action(user, "academics.year_closed", year, request=request)
    return year


def reopen_year(year, user=None, request=None):
    """Open a closed year and its terms again."""
    for term in year.terms.all():
        reopen_term(term, user=user, request=request)
    if not year.is_closed:
        return year
    year.is_closed = False
    year.closed_on = None
    year.save(update_fields=["is_closed", "closed_on"])
    log_action(user, "academics.year_reopened", year, request=request)
    return year


# ---------- promotion ----------

def promotion_plan(institution, students=None):
    """The students who can move up, and the class they move to.

    A student moves up when their class sits on a grade level and the next
    level already has a class. Students who are deferred, withdrawn or
    graduated are left alone.
    """
    from academics.models import GradeLevel, SchoolClass, Student

    queryset = Student.objects.filter(
        institution=institution,
        school_class__isnull=False,
        status=Student.Status.ACTIVE,
    ).select_related("school_class", "school_class__grade_level", "stream")
    if students is not None:
        queryset = queryset.filter(pk__in=[s.pk for s in students])

    plan = []
    for student in queryset:
        level = student.school_class.grade_level if student.school_class else None
        if level is None:
            continue
        next_level = GradeLevel.all_objects.filter(
            institution=institution, kind=level.kind, is_active=True, order__gt=level.order
        ).order_by("order", "name").first()
        if next_level is None:
            continue
        next_class = SchoolClass.objects.filter(
            institution=institution, grade_level=next_level).first()
        if next_class is None:
            continue
        plan.append({
            "student": student,
            "from_class": student.school_class,
            "to_class": next_class,
            "from_level": level,
            "to_level": next_level,
        })
    return plan


def promote_students(institution, user, student_ids=None, request=None):
    """Move students up a level.

    Returns the list of moves that were made. Every move is written to the
    audit log with the class the student came from and the class they go to.
    """

    plan = promotion_plan(institution)
    if student_ids is not None:
        wanted = {int(pk) for pk in student_ids}
        plan = [row for row in plan if row["student"].pk in wanted]

    moved = []
    for row in plan:
        student = row["student"]
        before = {"class": student.school_class.name if student.school_class else "",
                  "stream": student.stream.name if student.stream else ""}
        target = row["to_class"]
        student.school_class = target
        student.stream = _same_stream(student.stream, target)
        student.year_or_grade = row["to_level"].name
        student.save(update_fields=["school_class", "stream", "year_or_grade"])
        after = {"class": target.name,
                 "stream": student.stream.name if student.stream else ""}
        log_action(user, "students.promoted", student, before=before, after=after,
                   request=request)
        moved.append({**row, "stream": student.stream})
    return moved


def _same_stream(stream, target_class):
    """Keep the student in the same stream when the new class has one."""
    if stream is None:
        return None
    from academics.models import Stream

    match = Stream.objects.filter(school_class=target_class, name=stream.name).first()
    return match
