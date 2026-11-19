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
            return User.objects.none()
        active_users = User._default_manager.filter(is_active=True)
        if "@" in identifier:
            users = active_users.filter(email__iexact=normalize_email(identifier))
            users = users | active_users.filter(username__iexact=identifier)
        else:
            users = active_users.filter(username__iexact=identifier)
            # Also allow lookup by email value even if entered oddly; usernames won't contain spaces.
            users = users | active_users.filter(email__iexact=identifier.lower())
        seen = set()
        for user in users.distinct().iterator():
            if user.has_usable_password() and user.pk not in seen:
                seen.add(user.pk)
                yield user

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
                subject="Reset your EduSphere password",
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


def send_password_reset_confirmation_notice(user):
    """Tell the account owner that a reset link was actually used."""
    if not getattr(user, "email", ""):
        return None
    return send_email(
        PASSWORD_RESET_CONFIRMATION,
        user.email,
        "Your EduSphere password was reset",
        (
            f"Hello {user.get_full_name() or user.username},\n\n"
            "Your EduSphere password has been changed using the one-time reset "
            "link. You can now sign in with the new password.\n\n"
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
    return send_email(
        kind,
        user.email,
        "Your EduSphere password was changed",
        (
            f"Hello {user.get_full_name() or user.username},\n\n"
            "The password on your EduSphere account has been changed.\n\n"
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
