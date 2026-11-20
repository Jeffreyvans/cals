"""Rendered HTML fallback for emails that did not supply their own template."""
from django.template.loader import render_to_string
from django.utils.html import linebreaks

from config.brand import PRODUCT_NAME


def render_generic_email_html(subject, body, institution=None):
    """Return a themed HTML version of a plain-text email.

    Messages queued without a bespoke template still get the brown platform
    shell and an HTML part, so every email has both text and HTML versions.
    """
    try:
        return render_to_string("emails/generic.html", {
            "subject": subject or f"{PRODUCT_NAME}",
            "body_html": linebreaks(body or ""),
            "institution": institution,
        })
    except Exception:  # pragma: no cover - a broken template must not stop a send
        return ""
