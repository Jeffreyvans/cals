"""Admissions and lifecycle services.

The rules live here, not in the views: eligibility, offers, the atomic
acceptance that creates a student, lifecycle transitions, transfers, graduation
clearance and certificates. Every write that changes a student's life writes an
audit entry as well.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from academics.models import Student, Term
from accounts.models import User
from core.audit import log_action
from core.notifications import notify, queue_message, send_message
from core.tenancy import use_institution
from finance.models import Invoice, Payment
from finance.services import receive_payment

from .models import (
    AlumniRecord,
    ApplicantResult,
    Application,
    Certificate,
    EntryRequirement,
    GraduationClearance,
    LifecycleState,
    LifecycleTransition,
    Offer,
    StudentCard,
    TransferRecord,
)

GRADE_ORDER = ["A*", "A", "B", "C", "D", "E", "F", "U"]
DEFAULT_GRADE_MARKS = [(75, "A"), (65, "B"), (50, "C"), (40, "D"), (30, "E"), (0, "F")]

ALLOWED_TRANSITIONS = {
    LifecycleState.ENROLLED: {
        LifecycleState.DEFERRED,
        LifecycleState.SUSPENDED,
        LifecycleState.TRANSFERRED_OUT,
        LifecycleState.WITHDRAWN,
    },
    LifecycleState.DEFERRED: {
        LifecycleState.ENROLLED,
        LifecycleState.TRANSFERRED_OUT,
        LifecycleState.WITHDRAWN,
    },
    LifecycleState.SUSPENDED: {
        LifecycleState.ENROLLED,
        LifecycleState.TRANSFERRED_OUT,
        LifecycleState.WITHDRAWN,
    },
    LifecycleState.TRANSFERRED_OUT: {
        LifecycleState.ENROLLED,
    },
    LifecycleState.WITHDRAWN: {
        LifecycleState.ENROLLED,
    },
    LifecycleState.GRADUATED: {
        LifecycleState.ALUMNI,
    },
    LifecycleState.ALUMNI: set(),
}

APPLICATION_RATE_LIMIT = 5
APPLICATION_RATE_WINDOW = 60 * 60


class AdmissionError(ValidationError):
    """A rule stopped an admissions or lifecycle action."""


def _current_state(student):
    """The lifecycle name for a student's stored status."""
    if student.status in (Student.Status.ACTIVE, Student.Status.ENROLLED):
        return LifecycleState.ENROLLED
    if student.status == Student.Status.TRANSFERRED_OUT:
        return LifecycleState.TRANSFERRED_OUT
    if student.status == Student.Status.GRADUATED:
        return LifecycleState.GRADUATED
    if student.status == Student.Status.ALUMNI:
        return LifecycleState.ALUMNI
    if student.status == Student.Status.DEFERRED:
        return LifecycleState.DEFERRED
    if student.status == Student.Status.SUSPENDED:
        return LifecycleState.SUSPENDED
    if student.status == Student.Status.WITHDRAWN:
        return LifecycleState.WITHDRAWN
    return LifecycleState.ENROLLED


def _state_to_status(state):
    return {
        LifecycleState.ENROLLED: Student.Status.ENROLLED,
        LifecycleState.DEFERRED: Student.Status.DEFERRED,
        LifecycleState.SUSPENDED: Student.Status.SUSPENDED,
        LifecycleState.TRANSFERRED_OUT: Student.Status.TRANSFERRED_OUT,
        LifecycleState.WITHDRAWN: Student.Status.WITHDRAWN,
        LifecycleState.GRADUATED: Student.Status.GRADUATED,
        LifecycleState.ALUMNI: Student.Status.ALUMNI,
    }[state]


# ---------- eligibility ----------


def _grade_rank(grade):
    letter = (grade or "").strip().upper()
    if letter in GRADE_ORDER:
        return GRADE_ORDER.index(letter)
    return len(GRADE_ORDER)


def _grade_for_mark(mark):
    if mark is None:
        return ""
    for minimum, letter in DEFAULT_GRADE_MARKS:
        if mark >= minimum:
            return letter
    return "F"


