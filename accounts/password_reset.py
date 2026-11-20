"""Password reset emails delivered through the notification pipeline.

Django's stock view sends mail directly. This form keeps the same token flow
(single use, expiring) but queues the message in OutboundMessage, builds the
link from SITE_URL and gives the message an HTML part and a themed sender.
"""

from django import forms
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import PasswordResetConfirmView, PasswordResetView
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from config.brand import PRODUCT_NAME
from core.email_types import (
    FORCED_PASSWORD_CHANGE_NOTICE,
    PASSWORD_CHANGE_NOTICE,
    PASSWORD_RESET_CONFIRMATION,
    PASSWORD_RESET_REQUEST,
)
from core.notifications import (
    absolute_url,
    email_rate_limited,
    queue_message,
    record_email_attempt,
    render_email_html,
    send_email,
    send_message,
)

from .admin_forms import StrongSetPasswordForm
from .models import User
from .usernames import normalize_email, normalize_identifier


class EduSpherePasswordResetForm(PasswordResetForm):
    email = forms.CharField(
        label="Email or username",
        max_length=254,
        widget=forms.TextInput(attrs={"autocomplete": "username", "autocapitalize": "none"}),
    )

    def get_users(self, identifier):
        identifier = normalize_identifier(identifier)
        if not identifier:
            return []
        active_users = User._default_manager.filter(is_active=True)
        if "@" in identifier:
            users = (
                active_users.filter(email__iexact=normalize_email(identifier))
                | active_users.filter(username__iexact=identifier)
            )
        else:
            users = (
                active_users.filter(username__iexact=identifier)
                | active_users.filter(email__iexact=identifier.lower())
            )

        candidates = []
        seen = set()
        for user in users.distinct():
            if user.pk in seen or not user.has_usable_password():
                continue
            seen.add(user.pk)
            candidates.append(user)

        if not candidates:
            return []
        if len(candidates) == 1:
            return candidates

        # The same email can belong to accounts in different institutions. Treat an
        # ambiguous identifier as no match so the response stays non-revealing and
        # the wrong account is never reset; the person can use their username.
        first = candidates[0]
        if any(
            candidate.username.lower() != first.username.lower()
            or candidate.institution_id != first.institution_id
            for candidate in candidates[1:]
        ):
            return []
        return [first]

    def save(self, request=None, **kwargs):
        identifier = self.cleaned_data.get("email", "")
        for user in self.get_users(identifier):
            if not getattr(user, "email", ""):
                continue
            uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            link = absolute_url(reverse(
                "password_reset_confirm", kwargs={"uidb64": uidb64, "token": token}
            ))
            institution = getattr(user, "institution", None)
            login_link = (
                absolute_url(reverse("institution_login", args=[institution.slug]))
                if institution is not None and getattr(institution, "slug", "")
                else absolute_url(reverse("login"))
            )
            body = render_to_string("registration/password_reset_email.txt", {
                "user": user, "link": link, "institution": institution,
                "login_link": login_link,
            })
            html = render_email_html("emails/password_reset.html", {
                "user": user, "link": link, "institution": institution,
                "login_link": login_link,
                "uid": uidb64, "token": token,
            })
            message = queue_message(
                "email",
                user.email,
                body,
                subject=f"Reset your {PRODUCT_NAME} password",
                institution=institution,
                related_object=user,
                html_body=html,
                email_type=PASSWORD_RESET_REQUEST,
            )
            send_message(message)
            # Never surface delivery failures here: that would reveal whether the
            # identifier matched an account. Delivery is recorded on the message.


class EduSpherePasswordResetView(PasswordResetView):
    form_class = EduSpherePasswordResetForm

    def post(self, request, *args, **kwargs):
        identifier = normalize_identifier(request.POST.get("email") or "")
        rate_key = normalize_email(identifier) if "@" in identifier else identifier.lower()
        if email_rate_limited("reset", rate_key, request=request):
            # Always look like a successful request; the rate limit must not
            # reveal whether an account exists.
            from django.shortcuts import redirect

            return redirect("password_reset_done")
        record_email_attempt("reset", rate_key, request=request)
        return super().post(request, *args, **kwargs)


def _login_link_for(user):
    institution = getattr(user, "institution", None)
    if institution is not None and getattr(institution, "slug", ""):
        return absolute_url(reverse("institution_login", args=[institution.slug]))
    return absolute_url(reverse("login"))


def send_password_reset_confirmation_notice(user):
    """Tell the account owner that a reset link was actually used."""
    if not getattr(user, "email", ""):
        return None
    login_link = _login_link_for(user)
    return send_email(
        PASSWORD_RESET_CONFIRMATION,
        user.email,
        f"Your {PRODUCT_NAME} password was reset",
        (
            f"Hello {user.get_full_name() or user.username},\n\n"
            f"Your {PRODUCT_NAME} password has been changed using the one-time reset "
            "link.\n"
            f"Sign in at {login_link} with your email or username {user.username}.\n\n"
            "If you did not do this, contact your institution immediately.\n"
        ),
        institution=getattr(user, "institution", None),
        related_object=user,
    )


def send_password_change_notice(user, forced=False):
    """Notice after a voluntary or forced password change (single central send)."""
    if not getattr(user, "email", ""):
        return None
    kind = FORCED_PASSWORD_CHANGE_NOTICE if forced else PASSWORD_CHANGE_NOTICE
    login_link = _login_link_for(user)
    return send_email(
        kind,
        user.email,
        f"Your {PRODUCT_NAME} password was changed",
        (
            f"Hello {user.get_full_name() or user.username},\n\n"
            f"The password on your {PRODUCT_NAME} account has been changed.\n"
            f"Sign in at {login_link} with your email or username {user.username}.\n\n"
            "If you did not make this change, contact your institution.\n"
        ),
        institution=getattr(user, "institution", None),
        related_object=user,
    )


class EduSpherePasswordResetConfirmView(PasswordResetConfirmView):
    form_class = StrongSetPasswordForm

    def form_valid(self, form):
        user = form.user
        response = super().form_valid(form)
        send_password_reset_confirmation_notice(user)
        return response
