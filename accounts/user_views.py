"""The administrator Users area: list, add, edit, link and act on accounts."""

from datetime import datetime, time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeView
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.access import permission_required_code
from core.models import AuditEntry, Department, Institution, Role

from .admin_forms import (
    DuplicateForm,
    EditUserForm,
    LinkParentForm,
    StaffAccountForm,
    StrongPasswordChangeForm,
    StudentAccountForm,
)
from .models import SignupLink, StaffRegistrationRequest, User, UserInvitation
from .signup_links import (
    create_signup_link,
    delete_signup_link,
    reactivate_signup_link,
    revoke_signup_link,
)
from .user_admin import (
    accept_invitation,
    add_staff_member,
    attach_login,
    can_add_staff,
    can_add_student,
    can_manage_users,
    change_user_role,
    deactivate_user,
    deactivation_has_history,
    flag_duplicate,
    is_institution_admin,
    is_locked,
    link_parent,
    merge_users,
    reactivate_user,
    resend_invitation,
    reset_password,
    unlock_user,
    update_user,
)

PAGE_SIZE = 25


def _institution(request):
    inst = getattr(request, "institution", None)
    if inst is None:
        inst = Institution.objects.filter(slug="default").first()
    return inst


def _guard(request):
    if not can_manage_users(request.user):
        raise PermissionDenied("Your role does not allow this page.")


def _target_in_institution(request, pk):
    inst = _institution(request)
    return get_object_or_404(User.objects.select_related("primary_role"), pk=pk,
                             institution=inst)


def _may_touch(actor, target):
    """A registrar may add students; only staff-capable people touch staff."""
    if is_institution_admin(actor):
        return True
    if target.role in (User.Role.STUDENT, User.Role.PARENT):
        return can_add_student(actor)
    return can_add_staff(actor)


@login_required
@permission_required_code("admin.manage_users")
def user_list(request):
    """Every account in this institution, with the filters an administrator needs."""
    inst = _institution(request)
    queryset = User.objects.filter(institution=inst).select_related("primary_role")
    query = " ".join(request.GET.get("q", "").split())
    kind = request.GET.get("type", "")
    role_id = request.GET.get("role", "")
    department_id = request.GET.get("department", "")
    status = request.GET.get("status", "")
    verified = request.GET.get("verified", "")

    if query:
        from core.numbering import number_key

        # Numbers may be typed with or without their separators, so compare
        # compact keys here as well as doing the ordinary contains lookup.
        number_pks = [
            pk for pk, student_no, employee_no in queryset.values_list(
                "pk", "student__student_no", "staff_profile__employee_no")
            if number_key(query) in number_key(student_no)
            or number_key(query) in number_key(employee_no)
        ]
        queryset = queryset.filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
            | Q(student__student_no__icontains=query)
            | Q(staff_profile__employee_no__icontains=query)
            | Q(pk__in=number_pks)
        )

    if kind == "staff":
        queryset = queryset.exclude(
            role__in=(User.Role.STUDENT, User.Role.PARENT)
        )
    elif kind == "student":
        queryset = queryset.filter(role=User.Role.STUDENT)
    elif kind == "parent":
        queryset = queryset.filter(role=User.Role.PARENT)

    if role_id:
        queryset = queryset.filter(primary_role_id=role_id)
    if department_id:
        queryset = queryset.filter(staff_profile__departments__pk=department_id)
    if status == "active":
        queryset = queryset.filter(is_active=True)
    elif status == "inactive":
        queryset = queryset.filter(is_active=False)
    elif status == "locked":
        queryset = queryset.filter(locked_until__gt=timezone.now())
    elif status == "pending":
        now = timezone.now()
        queryset = queryset.filter(
            Q(invitations__used_at__isnull=True, invitations__expires_at__gt=now)
            | Q(must_change_password=True)
        ).distinct()
    if verified == "yes":
        queryset = queryset.filter(email_verifications__verified_at__isnull=False).distinct()
    elif verified == "no":
        queryset = queryset.exclude(
            email_verifications__verified_at__isnull=False
        ).distinct()

    queryset = queryset.order_by("first_name", "last_name", "username")
    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    now = timezone.now()
    for account in page:
        account.has_open_invitation = account.invitations.filter(
            used_at__isnull=True, expires_at__gt=now
        ).exists()
        account.is_currently_locked = is_locked(account)
        account.email_verified = account.email_verifications.filter(
            verified_at__isnull=False
        ).exists()
        account.student_record = getattr(account, "student", None)
        account.staff_record = getattr(account, "staff_profile", None)
        if account.staff_record is not None:
            account.department_names = ", ".join(
                account.staff_record.departments.values_list("name", flat=True)
            )

    pending_requests = StaffRegistrationRequest.objects.filter(
        institution=inst, status=StaffRegistrationRequest.Status.PENDING
    ).count()
    return render(request, "accounts/user_list.html", {
        "inst": inst,
        "page": page,
        "query": query,
        "current_type": kind,
        "current_role": role_id,
        "current_department": department_id,
        "current_status": status,
        "current_verified": verified,
        "roles": Role.all_objects.filter(institution=inst).order_by("name"),
        "departments": Department.all_objects.filter(institution=inst).order_by("name"),
        "pending_requests": pending_requests,
        "can_add_staff": can_add_staff(request.user),
        "can_add_student": can_add_student(request.user),
    })


