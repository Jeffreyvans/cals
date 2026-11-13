"""Force a temporary-password account to choose a new password first."""

from django.shortcuts import redirect
from django.urls import reverse

ALLOWED_PREFIXES = (
    "/login/",
    "/logout/",
    "/password-change/",
    "/password-reset/",
    "/reset/",
    "/invitation/",
    "/static/",
    "/media/",
)


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
            and not request.path.startswith(ALLOWED_PREFIXES)
        ):
            return redirect(f"{reverse('password_change')}?next={request.path}")
        return self.get_response(request)
