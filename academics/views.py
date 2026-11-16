from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.decorators import role_required
from accounts.models import User
from core.access import (
    module_required,
    permission_required_code,
    user_has_code,
)
from core.branding import brand_variables
from core.modules import is_enabled
from finance.models import Invoice

from .documents import build_academic_document, build_timetable_pdf, issue_document
from .forms import (
    ActivityForm,
    AnnouncementForm,
    AssessmentComponentForm,
    AssignmentSubmissionForm,
    AwardRuleForm,
    CalendarEventForm,
    ClassSubjectAllocationForm,
    ClassTeacherForm,
    CohortForm,
    CourseForm,
    CourseOfferingForm,
    CoursePrerequisiteForm,
    EnrollForm,
    GradeBandForm,
    GradeLevelForm,
    GuardianForm,
    LecturerAssignmentForm,
    ProgrammeForm,
    ProgrammeYearForm,
    SchoolClassForm,
    SchoolSettingsForm,
    StreamForm,
    StudentRegistrationForm,
    SubjectForm,
    TermForm,
    TimetableEntryForm,
    YearForm,
)
from .models import (
    DAY_CHOICES,
    AcademicYear,
    Activity,
    Announcement,
    AssessmentComponent,
    AssessmentMark,
    AssignmentSubmission,
    Attendance,
    AwardRule,
    CalendarEvent,
    ClassTeacher,
    Cohort,
    Course,
    CourseOffering,
    CoursePrerequisite,
    DocumentVerification,
    Enrollment,
    ExamSitting,
    GradeBand,
    GradeLevel,
    Guardian,
    Programme,
    ProgrammeYear,
    ResultSheet,
    SchoolClass,
    SchoolSettings,
    Student,
    Subject,
    Term,
    TimetableEntry,
    balances_by_student,
)
from .risk import compute_at_risk
from .services import (
    RegistrationError,
    ResultsError,
    approve_results,
    award_classification,
    class_position,
    clear_result_hold,
    drop_enrollment,
    moderate_results,
    notify_absence,
    promote_students,
    promotion_plan,
    recalculate_enrollment_mark,
    recalculate_item_marks,
    register_student,
    registration_catalogue,
    release_results,
    result_clearance_terms,
    result_sheet_map,
    result_summary,
    return_results,
    submit_results,
)

MONEY_ROLES = ("admin", "registrar", "bursar")


def _err(form):
    return " ".join(" ".join(v) for v in form.errors.values())


def _by_day(entries):
    buckets = {}
    for e in entries:
        buckets.setdefault(e.day, []).append(e)
    return [(label, buckets[d]) for d, label in DAY_CHOICES if d in buckets]


def _current_term():
    return Term.objects.filter(is_current=True).first()


def _sitting_for(student):
    """The student's exam sitting: this term's if it exists, otherwise the latest."""
    qs = ExamSitting.objects.select_related("term__academic_year")
    cur = _current_term()
    if cur:
        sitting = qs.filter(student=student, term=cur).first()
        if sitting:
            return sitting
    return qs.filter(student=student).order_by("-term__start_date").first()


def _save_marks(rows, post, components=None):
    """Save posted marks onto the given enrollments. Returns error strings.

    A course or subject with assessment components takes one field per
    component; its final mark is the weighted average. Without components the
    original single mark field is used.
    """
    errors = []
    components = list(components or [])
    sheets = result_sheet_map(rows)
    for e in rows:
        sheet = sheets.get((e.course_id, e.subject_id, e.term_id))
        if sheet is not None and sheet.status != ResultSheet.Status.SUBMITTED:
            errors.append(
                f"{e.student.name}: these results are "
                f"{sheet.get_status_display().lower()}; return them before changing marks.")
            continue
        if components:
            for component in components:
                key = f"component_{component.pk}_{e.pk}"
                if key not in post:
                    continue
                raw = post.get(key, "").strip()
                if not raw:
                    AssessmentMark.objects.filter(
                        enrollment=e, component=component).delete()
                    continue
                try:
                    m = Decimal(raw)
                except InvalidOperation:
                    errors.append(f"{e.student.name}: '{raw}' is not a number.")
                    continue
                if m < 0 or m > 100:
                    errors.append(
                        f"{e.student.name}: marks must be between 0 and 100.")
                    continue
                AssessmentMark.objects.update_or_create(
                    enrollment=e, component=component, defaults={"mark": m})
            recalculate_enrollment_mark(e)
            continue
        raw = post.get(f"mark_{e.pk}", "").strip()
        if not raw:
            continue
        try:
            m = Decimal(raw)
        except InvalidOperation:
            errors.append(f"{e.student.name}: '{raw}' is not a number.")
            continue
        if m < 0 or m > 100:
            errors.append(f"{e.student.name}: marks must be between 0 and 100.")
            continue
        e.mark = m
        e.save(update_fields=["mark"])
    return errors


def _save_attendance(rows, post, errors, user=None):
    """Record the posted attendance for the attendance_date.

    Each row posts a ``status_<pk>`` select (present, late, absent, excused)
    and an optional ``reason_<pk>``. The old ``present_<pk>`` checkbox still
    works when no status was posted, so bookmarks keep saving. A newly absent
    student is handed to :func:`notify_absence`, which tells the parents once.
    """
    raw_date = post.get("attendance_date", "").strip()
    if not raw_date:
        return
    try:
        d = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        errors.append("Attendance date must be a real date.")
        return
    for e in rows:
        status = post.get(f"status_{e.pk}", "").strip()
        if status:
            if status not in Attendance.Status.values:
                errors.append(f"{e.student.name}: '{status}' is not an attendance status.")
                continue
        else:
            status = (Attendance.Status.PRESENT if f"present_{e.pk}" in post
                      else Attendance.Status.ABSENT)
        defaults = {"status": status, "recorded_by": user}
        if f"reason_{e.pk}" in post:
            defaults["reason"] = post.get(f"reason_{e.pk}", "").strip()[:200]
        attendance, _created = Attendance.objects.update_or_create(
            enrollment=e, date=d, defaults=defaults)
        if status == Attendance.Status.ABSENT:
            notify_absence(attendance)


def _attendance_rows(enrollments):
    """Sorted [(term, sessions, present, pct_or_None)] for a set of enrollments."""
    rates = Attendance.rates_by_term(enrollments)
    out = []
    for term in sorted(rates):
        sessions, present = rates[term]
        pct = round(present * 100 / sessions) if sessions else None
        out.append((term, sessions, present, pct))
    return out


def _annotate_positions(rows):
    """Attach .position and .cohort_size to each enrollment in rows."""
    table = Enrollment.cohort_table(rows)
    for e in rows:
        e.position, e.cohort_size = table.get(e.pk, (None, None))


def _components_for(kind, item):
    field = "course" if kind == "course" else "subject"
    return list(AssessmentComponent.objects.filter(**{field: item}))


def _annotate_components(rows, components):
    """Attach .component_cells to each enrollment, using one extra query."""
    if not components:
        for e in rows:
            e.component_cells = []
        return
    marks = {(e_id, c_id): mark for e_id, c_id, mark in AssessmentMark.objects.filter(
        enrollment__in=rows, component__in=components,
    ).values_list("enrollment_id", "component_id", "mark")}
    for e in rows:
        e.component_cells = [(c, marks.get((e.pk, c.pk))) for c in components]


def _result_is_privileged(user):
    return user.role in ("admin", "registrar", "bursar", "lecturer")


def _annotate_result_state(rows, student, user):
    """Attach .result_state and .result_note to each enrollment.

    Students and parents only see marks from a released sheet when the student
    has no outstanding fees, or a bursar has released that term's results. A
    course or subject with no sheet predates the workflow and stays visible.
    """
    if _result_is_privileged(user):
        for e in rows:
            e.result_state, e.result_note = "shown", ""
        return
    sheets = result_sheet_map(rows)
    owed = student.balance > 0
    cleared = result_clearance_terms(student) if owed else set()
    for e in rows:
        sheet = sheets.get((e.course_id, e.subject_id, e.term_id))
        if sheet is not None and sheet.status != ResultSheet.Status.RELEASED:
            e.result_state, e.result_note = "pending", "Awaiting release"
        elif owed and e.term_id and e.term_id not in cleared:
            e.result_state, e.result_note = "withheld", "Fees outstanding"
        else:
            e.result_state, e.result_note = "shown", ""


