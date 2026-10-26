"""The academic structure: years, terms, levels and moving students up.

Everything that changes the shape of the school lives here, so the views stay
thin and the rules can be tested on their own. A closed year or term cannot be
changed any more, and a student is only promoted when there is a class waiting
for them.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
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


# ---------- assessment components ----------

def weighted_mark(enrollment, components=None, marks=None):
    """The weighted mark for an enrolment, or None until every component is marked."""
    from academics.models import AssessmentComponent, AssessmentMark

    if components is None:
        lookup = {"course_id": enrollment.course_id} if enrollment.course_id \
            else {"subject_id": enrollment.subject_id}
        components = list(AssessmentComponent.objects.filter(**lookup))
    if not components:
        return enrollment.mark
    if marks is None:
        marks = dict(AssessmentMark.objects.filter(
            enrollment=enrollment, component__in=components,
        ).values_list("component_id", "mark"))
    if any(c.pk not in marks for c in components):
        return None
    total_weight = sum((c.weight for c in components), Decimal("0"))
    if total_weight <= 0:
        return None
    weighted = sum((marks[c.pk] * c.weight for c in components), Decimal("0"))
    return (weighted / total_weight).quantize(Decimal("0.01"))


def recalculate_enrollment_mark(enrollment):
    """Store the weighted mark on the enrolment, or clear it while incomplete."""
    from academics.models import AssessmentComponent, AssessmentMark

    lookup = {"course_id": enrollment.course_id} if enrollment.course_id \
        else {"subject_id": enrollment.subject_id}
    components = list(AssessmentComponent.objects.filter(**lookup))
    if not components:
        return enrollment.mark
    marks = dict(AssessmentMark.objects.filter(
        enrollment=enrollment, component__in=components,
    ).values_list("component_id", "mark"))
    mark = weighted_mark(enrollment, components, marks)
    if enrollment.mark != mark:
        enrollment.mark = mark
        enrollment.save(update_fields=["mark"])
    return mark


def recalculate_item_marks(course=None, subject=None):
    """Recalculate every enrolment for a course or subject after setup changes."""
    from academics.models import Enrollment

    rows = Enrollment.objects.filter(
        course=course) if course is not None else Enrollment.objects.filter(subject=subject)
    for enrollment in rows:
        recalculate_enrollment_mark(enrollment)


# ---------- results, GPA and class positions ----------

def enrollment_credits(enrollment):
    """The credit weight of one result: the offering's, the course's, or 1.

    Subjects carry no credit value in school mode, so every subject counts
    equally and the average is the ordinary mean.
    """
    if enrollment.offering_id:
        return enrollment.offering.credit_points
    if enrollment.course_id:
        return enrollment.course.credits
    return 1


def weighted_average(rows):
    """The credit-weighted average mark of the marked rows, or None."""
    total = Decimal("0")
    credits_total = 0
    for enrollment in rows:
        if enrollment.mark is None:
            continue
        credits = enrollment_credits(enrollment)
        total += enrollment.mark * credits
        credits_total += credits
    if not credits_total:
        return None
    return (total / credits_total).quantize(Decimal("0.01"))


def grade_point_average(rows, bands=None):
    """The credit-weighted grade point average of the marked rows, or None."""
    from academics.models import GradeBand

    if bands is None:
        bands = list(GradeBand.objects.order_by("-min_mark"))
    total = Decimal("0")
    credits_total = 0
    for enrollment in rows:
        if enrollment.mark is None:
            continue
        _, points = GradeBand.grade_for(enrollment.mark, bands)
        credits = enrollment_credits(enrollment)
        total += points * credits
        credits_total += credits
    if not credits_total:
        return None
    return (total / credits_total).quantize(Decimal("0.01"))


def result_summary(rows, bands=None):
    """The marked count, credits, weighted average and GPA of a set of results."""
    marked = [e for e in rows if e.mark is not None]
    return {
        "marked": len(marked),
        "credits": sum(enrollment_credits(e) for e in marked),
        "average": weighted_average(rows),
        "gpa": grade_point_average(rows, bands),
    }


def _class_filter(student):
    """The widest group that forms the student's class, from narrowest to widest."""
    if student.cohort_id:
        return {"cohort_id": student.cohort_id}
    if student.school_class_id:
        return {"school_class_id": student.school_class_id}
    if student.programme_year_id:
        return {"programme_year_id": student.programme_year_id}
    return {"programme_id": student.programme_id}