def _result_grade(result):
    if result.grade:
        return result.grade.strip().upper()
    return _grade_for_mark(result.mark)


def parse_results_text(text):
    """Read lines such as ``Mathematics: B`` or ``English 71`` into results."""
    parsed = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if ":" in line:
            subject, value = line.split(":", 1)
        else:
            parts = line.rsplit(None, 1)
            if len(parts) != 2:
                continue
            subject, value = parts
        value = value.strip()
        if not subject.strip():
            continue
        match = re.fullmatch(r"\d+(?:\.\d+)?", value)
        parsed.append({
            "subject": subject.strip()[:120],
            "grade": "" if match else value[:5].upper(),
            "mark": Decimal(value) if match else None,
        })
    return parsed


def _results_for(application):
    """The structured results, falling back to the applicant's text."""
    results = list(application.results.all())
    if results:
        return results
    return [ApplicantResult(**row) for row in parse_results_text(application.previous_results)]


def check_eligibility(application, requirement=None):
    """Check an applicant against a programme's entry requirement.

    Writes ``eligible`` and ``eligibility_note`` on the application and returns
    ``(eligible, note)``. With no requirement the result stays ``None`` so the
    reviewer knows nothing automatic was applied.
    """
    requirement = requirement or EntryRequirement.objects.filter(
        programme=application.programme).first()
    if requirement is None:
        application.eligible = None
        application.eligibility_note = "No entry requirement has been set for this programme."
        application.save(update_fields=["eligible", "eligibility_note"])
        return None, application.eligibility_note

    results = _results_for(application)
    if not results:
        application.eligible = False
        application.eligibility_note = "No results were supplied."
        application.save(update_fields=["eligible", "eligibility_note"])
        return False, application.eligibility_note

    minimum_rank = _grade_rank(requirement.minimum_grade)
    passes = []
    for result in results:
        grade = _result_grade(result)
        if _grade_rank(grade) <= minimum_rank:
            passes.append(result)

    failed = []
    if requirement.minimum_subjects and len(passes) < requirement.minimum_subjects:
        failed.append(f"{len(passes)} pass(es); {requirement.minimum_subjects} required")
    passed_subjects = {r.subject.strip().casefold() for r in passes}
    missing = [name for name in requirement.required_subject_names
               if name.casefold() not in passed_subjects]
    if missing:
        failed.append("missing " + ", ".join(missing))
    numeric = [r.mark for r in results if r.mark is not None]
    if requirement.minimum_average is not None and numeric:
        average = sum(numeric, Decimal("0")) / len(numeric)
        if average < requirement.minimum_average:
            failed.append(
                f"average {average:.2f}; {requirement.minimum_average:g} required")

    eligible = not failed
    if eligible:
        note = (f"Meets the requirement: {len(passes)} pass(es) at grade "
                f"{requirement.minimum_grade} or better.")
    else:
        note = "Does not meet the entry requirement: " + "; ".join(failed) + "."
    application.eligible = eligible
    application.eligibility_note = note
    application.save(update_fields=["eligible", "eligibility_note"])
    return eligible, note


# ---------- review workflow ----------


def review_application(application, status, actor=None, note="", request=None):
    """Move an application through review, with an audit entry."""
    allowed = {
        Application.Status.UNDER_REVIEW,
        Application.Status.ACCEPTED,
        Application.Status.WAITLISTED,
        Application.Status.REJECTED,
        Application.Status.WITHDRAWN,
    }
    if status not in allowed:
        raise AdmissionError("That is not a valid review decision.")
    if application.status in (Application.Status.ENROLLED,):
        raise AdmissionError("This applicant is already enrolled.")
    before = application.status
    application.status = status
    application.decision_note = note
    application.reviewed_by = actor if getattr(actor, "is_authenticated", False) else None
    application.reviewed_at = timezone.now()
    application.save(update_fields=["status", "decision_note", "reviewed_by", "reviewed_at"])
    log_action(actor, "admissions.application_reviewed", application, request=request,
               before={"status": before}, after={"status": status, "note": note})
    return application


