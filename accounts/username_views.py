"""Server-side support for the optional live username availability check."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .models import User
from .usernames import normalize_identifier, validate_username


@login_required
@require_GET
def username_availability(request):
    """Return whether a username may be used. Never required to submit a form."""
    value = normalize_identifier(request.GET.get("username", ""))
    exclude_user = None
    exclude_pk = request.GET.get("exclude", "")
    if str(exclude_pk).isdigit() and request.user.is_authenticated:
        exclude_user = User.objects.filter(pk=exclude_pk).first()
    if not value:
        return JsonResponse({"available": False, "message": ""})
    try:
        key = validate_username(value, exclude_user=exclude_user)
    except ValidationError as exc:
        return JsonResponse({"available": False, "message": exc.messages[0]})
    return JsonResponse(
        {"available": True, "username": key, "message": "This username is available"}
    )
