"""Template helpers for categorised inline error messages."""

from django import template

from core.error_catalogue import get_spec

register = template.Library()


@register.filter
def error_spec(message):
    """Return the ErrorSpec for a message tagged ``error-code:CODE``."""
    tags = getattr(message, "extra_tags", "") or ""
    for tag in str(tags).split():
        if tag.startswith("error-code:"):
            return get_spec(tag.split(":", 1)[1])
    return None
