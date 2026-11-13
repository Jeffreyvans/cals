"""API key authentication.

A caller sends ``Authorization: Api-Key esk_<prefix>_<secret>`` (a bare
``Bearer`` header works too). The key's institution is made current for the
rest of the request, so every tenant-scoped query sees only that
institution's rows, whatever host or path prefix the request used.
"""

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from core.tenancy import set_current_institution

from .models import ApiKey


class ApiKeyAuthentication(BaseAuthentication):
    keyword = "Api-Key"

    def authenticate(self, request):
        raw = self._raw_key(request)
        if not raw:
            return None
        parts = ApiKey.split(raw)
        if parts is None:
            raise AuthenticationFailed("That API key is not in the expected format.")
        prefix, secret = parts
        key = (
            ApiKey.objects.select_related("user", "institution")
            .filter(prefix=prefix, is_active=True)
            .first()
        )
        if key is None or not key.matches(secret):
            raise AuthenticationFailed("Invalid or revoked API key.")
        user = key.user
        if not user.is_active:
            raise AuthenticationFailed("This account is not active.")
        if user.institution_id != key.institution_id:
            raise AuthenticationFailed("The key and its user belong to different institutions.")

        # The key, not the host, decides the tenant for API calls.
        request.institution = key.institution
        set_current_institution(key.institution)
        ApiKey.objects.filter(pk=key.pk).update(last_used=timezone.now())
        request.api_key = key
        return (user, key)

    def _raw_key(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if header:
            scheme, _, value = header.partition(" ")
            if scheme.lower() in ("api-key", "apikey", "bearer", "token") and value.strip():
                return value.strip()
        return request.META.get("HTTP_X_API_KEY", "").strip()

    def authenticate_header(self, request):
        return "Api-Key"