def _report_context(s, user):
    """Context for a student's report card. Lecturers only see their own rows."""
    rows = list(s.enrollments.select_related("course", "subject", "offering__course")
                .order_by("term", "course__code", "subject__code"))
    visible = rows
    if user.role == "lecturer":
        visible = [e for e in rows
                   if (e.course_id and e.course.lecturer_id == user.pk)
                   or (e.subject_id and e.subject.lecturer_id == user.pk)]
    _annotate_result_state(visible, s, user)
    shown = [e for e in visible if e.result_state == "shown"]
    _annotate_positions(shown)
    bands = list(GradeBand.objects.order_by("-min_mark"))
    summary = result_summary(shown, bands)
    hidden = [e for e in visible
              if e.mark is not None and e.result_state != "shown"]
    by_term = {}
    for e in visible:
        by_term.setdefault(e.term.name if e.term else "No term", []).append(e)
    rates = Attendance.rates_by_term(visible)
    term_rows = []
    for term, trows in sorted(by_term.items()):
        sessions, present = rates.get(term, (0, 0))
        pct = round(present * 100 / sessions) if sessions else None
        term_hidden = any(e.mark is not None and e.result_state != "shown"
                          for e in trows)
        term_shown = [e for e in trows if e.result_state == "shown"]
        term_summary = result_summary(term_shown, bands)
        if term_hidden:
            position, cohort_size = None, None
        else:
            position, cohort_size = class_position(s, trows[0].term)
        term_rows.append((term, trows[0].term, trows, sessions, present, pct,
                          term_summary["average"], term_summary["gpa"],
                          position, cohort_size, term_hidden))
    total_sessions = sum(v[0] for v in rates.values())
    total_present = sum(v[1] for v in rates.values())
    pending = sum(1 for e in visible
                  if e.mark is not None and e.result_state == "pending")
    withheld = sum(1 for e in visible
                   if e.mark is not None and e.result_state == "withheld")
    award = None if hidden else award_classification(s, shown, bands)
    can_clear = user.role in ("bursar", "admin") and s.balance > 0
    return {"s": s, "terms": term_rows, "bands": bands,
            "marked": sum(1 for e in visible if e.mark is not None),
            "total": len(visible),
            "average": None if hidden else summary["average"],
            "gpa": None if hidden else summary["gpa"],
            "credits": summary["credits"], "award": award,
            "pending": pending, "withheld": withheld,
            "fee_balance": s.balance if can_clear else None,
            "can_clear": can_clear,
            "cleared_term_ids": (list(result_clearance_terms(s))
                                 if can_clear else []),
            "attendance_sessions": total_sessions,
            "attendance_present": total_present,
            "attendance_pct": (round(total_present * 100 / total_sessions)
                               if total_sessions else None)}


# ---------- dashboard ----------
@login_required
def dashboard(request):
    u = request.user
    if getattr(u, "is_platform_admin", False):
        # A platform administrator belongs to EduSphere, not to a school.
        return redirect("platform_admin:dashboard")
    if u.role == "student":
        return redirect("student_portal")
    if u.role == "lecturer":
        return redirect("lecturer_portal")
    if u.role == "parent":
        return redirect("parent_portal")
    billed, paid, waived = balances_by_student()
    ctx = {"students": Student.objects.filter(status="active").count(),
           "owing": sum(1 for s in Student.objects.all()
                         if billed.get(s.pk, Decimal("0")) - paid.get(s.pk, Decimal("0"))
                         - waived.get(s.pk, Decimal("0")) > 0),
           "courses": Course.objects.count(),
           "announcements": Announcement.visible_to(request.user)[:5],
           "events": CalendarEvent.upcoming(request.user, limit=6)}
    return render(request, "academics/staff_dashboard.html", ctx)


@login_required
@permission_required_code("reports.view_dashboards", "admin.dashboard_school")
def role_dashboard(request):
    """Charts and at-risk students for the roles that run the institution."""
    from django.db.models import Sum
    from django.db.models.functions import TruncMonth

    from finance.models import Payment

    students = Student.objects.filter(status="active").select_related(
        "user", "school_class")
    billed, paid, waived = balances_by_student()
    total_billed = sum(billed.values(), Decimal("0"))
    total_paid = sum(paid.values(), Decimal("0"))
    total_waived = sum(waived.values(), Decimal("0"))
    outstanding = sum(
        (max(billed[s.pk] - paid.get(s.pk, Decimal("0"))
             - waived.get(s.pk, Decimal("0")), Decimal("0"))
         for s in students if s.pk in billed),
        Decimal("0"))
    collection_rate = (
        round((total_paid * 100 / (total_billed - total_waived)), 1)
        if total_billed > total_waived else None)

    term = _current_term()
    attendance = Attendance.objects.all()
    if term is not None:
        attendance = attendance.filter(enrollment__term=term)
    att = attendance.aggregate(
        total=Count("id"), present=Count("id", filter=Q(present=True)))
    attendance_pct = (
        round(att["present"] * 100 / att["total"], 1) if att["total"] else None)

    by_class = list(
        students.values("school_class__name", "year_or_grade")
        .annotate(total=Count("id")).order_by("-total")[:8])
    class_labels = [
        row["school_class__name"] or f"Year {row['year_or_grade']}"
        for row in by_class]
    class_values = [row["total"] for row in by_class]

    since = timezone.localdate() - timedelta(days=180)
    monthly = list(
        Payment.objects.filter(paid_on__date__gte=since)
        .annotate(month=TruncMonth("paid_on")).values("month")
        .annotate(total=Sum("amount")).order_by("month"))
    month_labels = [row["month"].strftime("%b %Y") for row in monthly]
    month_values = [float(row["total"]) for row in monthly]

    at_risk = []
    if user_has_code(request.user, "reports.view_at_risk"):
        at_risk = compute_at_risk(students=students)[:25]

    return render(request, "academics/role_dashboard.html", {
        "students": students.count(),
        "total_billed": total_billed, "total_paid": total_paid,
        "total_waived": total_waived, "outstanding": outstanding,
        "collection_rate": collection_rate, "attendance_pct": attendance_pct,
        "term": term, "class_labels": class_labels,
        "class_values": class_values, "month_labels": month_labels,
        "month_values": month_values, "at_risk": at_risk,
        "can_view_at_risk": user_has_code(request.user, "reports.view_at_risk"),
        "announcements": Announcement.visible_to(request.user)[:5],
        "events": CalendarEvent.upcoming(request.user, limit=6)})


# ---------- notices ----------
@role_required("registrar", "bursar", "lecturer")
def notices(request):
    can_post = request.user.role in ("admin", "registrar")
    form = AnnouncementForm()
    if request.method == "POST":
        if not can_post:
            raise PermissionDenied("Only the registrar can post or remove notices.")
        if request.POST.get("action") == "delete":
            a = get_object_or_404(Announcement, pk=request.POST.get("pk"))
            a.delete()
            messages.success(request, "Notice removed.")
            return redirect("notices")
        f = AnnouncementForm(request.POST)
        if f.is_valid():
            a = f.save(commit=False)
            a.posted_by = request.user
            a.save()
            messages.success(request, "Notice posted.")
            return redirect("notices")
        form = f
    return render(request, "academics/notices.html", {
        "notices": Announcement.objects.select_related("posted_by", "school_class"),
        "form": form, "can_post": can_post})


# ---------- staff portal ----------
@role_required("registrar", "bursar")
def student_search(request):
    return render(request, "academics/student_search.html")


@role_required("registrar", "bursar", "lecturer")
def student_search_api(request):
    q = request.GET.get("q", "").strip()[:100]
    qs = Student.objects.select_related("user", "programme")
    if q:
        from core.numbering import matching_pks

        numbers = matching_pks(qs, "student_no", q, limit=30)
        qs = qs.filter(Q(student_no__icontains=q) | Q(pk__in=numbers)
                       | Q(user__first_name__icontains=q)
                       | Q(user__last_name__icontains=q)
                       | Q(programme__name__icontains=q))
    billed, paid, waived = balances_by_student()
    data = [{"id": s.pk, "no": s.student_no, "name": s.name, "programme": s.programme.name,
             "status": s.get_status_display(),
             "balance": f"{billed.get(s.pk, Decimal('0')) - paid.get(s.pk, Decimal('0'))
                           - waived.get(s.pk, Decimal('0')):.2f}"}
            for s in qs[:30]]
    return JsonResponse({"results": data})