def schedule_interview(application, when, location="", note="", actor=None, request=None):
    if application.status in (Application.Status.ENROLLED, Application.Status.REJECTED):
        raise AdmissionError("This application can no longer be scheduled.")
    if when is None:
        raise AdmissionError("Choose the interview or test date and time.")
    application.interview_at = when
    application.interview_location = location
    application.interview_note = note
    application.status = Application.Status.INTERVIEW
    application.save(update_fields=["interview_at", "interview_location",
                                    "interview_note", "status"])
    log_action(actor, "admissions.interview_scheduled", application, request=request,
               after={"when": when.isoformat(), "location": location})
    return application


def make_offer(application, acceptance_deadline, deposit_amount=Decimal("0"),
               actor=None, request=None):
    """Issue an offer letter and mark the application as accepted."""
    if application.status in (Application.Status.REJECTED, Application.Status.WITHDRAWN,
                              Application.Status.ENROLLED):
        raise AdmissionError("This application cannot receive an offer.")
    if hasattr(application, "offer"):
        raise AdmissionError("This application already has an offer.")
    if acceptance_deadline is None or acceptance_deadline < timezone.localdate():
        raise AdmissionError("The acceptance deadline must be today or later.")
    if application.programme_id is None:
        raise AdmissionError("Choose the programme before making an offer.")
    offer = Offer.objects.create(
        application=application,
        acceptance_deadline=acceptance_deadline,
        deposit_amount=deposit_amount or Decimal("0"),
        issued_by=actor if getattr(actor, "is_authenticated", False) else None)
    application.status = Application.Status.ACCEPTED
    application.reviewed_by = actor if getattr(actor, "is_authenticated", False) else None
    application.reviewed_at = timezone.now()
    application.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    log_action(actor, "admissions.offer_issued", offer, request=request,
               after={"deadline": acceptance_deadline.isoformat(),
                      "deposit": str(offer.deposit_amount)})
    return offer


# ---------- acceptance ----------


def _unique_username(application):
    base = (application.email.split("@")[0] or
            f"{application.first_name}.{application.last_name}")
    base = re.sub(r"[^a-zA-Z0-9._-]", "", base).lower() or "applicant"
    candidate = base[:30]
    suffix = 0
    while User.objects.filter(username=candidate).exists():
        suffix += 1
        candidate = f"{base[:25]}{suffix}"
    return candidate


