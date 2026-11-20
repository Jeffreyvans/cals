"""Template helpers for an institution's logo and initials.

The sidebar, the public institution page, receipts, statements and emails all
show the same logo. A page asks for it with::

    {% load branding %}
    {% institution_logo institution 160 as logo_url %}
    {% if logo_url %}<img src="{{ logo_url }}" alt="">{% endif %}
    {% else %}{{ institution.initials }}{% endif %}

When there is no logo the templates fall back to the institution's initials,
so a school that has not uploaded one still gets a recognisable mark.
"""

from django import template

from config.brand import values as brand_values
from core.branding import primary_colour, readable_text
from core.document_header import header_context
from core.logo import institution_logo_png_url, institution_logo_url

register = template.Library()


# --- platform brand --------------------------------------------------------
# These tags read config.brand so no template needs the product name typed into
# it. They work without a request too, which matters for HTML emails rendered
# with render_to_string().


@register.simple_tag
def brand_value(name):
    """Any brand value by its key, for example ``{% brand_value 'copyright' %}``."""
    return brand_values().get(str(name).strip().lower(), "")


@register.simple_tag
def brand_name():
    """The product name (Vanpere by default)."""
    return brand_values()["product_name"]


@register.simple_tag
def brand_company():
    """The publisher name (Vanpere Digital Solutions by default)."""
    return brand_values()["company_name"]


@register.simple_tag
def brand_tagline():
    return brand_values()["tagline"]


@register.simple_tag
def brand_copyright():
    return brand_values()["copyright"]


@register.simple_tag
def brand_support_email():
    return brand_values()["support_email"]


@register.simple_tag
def brand_support_phone():
    return brand_values()["support_phone"]


@register.simple_tag
def brand_support_phone_display():
    return brand_values()["support_phone_display"]


@register.simple_tag
def brand_website():
    return brand_values()["website"]


@register.simple_tag
def brand_logo():
    """Bundled or platform-admin uploaded public wordmark path."""
    return brand_values()["logo_public"]


@register.simple_tag
def brand_favicon():
    return brand_values()["favicon"]


@register.simple_tag
def brand_powered_by():
    return brand_values()["powered_by"]


@register.simple_tag
def brand_via():
    return brand_values()["via_brand"]


@register.simple_tag
def brand_via_for(institution=None):
    """The ``via Brand`` suffix, or '' for a white-label institution email."""
    if institution is not None and not getattr(institution, "show_platform_branding", True):
        return ""
    return brand_values()["via_brand"]


def _asset_url(value):
    """A full URL for a bundled static asset or an uploaded media file."""
    if not value:
        return ""
    if value.startswith(("http://", "https://", "/")):
        return value
    from django.templatetags.static import static

    return static(value)


@register.simple_tag
def brand_favicon_url():
    return _asset_url(brand_values()["favicon"])


@register.simple_tag
def brand_logo_url():
    return _asset_url(brand_values()["logo_public"])


@register.simple_tag
def institution_logo(institution, size=160):
    """The URL of the institution's logo at ``size``, or an empty string."""
    return institution_logo_url(institution, size)


@register.simple_tag
def institution_logo_png(institution):
    """The URL of the PNG fallback logo, or an empty string."""
    return institution_logo_png_url(institution)


@register.simple_tag
def institution_initials(institution):
    """Up to two initials, used when there is no logo."""
    if institution is None:
        return "?"
    return institution.initials()


@register.simple_tag
def institution_brand_colour(institution):
    """The institution's primary colour, or the platform default."""
    return primary_colour(institution)


@register.simple_tag
def institution_brand_text(institution):
    """Text that stays readable on the institution's primary colour."""
    return readable_text(primary_colour(institution))


@register.inclusion_tag("core/_document_header.html")
def document_masthead(institution, layout=None, size=160):
    """The shared logo/name/address/VAT header for a printable screen."""
    return header_context(institution, layout=layout or None, size=size)