def class_position(student, term):
    """The student's (position, class size) for a term by weighted average.

    Students on the same average share a position and the next position is
    skipped, so 90, 80, 80 and 70 rank 1, 2, 2 and 4. A student with no
    marked results has no position but still sees how many were ranked.
    """
    from academics.models import Enrollment, Student

    if term is None:
        return None, 0
    peers = Student.objects.filter(**_class_filter(student)).values_list("pk", flat=True)
    rows = Enrollment.objects.filter(term=term, student_id__in=peers) \
        .select_related("offering__course")
    groups = {}
    for enrollment in rows:
        groups.setdefault(enrollment.student_id, []).append(enrollment)
    averages = []
    for group in groups.values():
        average = weighted_average(group)
        if average is not None:
            averages.append(average)
    mine = weighted_average(groups.get(student.pk, []))
    if mine is None:
        return None, len(averages)
    return sorted(averages, reverse=True).index(mine) + 1, len(averages)


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


# ---------- course registration ----------

PASS_MARK = Decimal("40")


class RegistrationError(ValidationError):
    """A registration attempt that the rules do not allow."""


def registration_issues(student, offering, on_date=None, ignore=()):
    """Return the reasons this student may not register for this offering.

    An empty list means the student is allowed to register now. Capacity is
    reported separately because a full offering puts the student on the
    waiting list instead of refusing them.
    """
    from academics.models import Enrollment

    if on_date is None:
        on_date = timezone.localdate()
    issues = []
    if student.status != student.Status.ACTIVE:
        issues.append("Only active students can register for courses.")
    if offering.course.programme_id != student.programme_id:
        issues.append("This course belongs to another programme.")

    if "term" not in ignore and offering.term.is_closed:
        issues.append(f"{offering.term} is closed.")

    programme_year = student.programme_year
    if "window" not in ignore and programme_year:
        if programme_year.registration_opens and on_date < programme_year.registration_opens:
            issues.append(
                f"Registration opens on {programme_year.registration_opens:%d %b %Y}."
            )
        if programme_year.registration_closes and on_date > programme_year.registration_closes:
            issues.append(
                f"Registration closed on {programme_year.registration_closes:%d %b %Y}."
            )
    if "window" not in ignore and offering.enrolment_deadline and on_date > offering.enrolment_deadline:
        issues.append(
            f"Registration for this course closed on {offering.enrolment_deadline:%d %b %Y}."
        )

    if "prerequisites" not in ignore:
        for rule in offering.course.prerequisites.select_related("required"):
            threshold = rule.min_mark if rule.min_mark is not None else PASS_MARK
            passed = Enrollment.objects.filter(
                student=student,
                course=rule.required,
                mark__gte=threshold,
            ).exclude(status=Enrollment.Status.DROPPED)
            if not passed.exists():
                issues.append(
                    f"{rule.required.code} must be passed with at least {threshold:g}%."
                )

    if "credits" not in ignore and programme_year and programme_year.credit_limit:
        carried = 0
        current = Enrollment.objects.filter(
            student=student, term=offering.term,
        ).exclude(status=Enrollment.Status.DROPPED).select_related("course", "offering")
        for enrollment in current:
            if enrollment.offering_id:
                carried += enrollment.offering.credit_points
            else:
                carried += enrollment.course.credits
        total = carried + offering.credit_points
        if total > programme_year.credit_limit:
            issues.append(
                f"That would load {total} credits; the limit is "
                f"{programme_year.credit_limit}."
            )
    return issues


def offering_is_full(offering):
    """True when the offering has a capacity and every place is taken."""
    return bool(offering.capacity) and offering.registered_count >= offering.capacity


def _existing_registration(student, offering):
    from academics.models import Enrollment

    return Enrollment.objects.filter(
        student=student, course=offering.course, term=offering.term,
    ).first()