@role_required("registrar", "bursar", "lecturer")
def student_detail(request, pk):
    s = get_object_or_404(Student.objects.select_related("user", "programme", "school_class",
                                                         "stream"), pk=pk)
    return render(request, "academics/student_detail.html", {
        "s": s, "enrollments": s.enrollments.select_related("course", "subject"),
        "activities": s.activities.all(), "invoices": s.invoices.prefetch_related(
            "payments", "waivers"),
        "show_money": request.user.role in MONEY_ROLES,
        "guardians": s.guardian_links.select_related("parent"),
        "guardian_form": GuardianForm(),
        "enroll_form": EnrollForm(s), "activity_form": ActivityForm()})


@role_required("registrar")
@transaction.atomic
def student_create(request):
    form = StudentRegistrationForm(request.POST or None)
    if form.is_valid():
        d = form.cleaned_data
        user = User.objects.create_user(d["username"], d["email"], d["password"],
            first_name=d["first_name"], last_name=d["last_name"], role="student")
        s = form.save(commit=False)
        s.user = user
        s.save()
        if s.programme.tuition_fee:
            Invoice.objects.create(student=s, description=f"Tuition – {s.programme.name}",
                amount=s.programme.tuition_fee, due_date=date.today(),
                created_by=request.user)
        messages.success(request, f"Account created for {s.name} ({s.student_no}).")
        return redirect("student_detail", pk=s.pk)
    return render(request, "academics/student_form.html", {"form": form})


@role_required("registrar")
def enroll(request, pk):
    s = get_object_or_404(Student, pk=pk)
    if request.method == "POST":
        f = EnrollForm(s, request.POST)
        if f.is_valid():
            e = f.save(commit=False)
            e.student = s
            try:
                e.save()
                messages.success(request, "Enrolled.")
            except ValidationError as err:
                messages.error(request, " ".join(err.messages))
        else:
            messages.error(request, f"Could not enroll. {_err(f)}")
    return redirect("student_detail", pk=pk)


@role_required("registrar", "student")
def course_registration(request, pk=None):
    """Register a student for this term's course offerings."""
    if request.user.role == "student":
        student = get_object_or_404(Student, user=request.user)
        if pk is not None and int(pk) != student.pk:
            raise PermissionDenied
    else:
        if pk is None:
            raise PermissionDenied
        student = get_object_or_404(
            Student.objects.select_related("programme", "programme_year"), pk=pk)

    terms = Term.objects.select_related("academic_year").order_by(
        "-academic_year__start_date", "start_date")
    term_id = request.POST.get("term") or request.GET.get("term")
    term = None
    if term_id:
        try:
            term = terms.filter(pk=int(term_id)).first()
        except (TypeError, ValueError):
            term = None
    if term is None:
        term = _current_term()
    if term is None:
        term = terms.first()

    if request.method == "POST" and term is not None:
        action = request.POST.get("action", "register")
        if action == "drop":
            enrollment = get_object_or_404(
                Enrollment.objects.select_related("offering"), pk=request.POST.get("enrollment"))
            if enrollment.student_id != student.pk:
                raise PermissionDenied
            drop_enrollment(
                enrollment,
                user=request.user,
                reason=request.POST.get("reason", ""),
                request=request,
            )
            messages.success(request, f"Dropped {enrollment.label}.")
        else:
            offering = get_object_or_404(
                CourseOffering.objects.select_related("course", "term"),
                pk=request.POST.get("offering"),
            )
            if offering.term_id != term.pk:
                messages.error(request, "That course is not offered in the selected term.")
            else:
                override = request.POST.get("override") == "1"
                try:
                    enrollment = register_student(
                        student,
                        offering,
                        user=request.user,
                        override=override,
                        reason=request.POST.get("reason", ""),
                        request=request,
                    )
                except RegistrationError as err:
                    messages.error(request, " ".join(err.messages))
                else:
                    if enrollment.status == Enrollment.Status.WAITLISTED:
                        messages.success(
                            request,
                            f"{offering.course.code} is full. "
                            f"{student.name} is on the waiting list.",
                        )
                    else:
                        messages.success(request, f"Registered for {offering.course.code}.")
        return redirect(f"{reverse('course_registration', args=[student.pk])}?term={term.pk}")

    return render(request, "academics/course_registration.html", {
        "student": student,
        "term": term,
        "terms": terms,
        "rows": registration_catalogue(student, term) if term else [],
        "can_override": request.user.role in ("registrar", "admin"),
        "is_self": request.user.role == "student",
    })


@role_required("registrar", "lecturer")
def add_activity(request, pk):
    s = get_object_or_404(Student, pk=pk)
    if request.method == "POST":
        f = ActivityForm(request.POST)
        if f.is_valid():
            a = f.save(commit=False)
            a.student = s
            a.save()
            messages.success(request, "Activity recorded.")
        else:
            messages.error(request, f"Could not save the activity. {_err(f)}")
    return redirect("student_detail", pk=pk)


# ---------- academic structure ----------
@role_required("registrar")
def academic_years(request):
    year_form, term_form = YearForm(), TermForm()
    event_form = CalendarEventForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "new_year":
            year_form = YearForm(request.POST)
            if year_form.is_valid():
                y = year_form.save()
                messages.success(request, f"Academic year {y.name} added.")
                return redirect("academic_years")
            messages.error(request, f"Could not add the year. {_err(year_form)}")
        elif action == "new_term":
            term_form = TermForm(request.POST)
            if term_form.is_valid():
                t = term_form.save()
                messages.success(request, f"{t.name} added to {t.academic_year.name}.")
                return redirect("academic_years")
            messages.error(request, f"Could not add the term. {_err(term_form)}")
        elif action == "new_event":
            event_form = CalendarEventForm(request.POST)
            if event_form.is_valid():
                e = event_form.save()
                messages.success(request, f"Calendar event {e.title} added.")
                return redirect("academic_years")
            messages.error(request, f"Could not add the event. {_err(event_form)}")
        elif action == "delete_event":
            event = CalendarEvent.objects.filter(pk=request.POST.get("pk")).first()
            if event is None:
                messages.error(request, "That calendar event was not found.")
            else:
                event.delete()
                messages.success(request, f"{event.title} removed from the calendar.")
            return redirect("academic_years")
        elif action == "year_current":
            y = AcademicYear.objects.filter(pk=request.POST.get("pk")).first()
            if y:
                y.is_current = True
                y.save()
                messages.success(request, f"{y.name} is now the current year.")
            else:
                messages.error(request, "That academic year was not found.")
            return redirect("academic_years")
        elif action == "term_current":
            t = Term.objects.filter(pk=request.POST.get("pk")).first()
            if t:
                t.is_current = True
                t.save()
                messages.success(request, f"{t.name} is now the current term.")
            else:
                messages.error(request, "That term was not found.")
            return redirect("academic_years")
        elif action in ("close_year", "reopen_year", "close_term", "reopen_term"):
            return _close_or_reopen(request, action)
    return render(request, "academics/academic_years.html", {
        "years": AcademicYear.objects.prefetch_related("terms"),
        "events": CalendarEvent.objects.select_related("school_class"),
        "year_form": year_form, "term_form": term_form, "event_form": event_form})


def _close_or_reopen(request, action):
    """Close or reopen a year or a term."""
    from .services import close_term, close_year, reopen_term, reopen_year

    pk = request.POST.get("pk")
    if action.endswith("year"):
        row = AcademicYear.objects.filter(pk=pk).first()
        if row is None:
            messages.error(request, "That academic year was not found.")
            return redirect("academic_years")
        if action == "close_year":
            close_year(row, user=request.user, request=request)
            messages.success(request, f"{row.name} is closed. It cannot be changed any more.")
        else:
            reopen_year(row, user=request.user, request=request)
            messages.success(request, f"{row.name} is open again.")
    else:
        row = Term.objects.filter(pk=pk).first()
        if row is None:
            messages.error(request, "That term was not found.")
            return redirect("academic_years")
        if action == "close_term":
            close_term(row, user=request.user, request=request)
            messages.success(request, f"{row.name} is closed.")
        else:
            reopen_term(row, user=request.user, request=request)
            messages.success(request, f"{row.name} is open again.")
    return redirect("academic_years")