@transaction.atomic
def create_student_record(
    institution,
    *,
    first_name,
    last_name,
    email="",
    phone="",
    username="",
    programme,
    school_class=None,
    stream=None,
    programme_year=None,
    cohort=None,
    year_or_grade="1",
    date_of_birth=None,
    guardian_name="",
    guardian_phone="",
    student_no="",
    access_mode="temporary_password",
    actor=None,
    request=None,
    first_invoice=False,
    fee_structure=None,
    due_date=None,
    status=Student.Status.ACTIVE,
):
    """Create the login and student record in one place.

    Admissions uses this when an offer is accepted, and an administrator uses
    it when adding a student directly, so there is one code path for student
    numbers, logins and the optional first invoice.
    """
    actor_institution = getattr(actor, "institution_id", None)
    if actor_institution is not None and actor_institution != institution.pk:
        raise ValidationError("You cannot add a student to another institution.")
    from accounts.user_admin import provision_user
    from core.models import Role
    from core.roles import ensure_roles

    due_date = due_date or timezone.localdate()
    with use_institution(institution):
        ensure_roles(institution)
        student_role = Role.all_objects.filter(
            institution=institution, code="student"
        ).first()
        account = provision_user(
            institution,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            username=username,
            role=student_role,
            access_mode=access_mode,
            actor=actor,
            request=request,
        )
        student = Student(
            institution=institution,
            user=account.user,
            student_no=str(student_no or "").strip()[:20],
            programme=programme,
            programme_year=programme_year,
            cohort=cohort,
            school_class=school_class,
            stream=stream,
            year_or_grade=str(year_or_grade or "1")[:20],
            date_of_birth=date_of_birth,
            guardian_name=str(guardian_name or "").strip()[:120],
            guardian_phone=str(guardian_phone or "").strip()[:30],
            status=status,
        )
        student.save()

        invoice = None
        if first_invoice:
            structure = fee_structure
            if structure is None:
                from finance.models import FeeStructure

                structures = FeeStructure.all_objects.filter(
                    institution=institution, is_active=True
                )
                structure = (
                    structures.filter(programme=programme).first()
                    or (structures.filter(school_class=school_class).first()
                        if school_class is not None else None)
                )
            amount = Decimal("0")
            description = "First invoice"
            term = None
            if structure is not None:
                amount = structure.total
                description = f"First invoice - {structure.name}"[:200]
                term = structure.term
            elif programme is not None and (programme.tuition_fee or Decimal("0")) > 0:
                amount = programme.tuition_fee
                description = f"Tuition - {programme.name}"[:200]
            if amount > 0:
                if term is None:
                    term = Term.all_objects.filter(
                        institution=institution, is_current=True
                    ).first()
                invoice = Invoice.objects.create(
                    institution=institution,
                    student=student,
                    description=description,
                    amount=amount,
                    due_date=due_date,
                    term=term,
                    structure=structure,
                    created_by=actor if getattr(actor, "is_authenticated", False) else None,
                )

        log_action(
            actor,
            "students.created",
            instance=student,
            institution=institution,
            request=request,
            after={
                "student_no": student.student_no,
                "programme": getattr(programme, "code", ""),
                "access_mode": account.access_mode,
            },
        )
        return {"student": student, "account": account, "invoice": invoice}


def _welcome_body(student, username, password, request=None):
    line = f"Student number: {student.student_no}\nUsername: {username}\nTemporary password: {password}"
    if request is not None:
        portal = request.build_absolute_uri("/me/")
    else:
        portal = "/me/"
    return (
        f"Welcome to {student.institution.name}, {student.name}.\n\n"
        f"Your place has been confirmed.\n{line}\n\n"
        f"Sign in at {portal} and change your password.\n"
        f"Programme: {student.programme.name}\n"
    )


