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
from django.utils.safestring import mark_safe

from config.brand import PRODUCT_NAME
from core.access import permission_required_code
from core.audit import log_action
from core.email_types import CONTACT_FORM
from core.errors import add_error_message
from core.idempotency import begin, finish, new_key
from core.models import Department, Institution
from core.notifications import EMAIL_FAILURE_MESSAGE, send_platform_email

from .admin_forms import RegistrationApprovalForm
from .forms import (
    IDENTIFIER_AMBIGUOUS_MESSAGE,
    IDENTIFIER_INVALID_MESSAGE,
    ContactForm,
    IdentifierAuthenticationForm,
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

    def _identifier_target_account(self, form):
        target = getattr(form, "resolved_user", None)
        if target is not None:
            return target
        identifier = (self.request.POST.get("username") or "").strip()
        if not identifier:
            return None
        try:
            from .forms import (
                _resolve_institution_login_user,
                _resolve_main_login_user,
            )

            if getattr(form, "institution", None) is not None:
                user, _ = _resolve_institution_login_user(identifier, form.institution)
                return user
            user, _normalized, status = _resolve_main_login_user(identifier)
            return user if status == "ok" else None
        except Exception:
            return User.objects.filter(username=identifier).first()

    def form_valid(self, form):
        from .user_admin import clear_failed_logins

        user = form.get_user()
        clear_failed_logins(user)
        # Only a real form login opens the guided setup; force_login and
        # Client.login() fire the auth signal but never reach this method.
        from core.institution_setup import queue_setup_after_login

        queue_setup_after_login(user, self.request)
        return super().form_valid(form)

    def form_invalid(self, form):
        from .user_admin import LOGIN_FAILURE_LIMIT, is_locked, note_failed_login

        # Cross-institution email ambiguity is guidance, not a failed password attempt.
        if getattr(form, "ambiguous_identifier", False):
            return super().form_invalid(form)

        account = self._identifier_target_account(form)
        if account is not None and account.is_active:
            if is_locked(account):
                add_error_message(
                    self.request,
                    "AUTH-LOCKED",
                    extra=self._unlock_hint(account),
                )
            else:
                note_failed_login(account, request=self.request)
                if is_locked(account):
                    add_error_message(
                        self.request, "AUTH-LOCKED", extra=self._unlock_hint(account)
                    )
                else:
                    left = max(1, LOGIN_FAILURE_LIMIT - (account.failed_login_count or 0))
                    add_error_message(
                        self.request,
                        "AUTH-FAILED",
                        extra=f"{left} attempt{'s' if left != 1 else ''} left.",
                    )
        return super().form_invalid(form)

    @staticmethod
    def _unlock_hint(account):
        remaining = getattr(account, "locked_until", None)
        if remaining is None:
            return "Ask your school administrator to unlock it."
        seconds = max(0, int((remaining - timezone.now()).total_seconds()))
        minutes = max(1, (seconds + 59) // 60)
        return (
            f"It unlocks in about {minutes} minute{'s' if minutes != 1 else ''}. "
            "Ask your school administrator to unlock it early."
        )


class EduSphereLoginView(LoginLockoutMixin, LoginView):
    """The public landing page and the sign-in form on one screen."""

    template_name = "registration/login.html"
    form_class = IdentifierAuthenticationForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("contact_form", ContactForm())
        context.setdefault("contact_sent", False)
        context.setdefault("contact_rate_limited", False)
        context["identifier_invalid_message"] = IDENTIFIER_INVALID_MESSAGE
        # A trusted, static guidance message; keeping the apostrophe literal
        # makes it readable in the rendered alert.
        context["identifier_ambiguous_message"] = mark_safe(IDENTIFIER_AMBIGUOUS_MESSAGE)
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
                f"New {PRODUCT_NAME} contact message\n\n"
                f"Name: {data['name']}\n"
                f"Email: {data['email']}\n"
                f"Phone: {data.get('phone') or 'Not given'}\n\n"
                f"{data['message']}\n"
            )
            message = send_platform_email(
                f"{PRODUCT_NAME} contact form",
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
        slug = kwargs["slug"]
        self.institution = Institution.objects.filter(is_active=True, slug=slug).first()
        if self.institution is None:
            # Follow a recently changed slug so old login links keep working.
            from core.models import resolve_institution_slug

            old = resolve_institution_slug(slug)
            if old is None:
                raise Http404("Institution not found.")
            target = reverse("institution_login", args=[old.slug])
            query = request.META.get("QUERY_STRING")
            return redirect(f"{target}?{query}" if query else target)
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        """A newly onboarded administrator starts with the guided setup."""
        if self.request.session.get("needs_institution_setup"):
            self.request.session.pop("needs_institution_setup")
            from core.institution_setup import mark_setup_started

            mark_setup_started(self.request.user)
            return reverse("institution_setup")
        return super().get_success_url()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["institution"] = self.institution
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["institution"] = self.institution
        context["identifier_invalid_message"] = IDENTIFIER_INVALID_MESSAGE
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
    context.setdefault(
        "idempotency_key",
        (getattr(request, "POST", None) or {}).get("idempotency_key") or new_key(),
    )
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
            idem_token, previous = begin(request, f"registration.student:{institution.pk}")
            if previous and previous.get("outcome") == "activated":
                return _public_page(
                    request,
                    "registration/register_student.html",
                    {
                        "form": StudentRegistrationForm(
                            initial={"institution": institution.pk}
                        ),
                        "activated": True,
                        "username": request.POST.get("username", ""),
                        "institution": institution,
                    },
                )
            if previous and previous.get("outcome") == "existing":
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
            finish(idem_token, {"outcome": result["outcome"]})
            if result["outcome"] == "activated":
                if not result.get("verification_sent"):
                    add_error_message(
                        request,
                        "EXT-EMAIL",
                        extra="Your account was created. Use Show one-time link instead.",
                    )
                return _public_page(
                    request,
                    "registration/register_student.html",
                    {
                        "form": StudentRegistrationForm(
                            initial={"institution": institution.pk}
                        ),
                        "activated": True,
                        "student": result["student"],
                        "username": data.get("username") or getattr(result.get("user"), "username", ""),
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
            idem_token, previous = begin(request, f"registration.staff:{data['institution'].pk}")
            if previous and previous.get("done"):
                return redirect("register_staff_sent")
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
                finish(idem_token, {"done": True})
                record_form_attempt(
                    request, f"register.staff.{data['institution'].pk}"
                )
                request.session["pending_staff_registration"] = {
                    "name": reg_request.full_name,
                    "username": reg_request.user.username,
                    "institution": data["institution"].name,
                    "institution_slug": getattr(data["institution"], "slug", ""),
                }
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
    pending = request.session.pop("pending_staff_registration", None) or {}
    # Backward compatibility with older session key.
    if not pending:
        name = request.session.pop("pending_registration_name", "")
        if name:
            pending = {"name": name}
    return _public_page(
        request,
        "registration/register_staff_sent.html",
        {
            "name": pending.get("name", ""),
            "username": pending.get("username", ""),
            "institution": pending.get("institution", ""),
            "institution_slug": pending.get("institution_slug", ""),
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
    approval_form = RegistrationApprovalForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "approve" else None,
        institution=reg_request.institution,
        registrant=reg_request.user,
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "approve":
            if approval_form.is_valid():
                try:
                    approve_staff_request(
                        reg_request,
                        request.user,
                        position=approval_form.cleaned_data["position"],
                        department=approval_form.cleaned_data["department"],
                        username=approval_form.cleaned_data["username"],
                        request=request,
                    )
                except ValidationError as exc:
                    messages.error(request, " ".join(exc.messages))
                else:
                    messages.success(
                        request,
                        f"{reg_request.full_name} was approved as "
                        f"{reg_request.position_label}.",
                    )
                    return redirect("registration_request_detail", pk=reg_request.pk)
            else:
                messages.error(request, "Check the highlighted fields and try again.")
        elif action == "reject":
            try:
                reject_staff_request(
                    reg_request,
                    request.user,
                    request.POST.get("reason", ""),
                    request=request,
                )
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                messages.success(
                    request, f"{reg_request.full_name}'s request was rejected."
                )
                return redirect("registration_request_detail", pk=reg_request.pk)
        else:
            messages.error(request, "Choose approve or reject.")
            return redirect("registration_request_detail", pk=reg_request.pk)

    return render(
        request,
        "registration/registration_request_detail.html",
        {
            "reg_request": reg_request,
            "approval_form": approval_form,
            "positions": [
                {"code": code, "label": STAFF_POSITIONS[code].label}
                for code in allowed_staff_positions(reg_request.institution)
            ],
            "departments": Department.all_objects.filter(
                institution=reg_request.institution
            ).order_by("name"),
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
                        "username": data.get("username") or getattr(result.get("user"), "username", ""),
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
                        "staff_username": reg_request.user.username,
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
