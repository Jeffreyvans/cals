"""Template helpers for public media."""

from django import template
from django.contrib.staticfiles import finders

register = template.Library()


@register.filter
def static_exists(path):
    """True when a static file is actually present on disk.

    ``{% static %}`` normally returns a URL such as ``/static/img/x.jpg``, but
    when ``STATIC_URL`` is relative it can return an absolute filesystem path.
    Both shapes are reduced to the path below the static directory before the
    staticfiles finders look for it.
    """
    if not path:
        return False

    text = str(path).replace("\\", "/")
    marker = "static/"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = text.lstrip("/")
    try:
        return finders.find(text) is not None
    except Exception:
        return False
