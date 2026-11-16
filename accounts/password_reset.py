"""Password reset emails delivered through the notification pipeline.

Django's stock view sends mail directly. This form keeps the same token flow
(single use, expiring) but queues the message in OutboundMessage, builds the
link from SITE_URL and gives the message an HTML part and a themed sender.
"""

from django.contrib import messages
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import PasswordResetView
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.notifications import (
    EMAIL_FAILURE_MESSAGE,
    absolute_url,
    email_rate_limited,
    queue_message,
    record_email_attempt,
    render_email_html,
    send_message,
)


class EduSpherePasswordResetForm(PasswordResetForm):
    def save(self, request=None, **kwargs):
        for user in self.get_users(self.cleaned_data["email"]):
            uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            link = absolute_url(reverse(
                "password_reset_confirm", kwargs={"uidb64": uidb64, "token": token}
            ))
            institution = getattr(user, "institution", None)
            body = render_to_string("registration/password_reset_email.txt", {
                "user": user, "link": link, "institution": institution,
            })
            html = render_email_html("emails/password_reset.html", {
                "user": user, "link": link, "institution": institution,
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
            )
            send_message(message)
            if request is not None and getattr(message, "status", "") != "sent":
                messages.error(request, EMAIL_FAILURE_MESSAGE)


class EduSpherePasswordResetView(PasswordResetView):
    form_class = EduSpherePasswordResetForm

    def post(self, request, *args, **kwargs):
        email = (request.POST.get("email") or "").strip()
        if email_rate_limited("reset", email, request=request):
            # Always look like a successful request; the rate limit must not
            # reveal whether an account exists.
            messages.error(request, EMAIL_FAILURE_MESSAGE)
            from django.shortcuts import redirect
            return redirect("password_reset_done")
        record_email_attempt("reset", email, request=request)
        return super().post(request, *args, **kwargs)