@role_required("registrar")
def grade_levels(request):
    """The steps of the school, and the classes that sit on them."""
    form = GradeLevelForm(request.POST or None)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "new_level":
            if form.is_valid():
                level = form.save()
                messages.success(request, f"Level {level.name} added.")
                return redirect("grade_levels")
            messages.error(request, f"Could not add the level. {_err(form)}")
        elif action == "link":
            school_class = SchoolClass.objects.filter(pk=request.POST.get("class")).first()
            level = GradeLevel.objects.filter(pk=request.POST.get("level")).first()
            if school_class is None or level is None:
                messages.error(request, "Choose a class and a level.")
            else:
                school_class.grade_level = level
                school_class.save(update_fields=["grade_level"])
                messages.success(request, f"{school_class.name} now sits on {level.name}.")
            return redirect("grade_levels")
        elif action == "unlink":
            school_class = SchoolClass.objects.filter(pk=request.POST.get("class")).first()
            if school_class is not None:
                school_class.grade_level = None
                school_class.save(update_fields=["grade_level"])
                messages.success(request, f"{school_class.name} no longer sits on a level.")
            return redirect("grade_levels")
    levels = GradeLevel.objects.prefetch_related("classes")
    classes = SchoolClass.objects.select_related("grade_level").order_by("name")
    return render(request, "academics/grade_levels.html", {
        "levels": levels, "classes": classes, "form": form})


@role_required("registrar")
def promotion(request):
    """Move students up a level when the year ends."""
    institution = request.institution
    if request.method == "POST":
        wanted = request.POST.getlist("students")
        moved = promote_students(institution, request.user, student_ids=wanted, request=request)
        if moved:
            messages.success(request, f"{len(moved)} students moved up.")
        else:
            messages.warning(request, "Nobody was moved. Tick the students you want to move.")
        return redirect("promotion")
    return render(request, "academics/promotion.html", {
        "plan": promotion_plan(institution)})


@role_required("registrar")
def class_list(request):
    form = SchoolClassForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            c = form.save()
            messages.success(request, f"{c.name} added.")
            return redirect("class_list")
        messages.error(request, f"Could not add the class. {_err(form)}")
    year = AcademicYear.objects.filter(is_current=True).first()
    teachers = {}
    if year:
        teachers = {ct.school_class_id: ct.teacher
                    for ct in ClassTeacher.objects.filter(academic_year=year)
                    .select_related("teacher")}
    rows = [{"c": c, "teacher": teachers.get(c.pk), "n": c.students.count()}
            for c in SchoolClass.objects.prefetch_related("streams")]
    return render(request, "academics/class_list.html",
                  {"rows": rows, "form": form})


@role_required("registrar", "lecturer")
def class_detail(request, pk):
    c = get_object_or_404(SchoolClass, pk=pk)
    can_edit = request.user.role in ("registrar", "admin")
    year = AcademicYear.objects.filter(is_current=True).first()
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        action = request.POST.get("action")
        if action == "stream":
            f = StreamForm(request.POST)
            if f.is_valid():
                name = f.cleaned_data["name"]
                if c.streams.filter(name__iexact=name).exists():
                    messages.error(request, f"{c.name} already has a stream called {name}.")
                else:
                    s = f.save(commit=False)
                    s.school_class = c
                    s.save()
                    messages.success(request, f"Stream {s.name} added to {c.name}.")
            else:
                messages.error(request, f"Could not add the stream. {_err(f)}")
        elif action == "teacher":
            if not year:
                messages.error(request, "Add an academic year and set it as current first.")
            else:
                f = ClassTeacherForm(request.POST)
                if f.is_valid():
                    ClassTeacher.objects.update_or_create(
                        school_class=c, academic_year=year,
                        defaults={"teacher": f.cleaned_data["teacher"]})
                    messages.success(request, "Class teacher saved.")
                else:
                    messages.error(request, f"Could not save the class teacher. {_err(f)}")
        elif action == "allocate":
            if not year:
                messages.error(request, "Add an academic year and set it as current first.")
            else:
                f = ClassSubjectAllocationForm(request.POST)
                if f.is_valid():
                    subject = f.cleaned_data["subject"]
                    if c.allocations.filter(academic_year=year, subject=subject).exists():
                        messages.error(request, f"{c.name} already takes {subject.name} this year.")
                    else:
                        allocation = f.save(commit=False)
                        allocation.school_class = c
                        allocation.academic_year = year
                        allocation.save()
                        messages.success(request, f"{subject.name} added to {c.name}.")
                else:
                    messages.error(request, f"Could not add the subject. {_err(f)}")
        elif action == "unallocate":
            allocation = c.allocations.filter(pk=request.POST.get("pk")).first()
            if allocation is not None:
                allocation.delete()
                messages.success(request, "Subject removed from the class.")
        return redirect("class_detail", pk=pk)
    ct = None
    if year:
        ct = ClassTeacher.objects.filter(school_class=c, academic_year=year) \
                                 .select_related("teacher").first()
    allocations = c.allocations.select_related("subject", "lecturer")
    if year:
        allocations = allocations.filter(academic_year=year)
    entries = TimetableEntry.objects.filter(school_class=c).select_related(
        "subject", "lecturer", "stream", "term")
    term = _current_term()
    if term:
        entries = entries.filter(term=term)
    return render(request, "academics/class_detail.html", {
        "c": c, "can_edit": can_edit, "year": year, "class_teacher": ct,
        "students": c.students.select_related("user", "stream").order_by("user__first_name"),
        "groups": _by_day(entries), "term": term,
        "allocations": allocations,
        "stream_form": StreamForm(), "teacher_form": ClassTeacherForm(),
        "allocation_form": ClassSubjectAllocationForm()})


@role_required("registrar", "lecturer")
def subject_list(request):
    can_edit = request.user.role in ("registrar", "admin")
    form = SubjectForm(request.POST or None)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        if form.is_valid():
            s = form.save()
            messages.success(request, f"Subject {s.code} added.")
            return redirect("subject_list")
        messages.error(request, f"Could not add the subject. {_err(form)}")
    return render(request, "academics/subject_list.html",
                  {"subjects": Subject.objects.all(), "form": form, "can_edit": can_edit})


@role_required("registrar")
def programme_list(request):
    programme_form, course_form = ProgrammeForm(), CourseForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "new_programme":
            programme_form = ProgrammeForm(request.POST)
            if programme_form.is_valid():
                p = programme_form.save()
                messages.success(request, f"{p.name} added.")
                return redirect("programme_list")
            messages.error(request, f"Could not add the programme. {_err(programme_form)}")
        elif action == "new_course":
            course_form = CourseForm(request.POST)
            if course_form.is_valid():
                c = course_form.save()
                messages.success(request, f"Course {c.code} added to {c.programme.name}.")
                return redirect("programme_list")
            messages.error(request, f"Could not add the course. {_err(course_form)}")
    return render(request, "academics/programme_list.html", {
        "programmes": Programme.objects.prefetch_related("courses__lecturer"),
        "programme_form": programme_form, "course_form": course_form})


@role_required("registrar")
def college_structure(request):
    """Manage programme years, cohorts, course offerings and prerequisites."""
    programme_year_form = ProgrammeYearForm()
    cohort_form = CohortForm()
    offering_form = CourseOfferingForm()
    prerequisite_form = CoursePrerequisiteForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "new_programme_year":
            programme_year_form = ProgrammeYearForm(request.POST)
            if programme_year_form.is_valid():
                row = programme_year_form.save()
                messages.success(request, f"{row} added.")
                return redirect("college_structure")
            messages.error(request, f"Could not add the programme year. {_err(programme_year_form)}")
        elif action == "new_cohort":
            cohort_form = CohortForm(request.POST)
            if cohort_form.is_valid():
                row = cohort_form.save()
                messages.success(request, f"Cohort {row.name} added.")
                return redirect("college_structure")
            messages.error(request, f"Could not add the cohort. {_err(cohort_form)}")
        elif action == "new_offering":
            offering_form = CourseOfferingForm(request.POST)
            if offering_form.is_valid():
                row = offering_form.save()
                messages.success(request, f"Offering {row} added.")
                return redirect("college_structure")
            messages.error(request, f"Could not add the offering. {_err(offering_form)}")
        elif action == "new_prerequisite":
            prerequisite_form = CoursePrerequisiteForm(request.POST)
            if prerequisite_form.is_valid():
                row = prerequisite_form.save()
                messages.success(request, f"Prerequisite {row} added.")
                return redirect("college_structure")
            messages.error(request, f"Could not add the prerequisite. {_err(prerequisite_form)}")
    return render(request, "academics/college_structure.html", {
        "programme_years": ProgrammeYear.objects.select_related(
            "programme", "academic_year").prefetch_related("cohorts"),
        "cohorts": Cohort.objects.select_related(
            "programme_year__programme", "lecturer").order_by(
                "programme_year__programme__code", "name"),
        "offerings": CourseOffering.objects.select_related(
            "course", "course__programme", "term__academic_year", "lecturer"),
        "prerequisites": CoursePrerequisite.objects.select_related(
            "course", "required"),
        "programme_year_form": programme_year_form,
        "cohort_form": cohort_form,
        "offering_form": offering_form,
        "prerequisite_form": prerequisite_form,
    })


