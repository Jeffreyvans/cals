"""Public landing, registration, email verification and admin review screens."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.access import permission_required_code
from core.audit import log_action
from core.email_types import CONTACT_FORM
from core.models import Department, Institution
from core.notifications import EMAIL_FAILURE_MESSAGE, send_platform_email

from .forms import (
    ContactForm,
    InstitutionAuthenticationForm,
    StaffRegistrationForm,
    StudentRegistrationForm,
)
from .models import (
    EmailVerification,
    SignupLink,
    StaffRegistrationRequest,
    User,
)
from .registration import (
    CONTACT_EMAIL,
    CONTACT_PHONE,
    CONTACT_WHATSAPP,
    STAFF_GENERIC_PENDING,
    STAFF_POSITIONS,
    STUDENT_GENERIC_ERROR,
    allowed_staff_positions,
    approve_staff_request,
    contact_rate_limited,
    contact_recipient,
    form_rate_limited,
    position_options,
    public_institutions,
    record_contact_attempt,
    record_form_attempt,
    register_staff,
    register_student,
    reject_staff_request,
    student_rate_limited,
)
from .signup_links import (
    link_offers,
    link_positions,
    link_rate_limited,
    open_signup_links,
    record_link_attempt,
    record_use,
)


class LoginLockoutMixin:
    """Count failed passwords and lock an account for a short while.

    Both the platform sign-in and the institution sign-in use this, so a
    wrong password is treated the same whichever door the visitor used.
    """

    def form_valid(self, form):
        from .user_admin import clear_failed_logins

        clear_failed_logins(form.get_user())
        return super().form_valid(form)

    def form_invalid(self, form):
        from .user_admin import is_locked, note_failed_login

        username = (self.request.POST.get("username") or "").strip()
        account = User.objects.filter(username=username).first()
        if account is not None and account.is_active:
            if is_locked(account):
                messages.error(
                    self.request,
                    "This account is temporarily locked after too many attempts. "
                    "Try again in a few minutes or ask an administrator to unlock it.",
                )
            else:
                note_failed_login(account, request=self.request)
        return super().form_invalid(form)


class EduSphereLoginView(LoginLockoutMixin, LoginView):
    """The public landing page and the sign-in form on one screen."""

    template_name = "registration/login.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("contact_form", ContactForm())
        context.setdefault("contact_sent", False)
        context.setdefault("contact_rate_limited", False)
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get("form") == "contact":
            return self._contact(request)
        return super().post(request, *args, **kwargs)

    def _contact(self, request):
        form = ContactForm(request.POST)
        if form.cleaned_data.get("website") if form.is_valid() else False:
            return self.render_to_response(
                self.get_context_data(contact_form=ContactForm(), contact_sent=True)
            )
        if contact_rate_limited(request):
            return self.render_to_response(
                self.get_context_data(
                    contact_form=form,
                    contact_rate_limited=True,
                ),
                status=429,
            )
        if form.is_valid():
            data = form.cleaned_data
            body = (
                f"New EduSphere contact message\n\n"
                f"Name: {data['name']}\n"
                f"Email: {data['email']}\n"
                f"Phone: {data.get('phone') or 'Not given'}\n\n"
                f"{data['message']}\n"
            )
            message = send_platform_email(
                "EduSphere contact form",
                body,
                contact_recipient(),
                reply_to=[data["email"]],
                email_type=CONTACT_FORM,
            )
            record_contact_attempt(request)
            if getattr(message, "status", "") != "sent":
                messages.error(request, EMAIL_FAILURE_MESSAGE)
            return self.render_to_response(
                self.get_context_data(contact_form=ContactForm(), contact_sent=True)
            )
        return self.render_to_response(
            self.get_context_data(contact_form=form)
        )


class EduSphereLandingView(EduSphereLoginView):
    """The platform page at ``/``.

    A signed-out visitor sees the same landing and sign-in screen as
    ``/login/``. A signed-in visitor is sent straight to the dashboard.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)