@login_required
@permission_required_code("admin.manage_users")
def user_detail(request, pk):
    account = _target_in_institution(request, pk)
    profile = getattr(account, "staff_profile", None)
    student = getattr(account, "student", None)
    invitations = account.invitations.select_related("invited_by")[:5]
    audits = AuditEntry.all_objects.filter(
        institution=account.institution
    ).filter(
        Q(model_name="User", object_id=str(account.pk))
        | Q(actor=account)
        | Q(model_name="StaffProfile", object_id=str(getattr(profile, "pk", "")))
    ).select_related("actor")[:20]
    now = timezone.now()
    account.has_open_invitation = account.invitations.filter(
        used_at__isnull=True, expires_at__gt=now
    ).exists()
    account.email_verified = account.email_verifications.filter(
        verified_at__isnull=False
    ).exists()
    return render(request, "accounts/user_detail.html", {
        "inst": _institution(request),
        "account": account,
        "profile": profile,
        "student": student,
        "invitations": invitations,
        "audits": audits,
        "is_locked": is_locked(account),
        "is_admin": is_institution_admin(request.user),
        "may_touch": _may_touch(request.user, account),
    })


@login_required
@permission_required_code("admin.manage_users")
def user_add_staff(request):
    if not can_add_staff(request.user):
        raise PermissionDenied("Your role does not allow adding staff.")
    inst = _institution(request)
    form = StaffAccountForm(
        request.POST or None, institution=inst, user=request.user
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            account = add_staff_member(
                inst,
                full_name=data["full_name"],
                email=data["email"],
                phone=data.get("phone", ""),
                role=data["role"],
                departments=data.get("departments"),
                job_title=data.get("job_title", ""),
                staff_no=data.get("staff_no", ""),
                start_date=data.get("start_date"),
                access_mode=data["access_mode"],
                actor=request.user,
                request=request,
            )
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        else:
            return _show_credentials(request, account, "Staff member added.")
    return render(request, "accounts/user_form.html", {
        "inst": inst,
        "form": form,
        "title": "Add a staff member",
        "subtitle": "The login, staff profile and role are created together.",
        "back": reverse("user_list"),
    })


@login_required
@permission_required_code("admin.manage_users", "students.create")
def user_add_student(request):
    if not can_add_student(request.user):
        raise PermissionDenied("Your role does not allow adding students.")
    inst = _institution(request)
    form = StudentAccountForm(
        request.POST or None, institution=inst, user=request.user
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            result = _create_student(inst, data, request)
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        else:
            return _show_credentials(request, result["account"], "Student added.")
    return render(request, "accounts/user_form.html", {
        "inst": inst,
        "form": form,
        "title": "Add a student",
        "subtitle": (
            "A registration number and login are created automatically. "
            "Tick the invoice box to bill the first fee."
        ),
        "back": reverse("user_list"),
    })


def _create_student(inst, data, request):
    from admissions.services import create_student_record

    programme = data["programme"]
    result = create_student_record(
        inst,
        first_name=data["first_name"],
        last_name=data["last_name"],
        email=data.get("email", ""),
        phone=data.get("phone", ""),
        username=data.get("username", ""),
        programme=programme,
        school_class=data.get("school_class"),
        stream=data.get("stream"),
        year_or_grade=data.get("year_or_grade") or "1",
        date_of_birth=data.get("date_of_birth"),
        guardian_name=data.get("guardian_name", ""),
        guardian_phone=data.get("guardian_phone", ""),
        access_mode=data["access_mode"],
        actor=request.user,
        request=request,
        first_invoice=data.get("first_invoice", False),
        fee_structure=data.get("fee_structure"),
    )
    return result


def _show_credentials(request, account, success_message):
    """Put the one-time password in the session, never in the database."""
    request.session["new_user_credentials"] = {
        "user_id": account.user.pk,
        "username": account.user.username,
        "password": account.password,
        "access_mode": account.access_mode,
    }
    request.session.pop("new_user_credentials_views", None)
    if account.password:
        messages.success(
            request,
            "Copy the temporary password now. It will not be shown again.",
        )
    else:
        messages.success(request, "Invitation email sent.")
    return redirect("user_created")


def _clear_credentials(request):
    request.session.pop("new_user_credentials", None)
    request.session.pop("new_user_credentials_views", None)


# The page is reached by a redirect, so the first request is often a browser
# or test-framework check of that redirect. It is rendered once more for the
# person, then the password is forgotten for good.
CREDENTIAL_VIEW_BUDGET = 2


@login_required
@permission_required_code("admin.manage_users", "students.create")
def user_created(request):
    credentials = request.session.get("new_user_credentials")
    inst = _institution(request)
    account = None
    if credentials:
        account = User.objects.filter(
            institution=inst, pk=credentials.get("user_id")
        ).first()
    if credentials is None or account is None:
        _clear_credentials(request)
        messages.info(request, "That one-time password has already been shown or expired.")
        return redirect("user_list")

    views = request.session.get("new_user_credentials_views", 0)
    if views >= CREDENTIAL_VIEW_BUDGET - 1:
        _clear_credentials(request)
    else:
        request.session["new_user_credentials_views"] = views + 1
    return render(request, "accounts/user_created.html", {
        "inst": inst,
        "account": account,
        "credentials": credentials,
    })


@login_required
@permission_required_code("admin.manage_users")
def user_edit(request, pk):
    account = _target_in_institution(request, pk)
    if not _may_touch(request.user, account):
        raise PermissionDenied("Your role does not allow editing this account.")
    inst = _institution(request)
    form = EditUserForm(
        request.POST or None,
        instance=account,
        institution=inst,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            update_user(
                account,
                first_name=data["first_name"],
                last_name=data["last_name"],
                email=data.get("email", ""),
                phone=data.get("phone", ""),
                actor=request.user,
                request=request,
            )
            role = data.get("role")
            if role is not None and role.pk != account.primary_role_id:
                change_user_role(account, role, actor=request.user, request=request)
            profile = getattr(account, "staff_profile", None)
            if profile is not None:
                profile.departments.set(data.get("departments") or [])
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        else:
            messages.success(request, "Account saved.")
            return redirect("user_detail", pk=account.pk)
    return render(request, "accounts/user_form.html", {
        "inst": inst,
        "form": form,
        "account": account,
        "title": f"Edit {account.get_full_name() or account.username}",
        "subtitle": "Role changes apply the next time this person opens a page.",
        "back": reverse("user_detail", args=[account.pk]),
    })


@login_required
@permission_required_code("admin.manage_users")
def user_action(request, pk):
    account = _target_in_institution(request, pk)
    if request.method != "POST":
        return redirect("user_detail", pk=account.pk)
    if not _may_touch(request.user, account):
        raise PermissionDenied("Your role does not allow this action.")
    action = request.POST.get("action", "")
    try:
        if action == "deactivate":
            if deactivation_has_history(account):
                messages.info(
                    request,
                    "This account keeps its invoices, results and audit history. "
                    "It is deactivated, not deleted.",
                )
            deactivate_user(account, actor=request.user, request=request,
                            reason=request.POST.get("reason", ""))
            messages.success(request, "Account deactivated.")
        elif action == "reactivate":
            reactivate_user(account, actor=request.user, request=request)
            messages.success(request, "Account reactivated.")
        elif action == "unlock":
            unlock_user(account, actor=request.user, request=request)
            messages.success(request, "Account unlocked.")
        elif action == "reset":
            password = reset_password(account, actor=request.user, request=request)
            request.session["new_user_credentials"] = {
                "user_id": account.pk,
                "username": account.username,
                "password": password,
                "access_mode": "temporary_password",
            }
            request.session.pop("new_user_credentials_views", None)
            return redirect("user_created")
        elif action == "resend_invitation":
            if account.must_change_password:
                raise ValidationError(
                    "This account uses a temporary password. Reset the password instead."
                )
            resend_invitation(account, actor=request.user, request=request)
            messages.success(request, f"Invitation sent to {account.email}.")
        elif action == "link_login":
            student = getattr(account, "student", None)
            if student is None:
                raise ValidationError("This account is not linked to a student record.")
            result = attach_login(
                student,
                actor=request.user,
                request=request,
                access_mode=request.POST.get("access_mode", "invitation"),
            )
            if result.password:
                request.session["new_user_credentials"] = {
                    "user_id": account.pk,
                    "username": account.username,
                    "password": result.password,
                    "access_mode": result.access_mode,
                }
                request.session.pop("new_user_credentials_views", None)
                return redirect("user_created")
            messages.success(request, "Login linked and invitation sent.")
        else:
            messages.error(request, "Choose an action.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("user_detail", pk=account.pk)


@login_required
@permission_required_code("admin.manage_users")
def user_link_parent(request):
    _guard(request)
    inst = _institution(request)
    form = LinkParentForm(request.POST or None, institution=inst, user=request.user)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            link_parent(
                data["parent"],
                data["student"],
                relationship=data.get("relationship", ""),
                can_view_finance=data.get("can_view_finance", False),
                actor=request.user,
                request=request,
            )
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        else:
            messages.success(request, "Parent linked to the student.")
            return redirect("user_list")
    return render(request, "accounts/link_parent.html", {"inst": inst, "form": form})


@login_required
@permission_required_code("admin.manage_users")
def user_duplicates(request):
    _guard(request)
    inst = _institution(request)
    form = DuplicateForm(request.POST or None, institution=inst, user=request.user)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            if data.get("merge"):
                merge_users(data["primary"], data["duplicate"],
                            actor=request.user, request=request)
                messages.success(request, "Accounts merged where it was safe.")
            else:
                flag_duplicate(data["duplicate"], data["primary"],
                               actor=request.user, request=request)
                messages.success(request, "Duplicate flagged. No history was deleted.")
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        else:
            return redirect("user_list")
    duplicates = User.objects.filter(
        institution=inst, duplicate_of__isnull=False
    ).select_related("duplicate_of")
    return render(request, "accounts/duplicates.html", {
        "inst": inst,
        "form": form,
        "duplicates": duplicates,
    })


def _signup_link_in_institution(request, pk):
    inst = _institution(request)
    return get_object_or_404(
        SignupLink.objects.select_related("institution"),
        pk=pk,
        institution=inst,
    )


@login_required
@permission_required_code("admin.manage_users")
def signup_link_list(request):
    """Create and review the shareable sign-up links of this institution."""
    _guard(request)
    inst = _institution(request)
    if request.method == "POST":
        expires_at = None
        expires_on = (request.POST.get("expires_on") or "").strip()
        if expires_on:
            parsed = parse_date(expires_on)
            if parsed is None:
                messages.error(request, "Enter the expiry date as YYYY-MM-DD.")
                return redirect("signup_link_list")
            expires_at = timezone.make_aware(datetime.combine(parsed, time(23, 59, 59)))
        max_uses = (request.POST.get("max_uses") or "").strip() or None
        try:
            create_signup_link(
                inst,
                label=request.POST.get("label", ""),
                kind=request.POST.get("kind", SignupLink.Kind.ANY),
                position=request.POST.get("position", ""),
                expires_at=expires_at,
                max_uses=max_uses,
                actor=request.user,
                request=request,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(
                request,
                "Sign-up link created. Copy the link and share it with the people "
                "you want to invite.",
            )
        return redirect("signup_link_list")

    from .registration import POSITION_ORDER, STAFF_POSITIONS

    links = list(
        SignupLink.objects.filter(institution=inst)
        .select_related("created_by")
        .prefetch_related("uses")
    )
    for link in links:
        link.share_url = link.url(request)
    return render(request, "accounts/signup_links.html", {
        "inst": inst,
        "links": links,
        "kind_choices": SignupLink.Kind.choices,
        "positions": [
            {"code": code, "label": STAFF_POSITIONS[code].label}
            for code in POSITION_ORDER
            if code not in ("administrator", "principal")
        ],
        "public_registration": inst.public_registration,
    })


@login_required
@permission_required_code("admin.manage_users")
def signup_link_action(request, pk):
    """Revoke, reactivate or delete one sign-up link."""
    _guard(request)
    link = _signup_link_in_institution(request, pk)
    if request.method != "POST":
        return redirect("signup_link_list")
    action = request.POST.get("action", "")
    try:
        if action == "revoke":
            revoke_signup_link(link, actor=request.user, request=request)
            messages.success(request, "The link no longer works.")
        elif action == "reactivate":
            reactivate_signup_link(link, actor=request.user, request=request)
            messages.success(request, "The link works again.")
        elif action == "delete":
            label = link.label or "Sign-up link"
            delete_signup_link(link, actor=request.user, request=request)
            messages.success(request, f"{label} was deleted.")
        else:
            messages.error(request, "Choose an action.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("signup_link_list")


class ForcePasswordChangeView(PasswordChangeView):
    """The screen a temporary-password account must pass through first."""

    template_name = "accounts/password_change.html"
    form_class = StrongPasswordChangeForm
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        user.must_change_password = False
        # The person has now chosen their own password, so the dashboard no
        # longer needs to warn about the deployment-supplied one.
        user.password_set_from_env = False
        user.save(update_fields=["must_change_password", "password_set_from_env"])
        messages.success(self.request, "Your new password is saved.")
        return response


def accept_invitation_view(request, token):
    """Choose a password from a one-time invitation link."""
    invitation = get_object_or_404(
        UserInvitation.objects.select_related("user", "user__institution"),
        token=token,
    )
    error = ""
    if request.method == "POST":
        password = request.POST.get("password", "")
        confirm = request.POST.get("password_confirm", "")
        if not invitation.is_open:
            error = "This invitation has already been used or has expired."
        elif password != confirm:
            error = "The two passwords do not match."
        else:
            try:
                accept_invitation(invitation, password, request=request)
            except ValidationError as exc:
                error = " ".join(exc.messages)
            else:
                messages.success(
                    request,
                    "Your password is set. Sign in to continue.",
                )
                return redirect("login")
    return render(request, "accounts/invitation_accept.html", {
        "invitation": invitation,
        "account": invitation.user,
        "error": error,
    })
