"""Public registration rules for students and staff.

The public forms are deliberately careful:

* a student is matched against the record created at admission, and the same
  generic message is shown whatever part of the match failed;
* a staff account is created inactive and only an administrator can approve it,
  so a visitor can never choose the administrator role;
* every attempt that fails is written to the audit trail;
* throttling is applied per address and per registration number.

All writes happen inside ``use_institution(institution)`` so a registration
form can never reach another institution's rows.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.audit import log_action
from core.models import Role
from core.notifications import notify, queue_message, render_email_html, send_message
from core.roles import ensure_roles
from core.tenancy import use_institution

from .models import EmailVerification, StaffRegistrationRequest, User

STUDENT_GENERIC_ERROR = (
    "We could not match these details to a record at this institution. "
    "Check your registration number and details, or contact the school's "
    "registrar to confirm your admission."
)

STAFF_GENERIC_PENDING = (
    "Your request has been sent. The institution's administrators will review "
    "it and email you when it is approved or if they need more information."
)

STUDENT_RATE_LIMIT = 5
STUDENT_RATE_WINDOW = 15 * 60
CONTACT_RATE_LIMIT = 5
CONTACT_RATE_WINDOW = 60 * 60

CONTACT_EMAIL = "vanhuvaonejeffrey@gmail.com"
CONTACT_PHONE = "+263774573765"
CONTACT_WHATSAPP = "https://wa.me/263774573765"


@dataclass(frozen=True)
class StaffPosition:
    code: str
    label: str
    role_code: str
    legacy_role: str


# Positions a visitor may ask for. Administrator and principal are deliberately
# absent: a public form must never create a leadership account.
STAFF_POSITIONS = {
    "lecturer": StaffPosition("lecturer", "Lecturer", "lecturer", User.Role.LECTURER),
    "teacher": StaffPosition("teacher", "Teacher", "class_teacher", User.Role.TEACHER),
    "registrar": StaffPosition("registrar", "Registrar", "registrar", User.Role.REGISTRAR),
    "bursar": StaffPosition("bursar", "Bursar", "bursar", User.Role.BURSAR),
    "accounts_clerk": StaffPosition(
        "accounts_clerk", "Accounts clerk", "accounts_clerk", User.Role.ACCOUNTANT
    ),
    "librarian": StaffPosition("librarian", "Librarian", "librarian", User.Role.STAFF),
    "hr_officer": StaffPosition("hr_officer", "HR officer", "hr_officer", User.Role.STAFF),
    "exams_officer": StaffPosition(
        "exams_officer", "Exams officer", "exams_officer", User.Role.STAFF
    ),
    "nurse": StaffPosition("nurse", "Nurse / health officer", "nurse", User.Role.STAFF),
    "counsellor": StaffPosition(
        "counsellor", "Counsellor", "counsellor", User.Role.STAFF
    ),
    "warden": StaffPosition("warden", "Warden / matron", "warden", User.Role.STAFF),
    "transport_officer": StaffPosition(
        "transport_officer", "Transport officer", "transport_officer", User.Role.STAFF
    ),
    "payroll_officer": StaffPosition(
        "payroll_officer", "Payroll officer", "payroll_officer", User.Role.STAFF
    ),
    "procurement_officer": StaffPosition(
        "procurement_officer", "Procurement officer", "procurement_officer", User.Role.STAFF
    ),
    "security_officer": StaffPosition(
        "security_officer", "Security officer", "security_officer", User.Role.STAFF
    ),
    "sports_officer": StaffPosition(
        "sports_officer", "Sports officer", "sports_officer", User.Role.STAFF
    ),
    "student_affairs_officer": StaffPosition(
        "student_affairs_officer",
        "Student affairs officer",
        "student_affairs_officer",
        User.Role.STAFF,
    ),
    "catering_officer": StaffPosition(
        "catering_officer", "Catering officer", "catering_officer", User.Role.STAFF
    ),
    "careers_officer": StaffPosition(
        "careers_officer", "Careers officer", "careers_officer", User.Role.STAFF
    ),
    "research_officer": StaffPosition(
        "research_officer", "Research officer", "research_officer", User.Role.STAFF
    ),
    "lab_technician": StaffPosition(
        "lab_technician", "Laboratory technician", "lab_technician", User.Role.STAFF
    ),
    "ict_officer": StaffPosition("ict_officer", "ICT officer", "ict_officer", User.Role.STAFF),
    "marketing_officer": StaffPosition(
        "marketing_officer", "Marketing officer", "marketing_officer", User.Role.STAFF
    ),
    "estates_officer": StaffPosition(
        "estates_officer", "Estates officer", "estates_officer", User.Role.STAFF
    ),
    "compliance_officer": StaffPosition(
        "compliance_officer", "Compliance officer", "compliance_officer", User.Role.STAFF
    ),
    "governance_officer": StaffPosition(
        "governance_officer", "Governance officer", "governance_officer", User.Role.STAFF
    ),
    "auditor": StaffPosition("auditor", "Auditor", "auditor", User.Role.STAFF),
    "hod": StaffPosition("hod", "Head of department", "hod", User.Role.HOD),
    "dean": StaffPosition("dean", "Dean", "dean", User.Role.DEAN),
}

POSITION_ORDER = list(STAFF_POSITIONS)
DEFAULT_STAFF_POSITIONS = [
    "lecturer",
    "teacher",
    "registrar",
    "bursar",
    "accounts_clerk",
    "librarian",
    "hr_officer",
]
FORBIDDEN_POSITIONS = {"administrator", "admin", "principal", "institution_admin"}


def normalize_text(value):
    """Trim and collapse runs of spaces."""
    return " ".join(str(value or "").split()).strip()


def normalized_key(value):
    """Case-insensitive, space-insensitive comparison key."""
    return normalize_text(value).casefold()


def position_label(code):
    position = STAFF_POSITIONS.get(code)
    return position.label if position else normalize_text(code).replace("_", " ").title()


def position_options():
    """Every position a public form may offer."""
    return [
        {"code": code, "label": STAFF_POSITIONS[code].label}
        for code in POSITION_ORDER
    ]


def allowed_staff_positions(institution):
    """The positions this institution has switched on, in a stable order."""
    raw = getattr(institution, "staff_positions", None) or []
    if isinstance(raw, str):
        raw = [raw]
    chosen = {str(code) for code in raw}
    return [code for code in POSITION_ORDER if code in chosen]


def public_institutions():
    """Institutions that have switched public registration on."""
    from core.models import Institution

    return Institution.objects.filter(
        is_active=True, public_registration=True
    ).order_by("name")


def _client_ip(request):
    if request is None:
        return ""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _student_rate_keys(request, institution, registration_number):
    ip = _client_ip(request)
    return (
        f"edusphere.register.ip.{institution.pk}.{ip}",
        f"edusphere.register.reg.{institution.pk}.{normalized_key(registration_number)}",
    )


def student_rate_limited(request, institution, registration_number,
                         limit=STUDENT_RATE_LIMIT, window=STUDENT_RATE_WINDOW):
    """True when this address or this registration number has tried too often."""
    return any(
        cache.get(key, 0) >= limit
        for key in _student_rate_keys(request, institution, registration_number)
    )


def record_student_attempt(request, institution, registration_number,
                           window=STUDENT_RATE_WINDOW):
    for key in _student_rate_keys(request, institution, registration_number):
        cache.set(key, cache.get(key, 0) + 1, window)


def contact_rate_limited(request, limit=CONTACT_RATE_LIMIT, window=CONTACT_RATE_WINDOW):
    return cache.get(f"edusphere.contact.{_client_ip(request)}", 0) >= limit


def record_contact_attempt(request, window=CONTACT_RATE_WINDOW):
    key = f"edusphere.contact.{_client_ip(request)}"
    cache.set(key, cache.get(key, 0) + 1, window)


def form_rate_limited(request, scope, limit=CONTACT_RATE_LIMIT,
                      window=CONTACT_RATE_WINDOW):
    key = f"edusphere.{scope}.{_client_ip(request)}"
    return cache.get(key, 0) >= limit


def record_form_attempt(request, scope, window=CONTACT_RATE_WINDOW):
    key = f"edusphere.{scope}.{_client_ip(request)}"
    cache.set(key, cache.get(key, 0) + 1, window)


def _student_candidates(institution, registration_number):
    """Find possible records by number, tolerating case and extra spaces."""
    from academics.models import Student

    key = normalized_key(registration_number)
    candidates = Student.all_objects.filter(
        institution=institution
    ).select_related("user")
    exact = [s for s in candidates if normalized_key(s.student_no) == key]
    if exact:
        return exact
    compact = key.replace(" ", "")
    return [
        s for s in candidates
        if normalized_key(s.student_no).replace(" ", "") == compact
    ]


def find_student(institution, registration_number, surname, date_of_birth):
    """The student whose number, surname and date of birth all match.

    Surname and date of birth are both required, which gives the "at least two
    other details" rule without ever telling the visitor which one failed.
    """
    if not registration_number or not surname or date_of_birth is None:
        return None
    for student in _student_candidates(institution, registration_number):
        if student.user is None:
            continue
        if normalized_key(student.user.last_name) != normalized_key(surname):
            continue
        if student.date_of_birth != date_of_birth:
            continue
        return student
    return None


def student_has_login(student):
    """True when the student already has an active login, or a pending invitation.

    A student added by an administrator may still have an unusable password
    because they have been sent an invitation. That account already exists, so
    public self-registration must not take it over.
    """
    user = getattr(student, "user", None)
    if not user or not user.is_active:
        return False
    if user.has_usable_password() or getattr(user, "must_change_password", False):
        return True
    return user.invitations.filter(
        used_at__isnull=True, expires_at__gt=timezone.now()
    ).exists()


def _verification_link(request, verification):
    path = reverse("register_verify_email", args=[verification.token])
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def send_email_verification(user, request=None):
    """Create a one-time token and email the link through the existing system."""
    if not user.email:
        return None
    verification = EmailVerification.objects.create(user=user)
    link = _verification_link(request, verification)
    body = (
        f"Hello {user.get_full_name() or user.username},\n\n"
        "Please confirm your email address for EduSphere by opening this link:\n"
        f"{link}\n\n"
        "If you did not create this account, you can ignore this message.\n"
    )
    html_body = render_email_html(
        "emails/verify_email.html",
        {"user": user, "link": link, "institution": user.institution},
    )
    message = queue_message(
        "email",
        user.email,
        body,
        subject="Confirm your EduSphere email address",
        institution=user.institution,
        related_object=verification,
        html_body=html_body,
    )
    send_message(message)
    return verification


@transaction.atomic
def register_student(
    institution,
    registration_number,
    surname,
    date_of_birth,
    contact,
    username,
    password,
    request=None,
):
    """Activate a student's portal when the admission record matches.

    Returns one of ``activated``, ``existing`` or ``mismatch``. A mismatch is
    deliberately indistinguishable from a wrong number, surname or date.
    """
    contact = normalize_text(contact)
    username = normalize_text(username)
    with use_institution(institution):
        student = find_student(
            institution, registration_number, surname, date_of_birth
        )
        if student is None:
            record_student_attempt(request, institution, registration_number)
            log_action(
                None,
                "registration.student_failed",
                institution=institution,
                model_name="Student",
                after={"registration_number": normalize_text(registration_number)},
                request=request,
            )
            return {"outcome": "mismatch"}

        if student_has_login(student):
            record_student_attempt(request, institution, registration_number)
            log_action(
                None,
                "registration.student_existing",
                instance=student,
                institution=institution,
                request=request,
            )
            return {"outcome": "existing", "student": student}

        if User.objects.filter(username__iexact=username).exclude(
            pk=student.user_id
        ).exists():
            record_student_attempt(request, institution, registration_number)
            log_action(
                None,
                "registration.student_failed",
                institution=institution,
                model_name="Student",
                after={"reason": "username taken"},
                request=request,
            )
            return {"outcome": "mismatch"}

        user = student.user
        user.username = username
        user.first_name = user.first_name or normalize_text(surname)
        user.last_name = user.last_name or normalize_text(surname)
        user.role = User.Role.STUDENT
        user.institution = institution
        user.is_active = True
        if "@" in contact:
            user.email = contact
        else:
            user.phone = contact
        user.set_password(password)
        user.save()

        verification = send_email_verification(user, request=request)
        log_action(
            user,
            "registration.student_activated",
            instance=student,
            institution=institution,
            after={"email_verification": bool(verification)},
            request=request,
        )
        return {
            "outcome": "activated",
            "student": student,
            "user": user,
            "verification_sent": verification is not None,
        }


def _notify_registration_admins(reg_request):
    """Put a new request in the bell of every administrator of the institution."""
    link = reverse("registration_request_detail", args=[reg_request.pk])
    users = User.tenant_objects.for_institution(reg_request.institution).filter(
        is_active=True
    )
    for user in users:
        if user.is_superuser or user.has_code("admin.manage_users"):
            notify(
                user,
                "New staff registration request",
                f"{reg_request.full_name} asked to join as "
                f"{reg_request.position_label}.",
                link=link,
                category="registration",
                institution=reg_request.institution,
            )


def _unique_username(base):
    base = normalize_text(base)[:30] or "staff"
    candidate = base
    suffix = 0
    while User.objects.filter(username__iexact=candidate).exists():
        suffix += 1
        candidate = f"{base[:25]}{suffix}"
    return candidate


@transaction.atomic
def register_staff(
    institution,
    full_name,
    email,
    phone,
    position,
    department,
    username,
    password,
    staff_no="",
    message="",
    request=None,
):
    """Create an inactive login and a request for an administrator to review."""
    full_name = normalize_text(full_name)
    email = normalize_text(email).lower()
    username = normalize_text(username)
    position = normalize_text(position)
    if position in FORBIDDEN_POSITIONS or position not in STAFF_POSITIONS:
        raise ValidationError("Choose a staff position from the list.")
    if position not in allowed_staff_positions(institution):
        raise ValidationError(
            "This institution is not accepting registrations for that position."
        )
    if department is not None and department.institution_id != institution.pk:
        raise ValidationError("Choose a department from this institution.")

    with use_institution(institution):
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError(
                "That username is already in use. Try another username, or sign in "
                "if you already have an account."
            )
        if User.objects.filter(
            institution=institution, email__iexact=email, is_active=True
        ).exists():
            raise ValidationError(
                "An account already uses that email address at this institution. "
                "Try signing in or resetting your password."
            )

        first_name, _, last_name = full_name.partition(" ")
        user = User(
            username=_unique_username(username),
            email=email,
            first_name=first_name[:150],
            last_name=last_name[:150],
            phone=normalize_text(phone)[:30],
            role=STAFF_POSITIONS[position].legacy_role,
            institution=institution,
            is_active=False,
        )
        user.set_password(password)
        user.save()

        reg_request = StaffRegistrationRequest.objects.create(
            institution=institution,
            user=user,
            full_name=full_name[:150],
            email=email,
            phone=normalize_text(phone)[:30],
            position=position,
            department=department,
            staff_no=normalize_text(staff_no)[:30],
            message=message[:2000],
        )
        _notify_registration_admins(reg_request)
        log_action(
            None,
            "registration.staff_requested",
            instance=reg_request,
            institution=institution,
            after={"position": position},
            request=request,
        )
        return reg_request


def _send_staff_decision(reg_request, approved, reason=""):
    if approved:
        body = (
            f"Hello {reg_request.full_name},\n\n"
            f"Your EduSphere staff account at {reg_request.institution.name} has "
            f"been approved as {reg_request.position_label}.\n"
            "You can now sign in with the username and password you chose.\n"
        )
        subject = "Your EduSphere staff account is approved"
    else:
        body = (
            f"Hello {reg_request.full_name},\n\n"
            f"Your EduSphere staff registration at {reg_request.institution.name} "
            "was not approved.\n"
        )
        if reason:
            body += f"Reason: {reason}\n"
        body += "If you think this is a mistake, contact the institution.\n"
        subject = "Your EduSphere staff registration was not approved"
    message = queue_message(
        "email",
        reg_request.email,
        body,
        subject=subject,
        institution=reg_request.institution,
        related_object=reg_request,
    )
    send_message(message)


@transaction.atomic
def approve_staff_request(reg_request, actor, position=None, department=None,
                          request=None):
    """Approve a request, activate the login and attach the role and profile."""
    if reg_request.status != StaffRegistrationRequest.Status.PENDING:
        raise ValidationError("This request has already been decided.")
    institution = reg_request.institution
    position = normalize_text(position or reg_request.position)
    if position in FORBIDDEN_POSITIONS or position not in STAFF_POSITIONS:
        raise ValidationError("Choose a staff position from the list.")
    if position not in allowed_staff_positions(institution):
        raise ValidationError(
            "This institution does not accept registrations for that position."
        )
    if department is not None and department.institution_id != institution.pk:
        raise ValidationError("Choose a department from this institution.")

    with use_institution(institution):
        ensure_roles(institution)
        role = Role.all_objects.filter(
            institution=institution, code=STAFF_POSITIONS[position].role_code
        ).first()
        if role is None:
            raise ValidationError(
                "The role for that position is missing. Restore the default roles first."
            )

        user = reg_request.user
        user.primary_role = role
        user.role = STAFF_POSITIONS[position].legacy_role
        user.is_active = True
        user.save()

        from core.models import StaffProfile

        profile = StaffProfile.all_objects.filter(user=user).first()
        if profile is None:
            profile = StaffProfile(institution=institution, user=user)
        profile.employee_no = reg_request.staff_no
        profile.job_title = STAFF_POSITIONS[position].label
        profile.phone = reg_request.phone
        profile.save()
        if department is not None:
            profile.departments.add(department)
        elif reg_request.department_id:
            profile.departments.add(reg_request.department)

        before = {"status": reg_request.status}
        reg_request.status = StaffRegistrationRequest.Status.APPROVED
        reg_request.approved_position = position
        reg_request.reviewed_by = actor
        reg_request.reviewed_at = timezone.now()
        reg_request.save(
            update_fields=[
                "status",
                "approved_position",
                "reviewed_by",
                "reviewed_at",
            ]
        )
        log_action(
            actor,
            "registration.staff_approved",
            instance=reg_request,
            before=before,
            after={"status": reg_request.status, "position": position},
            request=request,
        )
        notify(
            user,
            "Your staff account is approved",
            f"You can now sign in as {STAFF_POSITIONS[position].label}.",
            link=reverse("login"),
            category="registration",
            institution=institution,
        )
        _send_staff_decision(reg_request, approved=True)
        return reg_request


@transaction.atomic
def reject_staff_request(reg_request, actor, reason, request=None):
    """Reject a request and keep the login inactive."""
    if reg_request.status != StaffRegistrationRequest.Status.PENDING:
        raise ValidationError("This request has already been decided.")
    reason = normalize_text(reason)
    if not reason:
        raise ValidationError("Give a reason so the person knows what happened.")
    with use_institution(reg_request.institution):
        before = {"status": reg_request.status}
        reg_request.status = StaffRegistrationRequest.Status.REJECTED
        reg_request.rejection_reason = reason[:2000]
        reg_request.reviewed_by = actor
        reg_request.reviewed_at = timezone.now()
        reg_request.save(
            update_fields=[
                "status",
                "rejection_reason",
                "reviewed_by",
                "reviewed_at",
            ]
        )
        log_action(
            actor,
            "registration.staff_rejected",
            instance=reg_request,
            before=before,
            after={"status": reg_request.status, "reason": reason},
            request=request,
        )
        _send_staff_decision(reg_request, approved=False, reason=reason)
        return reg_request


def new_temporary_password():
    """A readable password for an administrator to hand over when needed."""
    return secrets.token_urlsafe(10)


def contact_recipient():
    """Where the public contact form is delivered."""
    return getattr(settings, "EDUSPHERE_CONTACT_EMAIL", CONTACT_EMAIL)


def today():
    return date.today()
