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

from core.branding import primary_colour, readable_text
from core.document_header import header_context
from core.logo import institution_logo_png_url, institution_logo_url

register = template.Library()


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