@role_required("registrar")
def timetable(request):
    entries = TimetableEntry.objects.select_related(
        "subject", "course", "course__programme", "lecturer", "school_class", "stream", "term")
    if request.GET.get("class"):
        entries = entries.filter(school_class_id=request.GET["class"])
    if request.GET.get("lecturer"):
        entries = entries.filter(lecturer_id=request.GET["lecturer"])
    form = TimetableEntryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Lesson added to the timetable.")
        return redirect("timetable")
    return render(request, "academics/timetable.html", {
        "groups": _by_day(entries), "form": form, "can_add": True,
        "page_title": "Timetable", "term": _current_term(),
        "classes": SchoolClass.objects.all(),
        "lecturers": User.tenant_objects.filter(role__in=("lecturer", "admin"), is_active=True)
                                 .order_by("first_name"),
        "filter_class": request.GET.get("class", ""),
        "filter_lecturer": request.GET.get("lecturer", "")})


@role_required("lecturer")
def my_timetable(request):
    entries = TimetableEntry.objects.filter(lecturer=request.user).select_related(
        "subject", "course", "course__programme", "school_class", "stream", "term")
    term = _current_term()
    if term:
        entries = entries.filter(term=term)
    return render(request, "academics/timetable.html", {
        "groups": _by_day(entries), "can_add": False,
        "page_title": "My timetable", "term": term})


def _timetable_pdf_response(request, title, entries, term, subtitle=""):
    pdf = build_timetable_pdf(request.institution, title, entries, term, subtitle)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="timetable.pdf"'
    return response


@role_required("registrar")
def timetable_pdf(request):
    """A printable PDF of the weekly timetable, honouring the class and teacher filters."""
    entries = TimetableEntry.objects.select_related(
        "subject", "course", "course__programme", "lecturer", "school_class", "stream", "term")
    term = _current_term()
    if term:
        entries = entries.filter(term=term)
    title = "Timetable"
    subtitle = str(term) if term else "All terms"
    school_class = None
    if request.GET.get("class"):
        entries = entries.filter(school_class_id=request.GET["class"])
        school_class = SchoolClass.objects.filter(pk=request.GET["class"]).first()
    if request.GET.get("lecturer"):
        lecturer = User.tenant_objects.filter(pk=request.GET["lecturer"]).first()
        entries = entries.filter(lecturer_id=request.GET["lecturer"])
        if lecturer is not None:
            title = f"{lecturer.get_full_name() or lecturer.username} – timetable"
    if school_class is not None:
        title = f"{school_class.name} – timetable"
    return _timetable_pdf_response(request, title, list(entries), term, subtitle)


@role_required("lecturer")
def my_timetable_pdf(request):
    entries = TimetableEntry.objects.filter(lecturer=request.user).select_related(
        "subject", "course", "course__programme", "school_class", "stream", "term")
    term = _current_term()
    if term:
        entries = entries.filter(term=term)
    name = request.user.get_full_name() or request.user.username
    return _timetable_pdf_response(
        request, f"{name} – timetable", list(entries), term)


@role_required("student")
def my_student_timetable_pdf(request):
    s = get_object_or_404(Student, user=request.user)
    term = _current_term()
    return _timetable_pdf_response(
        request, f"{s.name} – timetable", list(TimetableEntry.for_student(s)), term)


@role_required()
def school_settings(request):
    obj = SchoolSettings.get()
    form = SchoolSettingsForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Settings saved.")
        return redirect("school_settings")
    return render(request, "academics/school_settings.html", {"form": form})


# ---------- lecturer portal ----------
@role_required("lecturer")
def lecturer_portal(request):
    term = _current_term()
    lessons = TimetableEntry.objects.filter(lecturer=request.user).select_related(
        "subject", "course", "course__programme", "school_class", "stream", "term")
    if term:
        lessons = lessons.filter(term=term)
    assignments = (
        Activity.objects.filter(created_by=request.user, kind=Activity.Kind.ASSIGN)
        .annotate(submission_count=Count("submissions"))
        .select_related("course", "subject")
        .order_by("due_date", "pk"))
    return render(request, "academics/lecturer_portal.html", {
        "courses": request.user.courses.all(),
        "subjects": request.user.subjects_taught.all(),
        "timetable": lessons,
        "term": term,
        "assignments": assignments,
        "events": CalendarEvent.upcoming(request.user, limit=6),
        "announcements": Announcement.visible_to(request.user)[:5]})


def _assignment_group(activity):
    rows = Activity.objects.filter(
        created_by=activity.created_by, kind=Activity.Kind.ASSIGN,
        title=activity.title, due_date=activity.due_date)
    if activity.course_id:
        rows = rows.filter(course_id=activity.course_id)
    if activity.subject_id:
        rows = rows.filter(subject_id=activity.subject_id)
    return rows


@role_required("lecturer")
def lecturer_assignments(request):
    activities = (
        Activity.objects.filter(created_by=request.user, kind=Activity.Kind.ASSIGN)
        .select_related("course", "subject").order_by("due_date", "pk"))
    groups = {}
    for activity in activities:
        key = (activity.title, activity.due_date, activity.course_id,
               activity.subject_id)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "activity": activity, "students": 0, "submissions": 0}
        group["students"] += 1
        group["submissions"] += activity.submissions.count()
    return render(request, "academics/lecturer_assignments.html", {
        "groups": list(groups.values()),
        "form": LecturerAssignmentForm(user=request.user)})


