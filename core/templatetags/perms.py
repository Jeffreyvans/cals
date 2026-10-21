"""Template tags for permissions and feature flags.

Permissions are codes such as ``finance.receive_payment``. Django's template
language cannot use a dot inside a variable name, so the codes are exposed
twice:

* as a flat list of strings, for ``{% if "finance.receive_payment" in perms %}``
* as a nested dictionary, for ``{% if perms_code.finance.receive_payment %}``

Both tags need the request, because that is who holds the permission::

    {% load perms %}
    {% perms request as my_perms %}
    {% if "finance.receive_payment" in my_perms %}...{% endif %}
"""

from django import template

register = template.Library()


def _permission_dict(codes):
    """Turn ``finance.receive_payment`` into ``{"finance": {"receive_payment": True}}``."""
    tree = {}
    for code in codes:
        parts = code.split(".")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = True
    return tree


@register.simple_tag
def perms(request):
    """Every permission code the signed-in user holds."""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    from core.access import permission_codes_for

    return permission_codes_for(user)


@register.simple_tag
def perms_code(request):
    """The same permissions as a nested dictionary for dotted lookups."""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return {}
    from core.access import permission_codes_for

    return _permission_dict(permission_codes_for(user))


@register.simple_tag
def has_perm(request, code):
    """True when the signed-in user holds this permission code."""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    from core.access import user_has_code

    return user_has_code(user, code)


@register.simple_tag
def modules(request):
    """The module codes this institution has switched on."""
    from core.modules import enabled_modules

    institution = getattr(request, "institution", None)
    if institution is None:
        user = getattr(request, "user", None)
        institution = getattr(user, "institution", None)
    return enabled_modules(institution)


@register.simple_tag
def module_on(request, code):
    """True when the institution has this module switched on."""
    from core.modules import is_enabled

    institution = getattr(request, "institution", None)
    return is_enabled(code, institution)


@register.simple_tag
def current_institution(request):
    """The institution this request belongs to."""
    return getattr(request, "institution", None)
