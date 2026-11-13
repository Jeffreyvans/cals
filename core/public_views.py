"""Public, signed-out pages: the institution profile and the learn hub.

These pages use the platform's brown public theme (``static/css/public.css``).
An institution's logo, name, tagline and About text appear, but its primary
and accent colours are deliberately not applied here: those stay for the
signed-in application, receipts and documents.
"""

from django.shortcuts import get_object_or_404, render

from core.models import Institution


def institution_page(request, slug):
    """The public profile of one active institution at /i/<slug>/."""
    institution = get_object_or_404(
        Institution.objects.filter(is_active=True), slug=slug
    )
    return render(
        request,
        "public/institution.html",
        {"institution": institution},
    )


def learn_index(request):
    """A short public overview of the learning platform."""
    return render(request, "public/learn.html", {})