@transaction.atomic
def register_student(student, offering, user=None, override=False, reason="", request=None):
    """Register a student for one offering, or put them on its waiting list.

    A registrar or administrator may override the rules, but only with a
    written reason. Duplicates are never overridden: the existing row is
    updated or the attempt is refused.
    """
    from academics.models import CourseOffering, Enrollment

    offering = CourseOffering.objects.select_for_update().select_related(
        "course", "term",
    ).get(pk=offering.pk)
    existing = _existing_registration(student, offering)
    if existing and existing.status == Enrollment.Status.REGISTERED:
        raise RegistrationError(f"{student.name} is already registered for {offering.course.code}.")
    if (existing and existing.status == Enrollment.Status.WAITLISTED and not override
            and offering_is_full(offering)):
        raise RegistrationError(
            f"{student.name} is already on the waiting list for {offering.course.code}.")

    if override and (user is None or user.role not in ("registrar", "admin")):
        raise RegistrationError("Only a registrar or administrator can override registration rules.")
    if override and not reason.strip():
        raise RegistrationError("Give a reason for the registration override.")

    issues = registration_issues(student, offering)
    full = offering_is_full(offering)
    if override:
        status = Enrollment.Status.REGISTERED
    elif issues:
        raise RegistrationError(" ".join(issues))
    else:
        status = Enrollment.Status.WAITLISTED if full else Enrollment.Status.REGISTERED

    if existing:
        existing.offering = offering
        existing.status = status
        existing.save(update_fields=["offering", "status"])
        enrollment = existing
    else:
        enrollment = Enrollment.objects.create(
            student=student,
            course=offering.course,
            term=offering.term,
            offering=offering,
            status=status,
        )

    after = {
        "course": offering.course.code,
        "term": str(offering.term),
        "status": status,
    }
    if override:
        after["reason"] = reason.strip()
        after["overridden_issues"] = issues
    log_action(
        user,
        "academics.registration_override" if override else "academics.registered",
        enrollment,
        after=after,
        request=request,
    )
    return enrollment


@transaction.atomic
def drop_enrollment(enrollment, user=None, reason="", request=None, promote=True):
    """Drop a registration and, when a place frees, take the first person off the list."""
    from academics.models import Enrollment

    enrollment = Enrollment.objects.select_for_update().select_related(
        "course", "term", "offering",
    ).get(pk=enrollment.pk)
    if enrollment.status == Enrollment.Status.DROPPED:
        return enrollment
    before = {"status": enrollment.status}
    enrollment.status = Enrollment.Status.DROPPED
    enrollment.save(update_fields=["status"])
    log_action(
        user,
        "academics.registration_dropped",
        enrollment,
        before=before,
        after={"status": enrollment.status, "reason": reason.strip()},
        request=request,
    )
    if promote and enrollment.offering_id:
        promote_next_waiting(enrollment.offering, user=user, request=request)
    return enrollment


@transaction.atomic
def promote_next_waiting(offering, user=None, request=None):
    """Move the longest-waiting student into a free place, if there is one."""
    from academics.models import CourseOffering, Enrollment

    offering = CourseOffering.objects.select_for_update().get(pk=offering.pk)
    if offering_is_full(offering):
        return None
    waiting = Enrollment.objects.select_for_update().filter(
        offering=offering, status=Enrollment.Status.WAITLISTED,
    ).order_by("pk").first()
    if waiting is None:
        return None
    waiting.status = Enrollment.Status.REGISTERED
    waiting.save(update_fields=["status"])
    log_action(
        user,
        "academics.registration_promoted",
        waiting,
        before={"status": Enrollment.Status.WAITLISTED},
        after={"status": Enrollment.Status.REGISTERED},
        request=request,
    )
    return waiting


def registration_catalogue(student, term):
    """Offerings a student can register for this term, with their current state."""
    from academics.models import CourseOffering, Enrollment

    offerings = CourseOffering.objects.filter(
        term=term, course__programme=student.programme,
    ).select_related("course", "course__programme", "lecturer", "term") \
     .prefetch_related("course__prerequisites__required")
    registrations = {
        e.offering_id: e for e in Enrollment.objects.filter(
            student=student, term=term,
        ).select_related("offering")
    }
    rows = []
    for offering in offerings:
        enrollment = registrations.get(offering.pk)
        issues = registration_issues(student, offering)
        rows.append({
            "offering": offering,
            "enrollment": enrollment,
            "issues": issues,
            "full": offering_is_full(offering),
            "places_left": offering.places_left,
            "prerequisites": list(offering.course.prerequisites.all()),
        })
    return rows