@role_required("lecturer")
def assignment_create(request):
    form = LecturerAssignmentForm(
        request.user, request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        course = form.cleaned_data["course"]
        subject = form.cleaned_data["subject"]
        enrollments = Enrollment.objects.filter(course=course) if course else \
            Enrollment.objects.filter(subject=subject)
        term = _current_term()
        if term:
            enrollments = enrollments.filter(term=term)
        students = [row.student for row in enrollments.select_related("student")]
        if not students:
            messages.error(request, "Nobody is enrolled for that course or subject yet.")
        else:
            upload = form.cleaned_data.get("attachment")
            data = upload.read() if upload else None
            today = timezone.localdate()
            for student in students:
                Activity.objects.create(
                    institution=student.institution, student=student,
                    course=course, subject=subject, kind=Activity.Kind.ASSIGN,
                    title=form.cleaned_data["title"], date=today,
                    due_date=form.cleaned_data["due_date"],
                    detail=form.cleaned_data.get("detail", ""),
                    attachment=(ContentFile(data, name=upload.name)
                                if data else None),
                    created_by=request.user)
            messages.success(
                request, f"Assignment set for {len(students)} students.")
            return redirect("lecturer_assignments")
    return render(request, "academics/assignment_form.html", {"form": form})


@role_required("lecturer")
def assignment_detail(request, pk):
    activity = get_object_or_404(
        Activity, pk=pk, created_by=request.user, kind=Activity.Kind.ASSIGN)
    group = _assignment_group(activity).select_related("student__user")
    submissions = AssignmentSubmission.objects.filter(
        assignment__in=group).select_related("student__user")
    return render(request, "academics/assignment_detail.html", {
        "activity": activity,
        "students": group,
        "submissions": submissions,
        "submitted_ids": {row.assignment_id for row in submissions}})


def _roster_access(user, course):
    if user.role == "admin" or course.lecturer_id == user.pk:
        return True
    return False


@role_required("registrar", "lecturer")
def course_roster(request, pk):
    course = get_object_or_404(Course.objects.select_related("programme"), pk=pk)
    allowed = _roster_access(request.user, course) or request.user.role == "registrar"
    if not allowed:
        raise PermissionDenied("Only the course lecturer can open this roster.")
    rows = course.enrollments.select_related("student__user")
    components = _components_for("course", course)
    if request.method == "POST":
        if not _roster_access(request.user, course):
            raise PermissionDenied("Only the course lecturer can save marks.")
        errors = _save_marks(rows, request.POST, components)
        _save_attendance(rows, request.POST, errors, user=request.user)
        if errors:
            for msg in errors:
                messages.error(request, msg)
        else:
            messages.success(request, "Marks and attendance saved.")
        return redirect("course_roster", pk=pk)
    _annotate_components(rows, components)
    return render(request, "academics/course_roster.html", {
        "course": course, "rows": rows, "components": components,
        "can_configure": request.user.role in ("admin", "registrar")
        or course.lecturer_id == request.user.pk,
        "today": date.today()})


# ---------- assessment and reports ----------
@role_required("registrar", "lecturer")
def subject_roster(request, pk):
    subject = get_object_or_404(Subject.objects.select_related("lecturer"), pk=pk)

    def owns(user):
        return user.role == "admin" or subject.lecturer_id == user.pk

    if not (owns(request.user) or request.user.role == "registrar"):
        raise PermissionDenied("Only the subject teacher can open this roster.")
    rows = subject.enrollments.select_related("student__user") \
                             .order_by("student__user__last_name", "student__user__first_name")
    components = _components_for("subject", subject)
    if request.method == "POST":
        if not owns(request.user):
            raise PermissionDenied("Only the subject teacher can save marks.")
        errors = _save_marks(rows, request.POST, components)
        had_attendance = bool(request.POST.get("attendance_date", "").strip())
        _save_attendance(rows, request.POST, errors, user=request.user)
        if errors:
            for msg in errors:
                messages.error(request, msg)
        else:
            messages.success(request, "Marks and attendance saved." if had_attendance
                             else "Marks saved.")
        return redirect("subject_roster", pk=pk)
    _annotate_components(rows, components)
    return render(request, "academics/subject_roster.html", {
        "subject": subject, "rows": rows, "components": components,
        "can_save": owns(request.user),
        "can_configure": request.user.role in ("admin", "registrar")
        or subject.lecturer_id == request.user.pk,
        "today": date.today()})


@role_required("registrar", "lecturer")
def marks_report(request, kind, pk):
    if kind == "course":
        item = get_object_or_404(Course.objects.select_related("programme", "lecturer"), pk=pk)
    elif kind == "subject":
        item = get_object_or_404(Subject.objects.select_related("lecturer"), pk=pk)
    else:
        raise PermissionDenied("Unknown mark sheet.")
    owner_id = getattr(item, "lecturer_id", None)
    if not (request.user.role in ("registrar", "admin") or owner_id == request.user.pk):
        raise PermissionDenied("Only the teacher in charge can open this mark sheet.")
    rows = list(item.enrollments.select_related("student__user")
                .order_by("student__user__last_name", "student__user__first_name"))
    components = _components_for(kind, item)
    _annotate_positions(rows)
    _annotate_components(rows, components)
    marks = [e.mark for e in rows if e.mark is not None]
    average = (sum(marks) / len(marks)).quantize(Decimal("0.1")) if marks else None
    return render(request, "academics/marks_report.html", {
        "item": item, "kind": kind, "rows": rows, "marked": len(marks),
        "average": average, "components": components,
        "can_configure": request.user.role in ("admin", "registrar")
        or owner_id == request.user.pk,
        "back": reverse("course_roster" if kind == "course" else "subject_roster",
                        args=[pk])})


@role_required("registrar", "lecturer")
def results_workflow(request, kind, pk):
    if kind == "course":
        item = get_object_or_404(Course.objects.select_related("programme", "lecturer"), pk=pk)
    elif kind == "subject":
        item = get_object_or_404(Subject.objects.select_related("lecturer"), pk=pk)
    else:
        raise PermissionDenied("Unknown mark sheet.")
    owner_id = getattr(item, "lecturer_id", None)
    can_review = request.user.role in ("registrar", "admin")
    if not (can_review or owner_id == request.user.pk):
        raise PermissionDenied("Only the teacher in charge can open the results workflow.")
    lookup = {kind: item}
    if request.method == "POST":
        raw_term = request.POST.get("term", "")
        if not raw_term.isdigit():
            messages.error(request, "Choose a term first.")
            return redirect("results_workflow", kind=kind, pk=pk)
        term = get_object_or_404(Term, pk=raw_term)
        sheet = ResultSheet.objects.filter(term=term, **lookup).first()
        action = request.POST.get("action", "")
        note = request.POST.get("note", "")
        try:
            if action == "submit":
                submit_results(kind, item, term, request.user, request=request)
                messages.success(request, f"Results for {term} submitted for moderation.")
            elif action in ("moderate", "approve", "release", "return"):
                if not can_review:
                    raise PermissionDenied("Only the registrar can review results.")
                if sheet is None:
                    raise ResultsError("Submit the marks before reviewing them.")
                if action == "moderate":
                    moderate_results(sheet, request.user, note, request=request)
                    messages.success(request, f"Results for {term} moderated.")
                elif action == "approve":
                    approve_results(sheet, request.user, note, request=request)
                    messages.success(request, f"Results for {term} approved.")
                elif action == "release":
                    release_results(sheet, request.user, request=request)
                    messages.success(request, f"Results for {term} released to students.")
                else:
                    return_results(sheet, request.user, note, request=request)
                    messages.success(request, f"Results for {term} returned for changes.")
            else:
                messages.error(request, "Unknown workflow action.")
        except ResultsError as exc:
            messages.error(request, str(exc))
        return redirect("results_workflow", kind=kind, pk=pk)
    term_ids = (Enrollment.objects.filter(**lookup).exclude(term=None)
                .values_list("term_id", flat=True).distinct())
    terms = list(Term.objects.filter(pk__in=term_ids)
                 .select_related("academic_year").order_by("-start_date"))
    counts = {
        row["term_id"]: (row["total"], row["marked"])
        for row in Enrollment.objects.filter(**lookup).exclude(term=None)
        .values("term_id").annotate(
            total=Count("pk"), marked=Count("pk", filter=Q(mark__isnull=False)))
    }
    sheets = {s.term_id: s for s in ResultSheet.objects.filter(**lookup)}
    rows = [(term, sheets.get(term.pk), *counts.get(term.pk, (0, 0)))
            for term in terms]
    return render(request, "academics/results_workflow.html", {
        "item": item, "kind": kind, "rows": rows,
        "can_review": can_review, "can_submit": can_review
        or owner_id == request.user.pk,
        "back": reverse("course_roster" if kind == "course" else "subject_roster",
                        args=[pk]),
        "report_url": reverse("marks_report", args=[kind, pk])})


@role_required("registrar", "lecturer")
def assessment_components(request, kind, pk):
    if kind == "course":
        item = get_object_or_404(Course.objects.select_related("programme", "lecturer"), pk=pk)
    elif kind == "subject":
        item = get_object_or_404(Subject.objects.select_related("lecturer"), pk=pk)
    else:
        raise PermissionDenied("Unknown mark sheet.")
    owner_id = getattr(item, "lecturer_id", None)
    if not (request.user.role in ("registrar", "admin") or owner_id == request.user.pk):
        raise PermissionDenied("Only the teacher in charge can change the assessment setup.")
    components = AssessmentComponent.objects.filter(
        **{kind: item}).order_by("order", "name")
    form = AssessmentComponentForm()
    if request.method == "POST":
        action = request.POST.get("action", "new")
        if action == "delete":
            raw_pk = request.POST.get("pk", "")
            if not raw_pk.isdigit():
                raise Http404("That assessment component does not exist.")
            component = get_object_or_404(components, pk=raw_pk)
            name = component.name
            component.delete()
            recalculate_item_marks(**{kind: item})
            messages.success(request, f"{name} removed; final marks recalculated.")
            return redirect("assessment_components", kind=kind, pk=pk)
        form = AssessmentComponentForm(
            request.POST, instance=AssessmentComponent(**{kind: item}))
        if form.is_valid():
            component = form.save()
            recalculate_item_marks(**{kind: item})
            messages.success(request, f"{component.name} added; enter its marks on the roster.")
            return redirect("assessment_components", kind=kind, pk=pk)
    return render(request, "academics/assessment_components.html", {
        "item": item, "kind": kind, "components": components, "form": form,
        "back": reverse("course_roster" if kind == "course" else "subject_roster",
                        args=[pk]),
        "report_url": reverse("marks_report", args=[kind, pk])})


@role_required("registrar", "lecturer")
def attendance_register(request, kind, pk):
    if kind == "course":
        item = get_object_or_404(Course.objects.select_related("programme", "lecturer"), pk=pk)
        back = reverse("course_roster", args=[pk])
    elif kind == "subject":
        item = get_object_or_404(Subject.objects.select_related("lecturer"), pk=pk)
        back = reverse("subject_roster", args=[pk])
    else:
        raise PermissionDenied("Unknown register.")
    owner_id = getattr(item, "lecturer_id", None)
    if not (request.user.role in ("registrar", "admin") or owner_id == request.user.pk):
        raise PermissionDenied("Only the teacher in charge can open this register.")
    rows = list(item.enrollments.select_related("student__user")
                .order_by("student__user__last_name", "student__user__first_name"))
    dates, grid = Attendance.register(rows)
    reasons = {
        (e_id, d): reason
        for e_id, d, reason in Attendance.objects.filter(
            enrollment_id__in=[e.pk for e in rows], date__in=dates
        ).values_list("enrollment_id", "date", "reason")
    }
    per_date = []
    for d in dates:
        statuses = [grid.get(e.pk, {}).get(d) for e in rows]
        attended = sum(1 for s in statuses if s in Attendance.ATTENDED)
        per_date.append({"date": d, "attended": attended, "total": len(rows)})
    for e in rows:
        cells = []
        for d in dates:
            status = grid.get(e.pk, {}).get(d)
            cells.append((Attendance.LETTERS.get(status, "–"),
                          reasons.get((e.pk, d), "")))
        e.cells = cells
        statuses = [grid.get(e.pk, {}).get(d) for d in dates]
        e.attended = sum(1 for s in statuses if s in Attendance.ATTENDED)
        e.missed = sum(1 for s in statuses if s in Attendance.MISSED)
    return render(request, "academics/attendance_register.html", {
        "item": item, "kind": kind, "rows": rows, "dates": dates,
        "per_date": per_date, "sessions": len(dates), "back": back})


@role_required("registrar", "bursar", "lecturer")
def student_report(request, pk):
    s = get_object_or_404(Student.objects.select_related("user", "programme",
                                                        "school_class", "stream"), pk=pk)
    if request.method == "POST":
        if request.user.role not in ("bursar", "admin"):
            raise PermissionDenied("Only the bursar can release withheld results.")
        if request.POST.get("action") == "clear":
            raw_term = request.POST.get("term", "")
            if not raw_term.isdigit():
                messages.error(request, "Choose a term first.")
                return redirect("student_report", pk=pk)
            term = get_object_or_404(Term, pk=raw_term)
            try:
                clear_result_hold(s, term, request.user,
                                  request.POST.get("reason", ""), request=request)
            except ResultsError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request, f"Results for {term} released despite the fee balance.")
        return redirect("student_report", pk=pk)
    return render(request, "academics/student_report.html", _report_context(s, request.user))