def accept_offer(offer, accepted_by_name="", deposit_method="", deposit_reference="",
                 actor=None, request=None):
    """Accept an offer and create the student in one transaction.

    The login, student number, first invoice, deposit payment, welcome message
    and audit entries are written together. If any step fails, none of them are
    kept.
    """
    application = offer.application
    with use_institution(application.institution):
        expired = False
        with transaction.atomic():
            offer = Offer.all_objects.select_for_update().select_related(
                "application__programme", "application__programme_year",
                "application__cohort", "application__school_class").get(pk=offer.pk)
            application = offer.application
            if offer.status == Offer.Status.ACCEPTED and offer.student_id:
                return offer.student
            if offer.status != Offer.Status.PENDING:
                raise AdmissionError("This offer is no longer waiting for acceptance.")
            if offer.acceptance_deadline < timezone.localdate():
                offer.status = Offer.Status.EXPIRED
                offer.save(update_fields=["status"])
                expired = True
            else:
                if application.status == Application.Status.ENROLLED:
                    raise AdmissionError("This applicant is already enrolled.")
                if application.programme_id is None:
                    raise AdmissionError("The application has no programme.")

                deposit = offer.deposit_amount or Decimal("0")
                if deposit > 0 and deposit_method not in (
                        Offer.DepositMethod.RECORDED, Offer.DepositMethod.ONLINE):
                    raise AdmissionError("Choose how the deposit is being paid.")

                created = create_student_record(
                    application.institution,
                    first_name=application.first_name,
                    last_name=application.last_name,
                    email=application.email,
                    phone=application.phone,
                    username=_unique_username(application),
                    programme=application.programme,
                    programme_year=application.programme_year,
                    cohort=application.cohort,
                    school_class=application.school_class,
                    year_or_grade=application.year_or_grade or "1",
                    date_of_birth=application.date_of_birth,
                    guardian_name=application.guardian_name,
                    guardian_phone=application.guardian_phone,
                    access_mode="temporary_password",
                    actor=actor,
                    request=request,
                    status=Student.Status.ENROLLED,
                )
                student = created["student"]
                user = created["account"].user
                temp_password = created["account"].password
                username = user.username

                term = Term.all_objects.filter(
                    institution=application.institution, is_current=True).first()
                amount = application.programme.tuition_fee or Decimal("0")
                if amount <= 0 and deposit > 0:
                    amount = deposit
                invoice = Invoice.objects.create(
                    institution=application.institution,
                    student=student,
                    description=f"First invoice ? {application.programme.name}"[:200],
                    amount=amount,
                    due_date=offer.acceptance_deadline,
                    term=term,
                    created_by=actor if getattr(actor, "is_authenticated", False) else None,
                )
                if deposit > 0:
                    method = (Payment.Method.CARD
                              if deposit_method == Offer.DepositMethod.ONLINE
                              else Payment.Method.CASH)
                    receive_payment(
                        student, invoice, deposit, method=method,
                        reference=deposit_reference, payer_name=accepted_by_name,
                        received_by=actor if getattr(actor, "is_authenticated", False) else None,
                        currency=application.institution.currency)

                offer.status = Offer.Status.ACCEPTED
                offer.accepted_by_name = accepted_by_name[:120]
                offer.accepted_at = timezone.now()
                offer.deposit_method = deposit_method
                offer.deposit_reference = deposit_reference[:80]
                offer.deposit_paid_at = timezone.now() if deposit > 0 else None
                offer.student = student
                offer.save(update_fields=["status", "accepted_by_name", "accepted_at",
                                          "deposit_method", "deposit_reference",
                                          "deposit_paid_at", "student"])

                application.status = Application.Status.ENROLLED
                application.save(update_fields=["status"])

                LifecycleTransition.objects.create(
                    institution=application.institution, student=student,
                    from_state=LifecycleState.APPLICANT, to_state=LifecycleState.ENROLLED,
                    reason=f"Offer {offer.offer_no} accepted",
                    effective_date=timezone.localdate())
                log_action(actor, "admissions.offer_accepted", offer, request=request,
                           institution=application.institution,
                           after={"student": student.pk, "student_no": student.student_no})

                body = _welcome_body(student, username, temp_password, request)
                message = queue_message(
                    "email", application.email, body,
                    subject=f"Welcome to {application.institution.name}",
                    institution=application.institution, related_object=student)
                send_message(message)
                notify(user, "Welcome to " + application.institution.name,
                       "Your place has been confirmed. Sign in to see your account.",
                       link="/me/", category="admissions",
                       institution=application.institution)
                student.temporary_password = temp_password
                student.temporary_username = username
                return student
        if expired:
            raise AdmissionError("The acceptance deadline has passed.")


# ---------- lifecycle ----------


@transaction.atomic
def transition_student(student, to_state, reason, actor=None, effective_date=None,
                       request=None):
    """Move a student to a new lifecycle state, with a required reason."""
    reason = (reason or "").strip()
    if not reason:
        raise AdmissionError("Give a reason for this change.")
    with use_institution(student.institution):
        student = Student.all_objects.select_for_update().get(pk=student.pk)
        from_state = _current_state(student)
        if to_state == LifecycleState.GRADUATED:
            raise AdmissionError("Use the graduation service so the clearance checklist is met.")
        allowed = ALLOWED_TRANSITIONS.get(from_state, set())
        if to_state not in allowed:
            raise AdmissionError(
                f"A student who is {from_state.replace('_', ' ')} cannot become "
                f"{to_state.replace('_', ' ')}.")
        effective_date = effective_date or timezone.localdate()
        student.status = _state_to_status(to_state)
        student.save(update_fields=["status"])
        transition = LifecycleTransition.objects.create(
            institution=student.institution, student=student,
            from_state=from_state, to_state=to_state, reason=reason,
            effective_date=effective_date,
            actor=actor if getattr(actor, "is_authenticated", False) else None)
        log_action(actor, f"student.lifecycle.{to_state}", student, request=request,
                   institution=student.institution,
                   before={"state": from_state},
                   after={"state": to_state, "reason": reason})
        return transition


