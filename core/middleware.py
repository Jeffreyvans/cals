"""Resolve the institution for this request and make it the current one.

The institution comes from the signed-in user, or from the subdomain, or from
a path prefix such as ``/s/acme/``. When nothing matches, the default
institution is used.
"""


from core.tenancy import default_institution_id, reset_current_institution, set_current_institution


def institution_from_request(request):
    """The institution this request belongs to, or None."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        institution = getattr(user, "institution", None)
        if institution is not None:
            return institution

    host = request.get_host().split(":")[0]
    parts = host.split(".")
    if len(parts) > 2:
        from core.models import Institution

        candidate = parts[0].lower()
        inst = Institution.objects.filter(slug=candidate, is_active=True).first()
        if inst is not None:
            return inst

    path = request.path.strip("/")
    if path:
        prefix = path.split("/")[0]
        if prefix:
            from core.models import Institution

            inst = Institution.objects.filter(slug=prefix.lower(), is_active=True).first()
            if inst is not None:
                return inst
    return None


class InstitutionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        institution = institution_from_request(request)
        request.institution = institution
        # Always scope the request explicitly. Public pages have no institution
        # of their own, so they read the installation's default institution
        # rather than every institution.
        token = set_current_institution(
            institution if institution is not None else default_institution_id()
        )
        try:
            return self.get_response(request)
        finally:
            reset_current_institution(token)