@role_required("student")
def my_report(request):
    s = get_object_or_404(Student.objects.select_related("user", "programme",
                                                        "school_class", "stream"),
                          user=request.user)
    return render(request, "academics/student_report.html", _report_context(s, request.user))


def _academic_document_response(request, student, kind):
    context = _report_context(student, request.user)
    document = issue_document(student, kind, request.user, request=request)
    verify_url = request.build_absolute_uri(
        reverse("verify_document", args=[document.token]))
    pdf = build_academic_document(student, context, kind, verify_url)
    label = ("transcript" if kind == DocumentVerification.Kind.TRANSCRIPT
             else "report-card")
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{label}-{student.student_no or student.pk}.pdf"')
    return response


@role_required("registrar", "bursar", "lecturer")
def student_report_pdf(request, pk):
    student = get_object_or_404(Student, pk=pk)
    return _academic_document_response(
        request, student, DocumentVerification.Kind.REPORT_CARD)


@role_required("registrar", "bursar", "lecturer")
def student_transcript_pdf(request, pk):
    student = get_object_or_404(Student, pk=pk)
    return _academic_document_response(
        request, student, DocumentVerification.Kind.TRANSCRIPT)


@role_required("student")
def my_report_pdf(request):
    student = get_object_or_404(Student, user=request.user)
    return _academic_document_response(
        request, student, DocumentVerification.Kind.REPORT_CARD)


@role_required("student")
def my_transcript_pdf(request):
    student = get_object_or_404(Student, user=request.user)
    return _academic_document_response(
        request, student, DocumentVerification.Kind.TRANSCRIPT)


def _guardian_student(request, pk):
    return get_object_or_404(Student, pk=pk,
                             guardian_links__parent=request.user)


@role_required("parent")
def child_report_pdf(request, pk):
    return _academic_document_response(
        request, _guardian_student(request, pk),
        DocumentVerification.Kind.REPORT_CARD)


@role_required("parent")
def child_transcript_pdf(request, pk):
    return _academic_document_response(
        request, _guardian_student(request, pk),
        DocumentVerification.Kind.TRANSCRIPT)


def verify_document(request, token):
    """Publicly confirm a printed report card or transcript by its token."""
    document = get_object_or_404(
        DocumentVerification.all_objects.select_related(
            "student__user", "institution"), token=token)
    return render(request, "academics/verify_document.html", {
        "document": document,
        "INSTITUTION": document.institution.name,
        "INSTITUTION_OBJECT": document.institution,
        "BRAND": brand_variables(document.institution),
    })


# ---------- exam sittings ----------
def _enrolled_this_term(term):
    ids = Enrollment.objects.filter(term=term).values_list("student_id", flat=True)
    return Student.objects.filter(pk__in=ids, status="active")


@module_required("exams")
@role_required("registrar", "bursar")
def exams(request):
    terms = list(Term.objects.select_related("academic_year"))
    term = None
    tid = request.GET.get("term", "")
    if tid.isdigit():
        term = next((t for t in terms if t.pk == int(tid)), None)
    if term is None:
        term = _current_term() or (terms[0] if terms else None)
    if term is None:
        return render(request, "academics/exams.html",
                      {"terms": terms, "term": None})

    if request.method == "POST":
        action = request.POST.get("action", "")
        enrolled = _enrolled_this_term(term)
        if action == "bulk":
            made = ExamSitting.enter(term, enrolled)
            messages.success(request,
                f"{made} student{'' if made == 1 else 's'} entered for {term}.")
        elif action == "enter_one":
            pk = request.POST.get("pk", "")
            if pk.isdigit() and enrolled.filter(pk=int(pk)).exists():
                ExamSitting.enter(term, enrolled.filter(pk=int(pk)))
                messages.success(request, "Student entered.")
            else:
                messages.error(request, "That student is not enrolled this term.")
        elif action.startswith("remove_"):
            pk = action.removeprefix("remove_")
            sitting = ExamSitting.objects.filter(pk=pk, term=term).first()
            if sitting:
                name = sitting.student.name
                sitting.delete()
                messages.success(request, f"{name} is no longer entered.")
        elif action == "save":
            sittings = ExamSitting.objects.filter(term=term)
            changed = 0
            for s in sittings:
                if f"venue_{s.pk}" not in request.POST and \
                        f"note_{s.pk}" not in request.POST and \
                        f"cleared_{s.pk}" not in request.POST:
                    continue
                venue = request.POST.get(f"venue_{s.pk}", "").strip()[:60]
                note = request.POST.get(f"note_{s.pk}", "").strip()[:120]
                cleared = f"cleared_{s.pk}" in request.POST
                if (s.venue, s.note, s.cleared) != (venue, note, cleared):
                    s.venue, s.note, s.cleared = venue, note, cleared
                    s.save(update_fields=["venue", "note", "cleared"])
                    changed += 1
            messages.success(request,
                "Sittings saved." if changed else "Nothing changed.")
        return redirect(f"{reverse('exams')}?term={term.pk}")

    sittings = ExamSitting.objects.filter(term=term).select_related(
        "student", "student__user", "student__programme",
        "student__school_class").order_by("exam_no")
    taken = {s.student_id for s in sittings}
    missing = _enrolled_this_term(term).exclude(pk__in=taken).select_related(
        "user", "programme")
    billed, paid, waived = balances_by_student()
    for s in sittings:
        s.balance = billed.get(s.student_id, Decimal("0")) - \
                    paid.get(s.student_id, Decimal("0")) - \
                    waived.get(s.student_id, Decimal("0"))
    return render(request, "academics/exams.html", {
        "terms": terms, "term": term, "sittings": sittings, "missing": missing})