@transaction.atomic
def transfer_out(student, other_institution, reference="", reason="", actor=None,
                 effective_date=None, request=None):
    reason = (reason or "").strip()
    if not reason:
        raise AdmissionError("Give a reason for the transfer.")
    if not (other_institution or "").strip():
        raise AdmissionError("Name the institution the student is moving to.")
    with use_institution(student.institution):
        record = TransferRecord.objects.create(
            institution=student.institution, student=student,
            direction=TransferRecord.Direction.OUT,
            other_institution=other_institution[:200], reference=reference[:80],
            reason=reason, effective_date=effective_date or timezone.localdate(),
            created_by=actor if getattr(actor, "is_authenticated", False) else None)
        transition_student(student, LifecycleState.TRANSFERRED_OUT,
                           f"Transferred out to {other_institution}: {reason}",
                           actor=actor, effective_date=effective_date, request=request)
        log_action(actor, "students.transferred_out", record, request=request,
                   institution=student.institution,
                   after={"institution": other_institution, "reference": reference})
        return record


@transaction.atomic
def transfer_in(student, other_institution, reference="", reason="", actor=None,
                effective_date=None, request=None):
    reason = (reason or "").strip()
    if not reason:
        raise AdmissionError("Give a reason for the transfer.")
    if not (other_institution or "").strip():
        raise AdmissionError("Name the institution the student is coming from.")
    with use_institution(student.institution):
        record = TransferRecord.objects.create(
            institution=student.institution, student=student,
            direction=TransferRecord.Direction.IN,
            other_institution=other_institution[:200], reference=reference[:80],
            reason=reason, effective_date=effective_date or timezone.localdate(),
            created_by=actor if getattr(actor, "is_authenticated", False) else None)
        if _current_state(student) != LifecycleState.ENROLLED:
            transition_student(student, LifecycleState.ENROLLED,
                               f"Transfer in from {other_institution}: {reason}",
                               actor=actor, effective_date=effective_date,
                               request=request)
        log_action(actor, "students.transferred_in", record, request=request,
                   institution=student.institution,
                   after={"institution": other_institution, "reference": reference})
        return record


# ---------- graduation, certificates and cards ----------


def clearance_for(student):
    """The student's clearance row, created empty on first use."""
    clearance, _created = GraduationClearance.objects.get_or_create(
        institution=student.institution, student=student)
    return clearance


@transaction.atomic
def update_clearance(student, data, actor=None, request=None):
    """Update the checklist. Fees are checked against the real balance."""
    clearance = clearance_for(student)
    for key in ("fees_cleared", "library_books_returned",
                "hostel_keys_returned", "exams_passed"):
        if key in data:
            setattr(clearance, key, bool(data.get(key)))
    if data.get("refresh_fees"):
        from finance.services import student_balance

        clearance.fees_cleared = student_balance(student) <= 0
    clearance.notes = data.get("notes", clearance.notes)
    clearance.updated_by = actor if getattr(actor, "is_authenticated", False) else None
    if clearance.is_clear:
        clearance.cleared_by = actor if getattr(actor, "is_authenticated", False) else None
        clearance.cleared_at = clearance.cleared_at or timezone.now()
    else:
        clearance.cleared_by = None
        clearance.cleared_at = None
    clearance.save()
    log_action(actor, "students.clearance_updated", clearance, request=request,
               institution=student.institution,
               after={key: value for key, _label, value in clearance.items})
    return clearance