class InstitutionLoginView(LoginLockoutMixin, LoginView):
    """A branded sign-in page for one institution at ``/i/<slug>/login/``.

    The page shows the institution's own logo, name and tagline, and it only
    accepts accounts that belong to that institution (a platform superuser
    may still sign in). It is a shareable door: an institution can put this
    link on its website so its people land on the right page. The ordinary
    platform sign-in at ``/login/`` keeps working for everyone.
    """

    template_name = "registration/institution_login.html"
    form_class = InstitutionAuthenticationForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        self.institution = get_object_or_404(
            Institution.objects.filter(is_active=True), slug=kwargs["slug"]
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["institution"] = self.institution
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["institution"] = self.institution
        links = open_signup_links(self.institution)
        registration_available = self.institution.public_registration or bool(links)
        show_register = (
            self.institution.show_register_button and registration_available
        )
        context["show_register"] = show_register
        context["hide_register"] = not show_register
        context["register_url"] = (
            links[0].path() if links else reverse("register_choice")
        )
        return context


def _public_page(request, template, context=None, status=200):
    context = context or {}
    context.setdefault("contact_email", CONTACT_EMAIL)
    context.setdefault("contact_phone", CONTACT_PHONE)
    context.setdefault("contact_whatsapp", CONTACT_WHATSAPP)
    return render(request, template, context, status=status)


def register_choice(request):
    """The first step: are you a student or a member of staff?"""
    return _public_page(
        request,
        "registration/register_choice.html",
        {
            "institutions": public_institutions(),
            "has_institutions": public_institutions().exists(),
        },
    )


def register_student_view(request):
    institution = None
    if request.method == "POST":
        institution = public_institutions().filter(
            pk=request.POST.get("institution") or 0
        ).first()
        if institution is not None and student_rate_limited(
            request, institution, request.POST.get("registration_number", "")
        ):
            log_action(
                None,
                "registration.student_rate_limited",
                institution=institution,
                request=request,
            )
            return _public_page(
                request,
                "registration/register_student.html",
                {
                    "form": StudentRegistrationForm(request.POST),
                    "rate_limited": True,
                    "institution": institution,
                },
                status=429,
            )

        form = StudentRegistrationForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            institution = data["institution"]
            result = register_student(
                institution,
                data["registration_number"],
                data["surname"],
                data["date_of_birth"],
                data["contact"],
                data["username"],
                data["password"],
                request=request,
            )
            if result["outcome"] == "activated":
                return _public_page(
                    request,
                    "registration/register_student.html",
                    {
                        "form": StudentRegistrationForm(
                            initial={"institution": institution.pk}
                        ),
                        "activated": True,
                        "student": result["student"],
                        "verification_sent": result.get("verification_sent", False),
                        "institution": institution,
                    },
                )
            if result["outcome"] == "existing":
                return _public_page(
                    request,
                    "registration/register_student.html",
                    {
                        "form": StudentRegistrationForm(
                            initial={"institution": institution.pk}
                        ),
                        "existing_account": True,
                        "institution": institution,
                    },
                )
            return _public_page(
                request,
                "registration/register_student.html",
                {
                    "form": StudentRegistrationForm(request.POST),
                    "generic_error": STUDENT_GENERIC_ERROR,
                    "institution": institution,
                    "contact_url": reverse(
                        "register_contact", args=[institution.pk]
                    ),
                },
            )
    else:
        initial = {}
        if request.GET.get("institution"):
            initial["institution"] = request.GET["institution"]
        form = StudentRegistrationForm(initial=initial)

    return _public_page(
        request,
        "registration/register_student.html",
        {
            "form": form,
            "institutions": public_institutions(),
            "has_institutions": public_institutions().exists(),
            "institution": institution,
        },
    )


def register_staff_view(request):
    institution = None
    if request.method == "POST":
        institution = public_institutions().filter(
            pk=request.POST.get("institution") or 0
        ).first()
        if institution is not None and form_rate_limited(
            request, f"register.staff.{institution.pk}"
        ):
            log_action(
                None,
                "registration.staff_rate_limited",
                institution=institution,
                request=request,
            )
            return _public_page(
                request,
                "registration/register_staff.html",
                {
                    "form": StaffRegistrationForm(request.POST),
                    "rate_limited": True,
                    "institution": institution,
                },
                status=429,
            )

        form = StaffRegistrationForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                reg_request = register_staff(
                    data["institution"],
                    data["full_name"],
                    data["email"],
                    data["phone"],
                    data["position"],
                    data.get("department"),
                    data["username"],
                    data["password"],
                    staff_no=data.get("staff_no", ""),
                    message=data.get("message", ""),
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                record_form_attempt(
                    request, f"register.staff.{data['institution'].pk}"
                )
                request.session["pending_registration_name"] = (
                    reg_request.full_name
                )
                return redirect("register_staff_sent")
    else:
        initial = {}
        if request.GET.get("institution"):
            initial["institution"] = request.GET["institution"]
        form = StaffRegistrationForm(initial=initial)

    return _public_page(
        request,
        "registration/register_staff.html",
        {
            "form": form,
            "institutions": public_institutions(),
            "has_institutions": public_institutions().exists(),
            "positions": position_options(),
            "institution": institution,
            "position_map": {
                str(inst.pk): allowed_staff_positions(inst)
                for inst in public_institutions()
            },
        },
    )


def register_staff_sent(request):
    return _public_page(
        request,
        "registration/register_staff_sent.html",
        {
            "name": request.session.pop("pending_registration_name", ""),
            "message": STAFF_GENERIC_PENDING,
        },
    )


def register_contact(request, pk):
    institution = get_object_or_404(
        public_institutions(), pk=pk
    )
    return _public_page(
        request,
        "registration/register_contact.html",
        {"institution": institution},
    )


def verify_email(request, token):
    verification = get_object_or_404(
        EmailVerification.objects.select_related("user", "user__institution"),
        token=token,
    )
    if verification.verified_at is None:
        verification.verified_at = timezone.now()
        verification.save(update_fields=["verified_at"])
    return _public_page(
        request,
        "registration/verify_email.html",
        {
            "verification": verification,
            "user": verification.user,
            "verified": True,
        },
    )


def privacy_notice(request):
    return _public_page(request, "registration/privacy.html")


@login_required
@permission_required_code("admin.manage_users")
def registration_requests(request):
    institution = getattr(request, "institution", None)
    queryset = StaffRegistrationRequest.objects.select_related(
        "user", "department", "reviewed_by"
    )
    if institution is None:
        queryset = queryset.none()
    else:
        queryset = queryset.filter(institution=institution)
    status = request.GET.get("status", "")
    position = request.GET.get("position", "")
    query = " ".join(request.GET.get("q", "").split())
    if status in StaffRegistrationRequest.Status.values:
        queryset = queryset.filter(status=status)
    if position in STAFF_POSITIONS:
        queryset = queryset.filter(position=position)
    if query:
        from django.db.models import Q

        queryset = queryset.filter(
            Q(full_name__icontains=query)
            | Q(email__icontains=query)
            | Q(user__username__icontains=query)
        )
    return render(
        request,
        "registration/registration_requests.html",
        {
            "requests": queryset,
            "statuses": StaffRegistrationRequest.Status.choices,
            "positions": position_options(),
            "current_status": status,
            "current_position": position,
            "query": query,
            "pending_count": queryset.filter(
                status=StaffRegistrationRequest.Status.PENDING
            ).count(),
        },
    )


@login_required
@permission_required_code("admin.manage_users")
def registration_request_detail(request, pk):
    institution = getattr(request, "institution", None)
    if institution is None:
        raise Http404
    reg_request = get_object_or_404(
        StaffRegistrationRequest.objects.select_related(
            "user", "department", "reviewed_by"
        ).filter(institution=institution),
        pk=pk,
    )
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "approve":
                department = None
                department_id = request.POST.get("department")
                if department_id:
                    department = Department.all_objects.filter(
                        institution=reg_request.institution, pk=department_id
                    ).first()
                approve_staff_request(
                    reg_request,
                    request.user,
                    position=request.POST.get("position") or reg_request.position,
                    department=department,
                    request=request,
                )
                messages.success(
                    request,
                    f"{reg_request.full_name} was approved as "
                    f"{reg_request.position_label}.",
                )
            elif action == "reject":
                reject_staff_request(
                    reg_request,
                    request.user,
                    request.POST.get("reason", ""),
                    request=request,
                )
                messages.success(
                    request, f"{reg_request.full_name}'s request was rejected."
                )
            else:
                messages.error(request, "Choose approve or reject.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("registration_request_detail", pk=reg_request.pk)

    departments = Department.all_objects.filter(
        institution=reg_request.institution
    ).order_by("name")
    return render(
        request,
        "registration/registration_request_detail.html",
        {
            "reg_request": reg_request,
            "positions": [
                {"code": code, "label": STAFF_POSITIONS[code].label}
                for code in allowed_staff_positions(reg_request.institution)
            ],
            "departments": departments,
        },
    )


def _signup_closed(request, link, reason):
    return _public_page(
        request,
        "registration/signup_link.html",
        {
            "link": link,
            "institution": link.institution,
            "closed": True,
            "close_reason": reason,
        },
    )


def _signup_staff_form(link, data=None):
    form = StaffRegistrationForm(
        data, institution=link.institution, positions=link_positions(link)
    )
    if link.position:
        form.fields["position"].widget = forms.HiddenInput()
        form.fields["position"].initial = link.position
    return form


def _signup_student(request, link):
    institution = link.institution
    if request.method == "POST":
        registration_number = request.POST.get("registration_number", "")
        if student_rate_limited(request, institution, registration_number):
            record_link_attempt(request, link)
            log_action(
                None, "registration.student_rate_limited",
                institution=institution, request=request,
            )
            return _public_page(
                request,
                "registration/signup_link.html",
                {
                    "link": link,
                    "institution": institution,
                    "path": "student",
                    "form": StudentRegistrationForm(
                        request.POST, institution=institution),
                    "rate_limited": True,
                },
                status=429,
            )
        form = StudentRegistrationForm(request.POST, institution=institution)
        if form.is_valid():
            data = form.cleaned_data
            result = register_student(
                institution,
                data["registration_number"],
                data["surname"],
                data["date_of_birth"],
                data["contact"],
                data["username"],
                data["password"],
                request=request,
            )
            if result["outcome"] == "activated":
                record_use(
                    link, "student", user=result.get("user"),
                    email=data.get("contact", ""), request=request,
                )
                return _public_page(
                    request,
                    "registration/signup_link.html",
                    {
                        "link": link,
                        "institution": institution,
                        "path": "student",
                        "activated": True,
                        "student": result["student"],
                        "verification_sent": result.get("verification_sent", False),
                    },
                )
            if result["outcome"] == "existing":
                return _public_page(
                    request,
                    "registration/signup_link.html",
                    {
                        "link": link,
                        "institution": institution,
                        "path": "student",
                        "existing_account": True,
                    },
                )
            context = {
                "link": link,
                "institution": institution,
                "path": "student",
                "form": form,
                "generic_error": STUDENT_GENERIC_ERROR,
            }
            if institution.public_registration:
                context["contact_url"] = reverse(
                    "register_contact", args=[institution.pk])
            return _public_page(request, "registration/signup_link.html", context)
    else:
        form = StudentRegistrationForm(institution=institution)
    return _public_page(
        request,
        "registration/signup_link.html",
        {
            "link": link,
            "institution": institution,
            "path": "student",
            "form": form,
        },
    )


def _signup_staff(request, link):
    institution = link.institution
    if request.method == "POST":
        if form_rate_limited(request, f"signup.staff.{link.pk}"):
            record_link_attempt(request, link)
            log_action(
                None, "registration.staff_rate_limited",
                institution=institution, request=request,
            )
            return _public_page(
                request,
                "registration/signup_link.html",
                {
                    "link": link,
                    "institution": institution,
                    "path": "staff",
                    "form": _signup_staff_form(link, request.POST),
                    "rate_limited": True,
                },
                status=429,
            )
        form = _signup_staff_form(link, request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                reg_request = register_staff(
                    institution,
                    data["full_name"],
                    data["email"],
                    data["phone"],
                    data["position"],
                    data.get("department"),
                    data["username"],
                    data["password"],
                    staff_no=data.get("staff_no", ""),
                    message=data.get("message", ""),
                    allowed_positions=link_positions(link),
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                record_link_attempt(request, link)
                record_use(
                    link, "staff", user=reg_request.user,
                    email=data["email"], request=request,
                )
                return _public_page(
                    request,
                    "registration/signup_link.html",
                    {
                        "link": link,
                        "institution": institution,
                        "path": "staff",
                        "staff_sent": True,
                        "staff_name": reg_request.full_name,
                        "message": STAFF_GENERIC_PENDING,
                    },
                )
    else:
        form = _signup_staff_form(link)
    return _public_page(
        request,
        "registration/signup_link.html",
        {
            "link": link,
            "institution": institution,
            "path": "staff",
            "form": form,
            "preset_position": link.position_label,
        },
    )


def signup_link(request, token):
    """The public page behind a shareable sign-up link."""
    link = get_object_or_404(
        SignupLink.objects.select_related("institution"), token=token)
    institution = link.institution
    if not institution.is_active:
        return _signup_closed(request, link, "This institution is not active.")
    if not link.is_open:
        return _signup_closed(request, link, "This sign-up link is closed.")

    if link_rate_limited(request, link):
        return _public_page(
            request,
            "registration/signup_link.html",
            {"link": link, "institution": institution, "rate_limited": True},
            status=429,
        )

    offers = link_offers(link)
    path = (request.POST.get("path") or request.GET.get("as") or "").strip()
    if path not in offers:
        path = ""
    if link.kind != SignupLink.Kind.ANY:
        path = next(iter(offers))
    if not path:
        return _public_page(
            request,
            "registration/signup_link.html",
            {
                "link": link,
                "institution": institution,
                "choose": True,
                "offers": offers,
            },
        )
    if path == "student":
        return _signup_student(request, link)
    return _signup_staff(request, link)


def password_reset_done(request):
    return _public_page(request, "registration/password_reset_done.html")


def password_reset_complete(request):
    return _public_page(request, "registration/password_reset_complete.html")


__all__ = [
    "EduSphereLoginView",
    "password_reset_complete",
    "password_reset_done",
    "privacy_notice",
    "register_choice",
    "register_contact",
    "register_staff_sent",
    "register_staff_view",
    "register_student_view",
    "registration_request_detail",
    "registration_requests",
    "signup_link",
    "verify_email",
]