@role_required("student")
def my_exam_card(request):
    s = get_object_or_404(Student.objects.select_related("user", "programme",
                                                         "school_class", "stream"),
                          user=request.user)
    sitting = _sitting_for(s)
    rows = (s.enrollments.filter(term=sitting.term)
            .select_related("course", "subject")
            .order_by("course__code", "subject__code")
            if sitting else Enrollment.objects.none())
    return render(request, "academics/exam_card.html",
                  {"s": s, "sitting": sitting, "exam_rows": rows})


# ---------- grade scale and awards ----------
@role_required()
def grade_bands(request):
    form = GradeBandForm()
    award_form = AwardRuleForm()
    if request.method == "POST":
        if request.POST.get("action") == "award":
            award_form = AwardRuleForm(request.POST)
            if award_form.is_valid():
                rule = award_form.save()
                messages.success(request, f"Award {rule.label} saved.")
                return redirect("grade_bands")
        else:
            form = GradeBandForm(request.POST)
            if form.is_valid():
                b = form.save()
                messages.success(request, f"Grade {b.letter} saved.")
                return redirect("grade_bands")
    return render(request, "academics/grade_bands.html", {
        "bands": GradeBand.objects.all(), "form": form,
        "awards": AwardRule.objects.all(), "award_form": award_form})


@role_required()
def grade_band_delete(request, pk):
    b = get_object_or_404(GradeBand, pk=pk)
    if request.method == "POST":
        b.delete()
        messages.success(request, f"Grade {b.letter} removed.")
    return redirect("grade_bands")


@role_required()
def award_rule_delete(request, pk):
    rule = get_object_or_404(AwardRule, pk=pk)
    if request.method == "POST":
        label = rule.label
        rule.delete()
        messages.success(request, f"Award {label} removed.")
    return redirect("grade_bands")


# ---------- parent portal ----------
@role_required("parent")
def parent_portal(request):
    links = list(
        Guardian.objects.filter(parent=request.user).select_related(
            "student", "student__user", "student__programme", "student__school_class",
            "student__stream"))
    can_view_finance = user_has_code(request.user, "parent_portal.view_finance")
    for link in links:
        link.show_finance = bool(link.can_view_finance and can_view_finance)
        link.balance = link.student.balance if link.show_finance else None
    return render(request, "academics/parent_portal.html", {
        "links": links,
        "can_view_finance": can_view_finance,
        "events": CalendarEvent.upcoming(request.user, limit=6),
        "announcements": Announcement.visible_to(request.user)[:5]})


@role_required("parent")
def child_detail(request, pk):
    s = get_object_or_404(
        Student.objects.select_related("user", "programme", "school_class", "stream"),
        pk=pk, guardian_links__parent=request.user)
    link = get_object_or_404(Guardian, parent=request.user, student=s)
    can_view_finance = bool(
        link.can_view_finance
        and user_has_code(request.user, "parent_portal.view_finance"))
    siblings = Guardian.objects.filter(parent=request.user).exclude(
        student=s).select_related("student")
    sitting = _sitting_for(s)
    exam_rows = (s.enrollments.filter(term=sitting.term)
                 .select_related("course", "subject")
                 .order_by("course__code", "subject__code")
                 if sitting else Enrollment.objects.none())
    enrollments = list(s.enrollments.select_related("course", "subject"))
    _annotate_result_state(enrollments, s, request.user)
    return render(request, "academics/child_detail.html", {
        "s": s, "enrollments": enrollments,
        "attendance": _attendance_rows(s.enrollments.all()),
        "absences": Attendance.absences(s.enrollments.all(), limit=8),
        "timetable": TimetableEntry.for_student(s),
        "activities": s.activities.all()[:10],
        "invoices": s.invoices.all() if can_view_finance else [],
        "sitting": sitting, "exam_rows": exam_rows,
        "can_view_finance": can_view_finance,
        "finance_on": is_enabled("finance", getattr(request, "institution", None)),
        "siblings": siblings,
        "announcements": Announcement.visible_to(request.user)[:5]})


@role_required("registrar")
def link_guardian(request, pk):
    s = get_object_or_404(Student, pk=pk)
    if request.method == "POST":
        f = GuardianForm(request.POST)
        if f.is_valid():
            existing = User.objects.filter(username=f.cleaned_data["username"]).first()
            if existing and Guardian.objects.filter(parent=existing, student=s).exists():
                messages.error(request, "That parent is already linked to this student.")
            else:
                g = f.save(s)
                name = g.parent.get_full_name() or g.parent.username
                messages.success(request, f"{name} can now follow {s.name}.")
        else:
            messages.error(request, f"Could not link the parent. {_err(f)}")
    return redirect("student_detail", pk=pk)


@role_required("registrar")
def unlink_guardian(request, pk, link_pk):
    s = get_object_or_404(Student, pk=pk)
    g = get_object_or_404(Guardian, pk=link_pk, student=s)
    if request.method == "POST":
        name = g.parent.get_full_name() or g.parent.username
        g.delete()
        messages.success(request, f"{name} is no longer linked to {s.name}.")
    return redirect("student_detail", pk=pk)


# ---------- student portal ----------
@role_required("student")
def student_portal(request):
    s = get_object_or_404(Student.objects.select_related("school_class", "stream"), user=request.user)
    enrollments = list(s.enrollments.select_related("course", "subject"))
    _annotate_result_state(enrollments, s, request.user)
    assignments = list(
        s.activities.filter(kind=Activity.Kind.ASSIGN).select_related(
            "course", "subject", "created_by").order_by("due_date", "pk"))
    assignment_rows = []
    for activity in assignments:
        assignment_rows.append({
            "activity": activity,
            "submission": activity.submissions.filter(student=s).first(),
        })
    return render(request, "academics/student_portal.html", {
        "s": s, "enrollments": enrollments,
        "attendance": _attendance_rows(s.enrollments.all()),
        "absences": Attendance.absences(s.enrollments.all(), limit=8),
        "activities": s.activities.all()[:10], "invoices": s.invoices.prefetch_related("payments"),
        "timetable": TimetableEntry.for_student(s),
        "announcements": Announcement.visible_to(request.user)[:5],
        "events": CalendarEvent.upcoming(request.user, limit=6),
        "finance_on": is_enabled("finance", getattr(request, "institution", None)),
        "assignments": assignments,
        "assignment_rows": assignment_rows})


@role_required("student")
def my_assignments(request):
    s = get_object_or_404(Student.objects.select_related("school_class", "stream", "programme"), user=request.user)
    assignments = list(
        s.activities.filter(kind=Activity.Kind.ASSIGN).select_related(
            "course", "subject", "created_by").order_by("due_date", "pk"))
    rows = []
    for activity in assignments:
        rows.append({
            "activity": activity,
            "submission": activity.submissions.filter(student=s).first(),
        })
    return render(request, "academics/my_assignments.html", {
        "s": s, "assignment_rows": rows})


@role_required("student")
def assignment_submit(request, pk):
    s = get_object_or_404(Student.objects.select_related("school_class", "stream", "programme"), user=request.user)
    activity = get_object_or_404(Activity, pk=pk, student=s)
    if activity.kind != Activity.Kind.ASSIGN:
        raise Http404("That is not an assignment.")
    if request.method == "POST":
        form = AssignmentSubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            submission = activity.submissions.filter(student=s).first()
            if submission is None:
                submission = AssignmentSubmission(
                    institution=s.institution, assignment=activity, student=s)
            submission.text = form.cleaned_data.get("text") or submission.text
            submission.file = form.cleaned_data.get("file") or submission.file
            submission.submitted_at = timezone.now()
            submission.save()
            messages.success(request, "Assignment submitted.")
            return redirect("my_assignments")
        messages.error(request, _err(form))
        return redirect("my_assignments")
    form = AssignmentSubmissionForm()
    return render(request, "academics/assignment_submit.html", {
        "s": s, "activity": activity, "form": form})