@transaction.atomic
def graduate_student(student, actor=None, request=None):
    """Graduate a student only once every clearance item is clear."""
    with use_institution(student.institution):
        student = Student.all_objects.select_for_update().get(pk=student.pk)
        clearance = GraduationClearance.objects.filter(
            institution=student.institution, student=student).first()
        if clearance is None or not clearance.is_clear:
            outstanding = clearance.outstanding if clearance else [
                "fees cleared", "library books returned",
                "hostel keys returned", "examinations passed"]
            raise AdmissionError(
                "The student is not clear to graduate. Outstanding: "
                + ", ".join(outstanding) + ".")
        if _current_state(student) != LifecycleState.GRADUATED:
            from_state = _current_state(student)
            student.status = Student.Status.GRADUATED
            student.save(update_fields=["status"])
            LifecycleTransition.objects.create(
                institution=student.institution, student=student,
                from_state=from_state, to_state=LifecycleState.GRADUATED,
                reason="Graduation clearance completed",
                effective_date=timezone.localdate(),
                actor=actor if getattr(actor, "is_authenticated", False) else None)
        clearance.graduated_by = actor if getattr(actor, "is_authenticated", False) else None
        clearance.graduated_at = timezone.now()
        clearance.cleared_by = clearance.cleared_by or (
            actor if getattr(actor, "is_authenticated", False) else None)
        clearance.cleared_at = clearance.cleared_at or timezone.now()
        clearance.save(update_fields=["graduated_by", "graduated_at",
                                      "cleared_by", "cleared_at"])
        AlumniRecord.objects.get_or_create(
            institution=student.institution, student=student,
            defaults={
                "contact_email": student.user.email,
                "contact_phone": student.guardian_phone or student.user.phone,
                "graduation_year": timezone.localdate().year,
                "updated_by": actor if getattr(actor, "is_authenticated", False) else None,
            })
        log_action(actor, "students.graduated", student, request=request,
                   institution=student.institution,
                   after={"graduation_year": timezone.localdate().year})
        return clearance


@transaction.atomic
def issue_certificate(student, award, title="", kind=Certificate.Kind.ACADEMIC,
                      graduation_date=None, actor=None, request=None):
    """Issue a numbered certificate with a public verification token."""
    with use_institution(student.institution):
        student = Student.all_objects.get(pk=student.pk)
        if student.status not in (Student.Status.GRADUATED, Student.Status.ALUMNI):
            raise AdmissionError("Only a graduated student can receive a certificate.")
        certificate = Certificate.objects.create(
            institution=student.institution, student=student,
            kind=kind, title=title or "Certificate of Award", award=award,
            graduation_date=graduation_date or timezone.localdate(),
            issued_by=actor if getattr(actor, "is_authenticated", False) else None)
        log_action(actor, "students.certificate_issued", certificate, request=request,
                   institution=student.institution,
                   after={"serial": certificate.serial_no, "award": award})
        return certificate


@transaction.atomic
def issue_id_card(student, valid_until, photo=None, actor=None, request=None):
    """Create or update the printable identity card of a student."""
    with use_institution(student.institution):
        card = StudentCard.objects.filter(
            institution=student.institution, student=student).first()
        if card is None:
            card = StudentCard(
                institution=student.institution, student=student,
                valid_until=valid_until, photo=photo or student.photo)
        else:
            card.valid_until = valid_until
            if photo is not None:
                card.photo = photo
        card.is_active = True
        card.save()
        log_action(actor, "students.id_card_issued", card, request=request,
                   institution=student.institution,
                   after={"valid_until": str(valid_until)})
        return card


# ---------- public form rate limiting ----------


def application_rate_key(request, institution):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")
    return f"edusphere.application.{institution.pk}.{ip}"


def application_rate_limited(request, institution, limit=APPLICATION_RATE_LIMIT,
                             window=APPLICATION_RATE_WINDOW):
    """True when this address has already sent too many applications."""
    return cache.get(application_rate_key(request, institution), 0) >= limit


def record_application_attempt(request, institution, window=APPLICATION_RATE_WINDOW):
    key = application_rate_key(request, institution)
    cache.set(key, cache.get(key, 0) + 1, window)


def safe_decimal(raw, default=Decimal("0")):
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return default


def today():
    return date.today()
