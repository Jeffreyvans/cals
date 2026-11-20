"""Public, signed-out pages: the learn pages, the institution profile and SEO.

These pages use the platform's all-brown public theme
(``static/css/public.css``). An institution's logo, name, tagline, About text
and contact details appear on its own page, but the page colours always stay
brown: an institution's primary and accent colours are reserved for receipts,
documents and the signed-in application, so a public page can never publish an
unreadable or off-palette page.
"""
import json

from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse

from accounts.forms import DemoRequestForm
from accounts.registration import (
    CONTACT_EMAIL,
    CONTACT_PHONE,
    CONTACT_WHATSAPP,
    contact_rate_limited,
    contact_recipient,
    record_contact_attempt,
)
from accounts.signup_links import open_signup_links
from config.brand import COLOUR_PUBLIC, PRODUCT_NAME
from config.brand import values as brand_values
from core.email_types import DEMO_REQUEST
from core.learn import LEARN_BY_SLUG, LEARN_PAGES
from core.logo import absolute_logo_url
from core.models import Institution
from core.notifications import EMAIL_FAILURE_MESSAGE, send_platform_email

# Routes that belong to the signed-in application. They are kept out of search
# results; a few public check pages inside those prefixes are allowed back in.
ROBOTS_DISALLOW = (
    "/admin/",
    "/api/",
    "/settings/",
    "/users/",
    "/dashboard/",
    "/students/",
    "/years/",
    "/levels/",
    "/promotion/",
    "/classes/",
    "/courses/",
    "/timetable/",
    "/attendance/",
    "/marks/",
    "/results/",
    "/reports/",
    "/search/",
    "/notifications/",
    "/audit/",
    "/imports/",
    "/setup/",
    "/logout/",
    "/login/",
    "/password-reset/",
    "/reset/",
    "/register/",
    "/join/",
    "/verify-email/",
    "/invitation/",
    "/password-change/",
    "/finance/",
    "/fiscalisation/",
    "/admissions/",
    "/lms/",
    "/office/",
    "/library/",
    "/health/",
    "/counselling/",
    "/student-affairs/",
    "/sports/",
    "/boarding/",
    "/transport/",
    "/catering/",
    "/careers/",
    "/examinations/",
    "/research/",
    "/labs/",
    "/ict/",
)

ROBOTS_ALLOW = (
    "/static/",
    "/media/",
    "/admissions/apply/",
    "/admissions/track/",
    "/admissions/offer/",
    "/admissions/verify/",
    "/finance/verify/",
    "/verify/",
)


def learn_index(request):
    """A short public overview with a link to every learn page."""
    return render(request, "public/learn.html", {"learn_pages": LEARN_PAGES})


def about(request):
    """Plain-English information about the product and its publisher.

    No statistics, client names, awards or testimonials: the page says what the
    platform is, who publishes it and how to get in touch.
    """
    return render(request, "public/about.html", {"learn_pages": LEARN_PAGES})


def manifest(request):
    """PWA manifest generated from the brand values.

    The checked-in ``static/manifest.json`` is a placeholder for tools that read
    the file directly. Browsers use this route, so a rename is one edit in
    ``config/brand.py`` and the installed app name changes too.
    """
    brand = brand_values()
    data = {
        "name": brand["product_name"],
        "short_name": brand["product_name"],
        "description": f"{brand['tagline']}.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": COLOUR_PUBLIC,
        "theme_color": COLOUR_PUBLIC,
        "icons": [
            {
                "src": static("img/brand/icon-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": static("img/brand/icon-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ],
    }
    response = JsonResponse(data)
    response["Cache-Control"] = "public, max-age=3600"
    return response


def learn_alias(request, slug):
    """Send the old ``/learn/<slug>/`` address to the canonical top-level URL.

    The canonical URL is the short one (``/features/``), so a link that uses
    the older prefix is redirected rather than served twice.
    """
    page = LEARN_BY_SLUG.get(slug)
    if page is None:
        raise Http404("Unknown learn page")
    return redirect(reverse(page["name"]), permanent=True)


def learn_page(request, slug):
    """One plain-English learn page at its clean top-level URL."""
    page = LEARN_BY_SLUG.get(slug)
    if page is None:
        raise Http404("Unknown learn page")
    context = {"page": page, "learn_pages": LEARN_PAGES}
    if page.get("faqs"):
        context["faq_json"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": item["q"],
                        "acceptedAnswer": {
                            "@type": "Answer",
                            "text": item["a"],
                        },
                    }
                    for item in page["faqs"]
                ],
            },
            ensure_ascii=False,
        )
    if slug == "pricing-and-contact":
        return _pricing_page(request, context)
    return render(request, "public/learn_page.html", context)


def _pricing_page(request, context):
    """The pricing page also carries the demo request form."""
    context.update(
        {
            "contact_email": CONTACT_EMAIL,
            "contact_phone": CONTACT_PHONE,
            "contact_whatsapp": CONTACT_WHATSAPP,
            "demo_form": DemoRequestForm(),
            "demo_sent": False,
            "demo_rate_limited": False,
        }
    )
    if request.method != "POST":
        return render(request, "public/learn_page.html", context)

    form = DemoRequestForm(request.POST)
    if not form.is_valid():
        context["demo_form"] = form
        return render(request, "public/learn_page.html", context)

    data = form.cleaned_data
    if data.get("website"):
        # A bot filled the hidden field. Pretend it worked and send nothing.
        context["demo_sent"] = True
        return render(request, "public/learn_page.html", context)

    if contact_rate_limited(request):
        context.update({"demo_form": form, "demo_rate_limited": True})
        return render(request, "public/learn_page.html", context, status=429)

    body = (
        f"New {PRODUCT_NAME} demo request\n\n"
        f"Institution: {data['institution']}\n"
        f"Name: {data['name']}\n"
        f"Email: {data['email']}\n"
        f"Phone: {data.get('phone') or 'Not given'}\n\n"
        f"{data.get('message') or 'No extra notes.'}\n"
    )
    message = send_platform_email(
        f"{PRODUCT_NAME} demo request",
        body,
        contact_recipient(),
        reply_to=[data["email"]],
        email_type=DEMO_REQUEST,
    )
    if getattr(message, "status", "") != "sent":
        messages.error(request, EMAIL_FAILURE_MESSAGE)
    record_contact_attempt(request)
    context.update({"demo_form": DemoRequestForm(), "demo_sent": True})
    return render(request, "public/learn_page.html", context)


def institution_page(request, slug):
    """The public profile of one institution at /i/<slug>/.

    An institution can switch the page off; a switched-off page is a 404, just
    like a slug that never existed.
    """
    institution = Institution.objects.filter(
        is_active=True, public_page_enabled=True, slug=slug
    ).first()
    if institution is None:
        # A slug changed recently: follow the old address to the new one
        # instead of showing a 404 on a printed poster or a shared link.
        from core.models import resolve_institution_slug

        old = resolve_institution_slug(slug)
        if old is None or not old.public_page_enabled:
            raise Http404("Institution not found.")
        return redirect("institution_public", slug=old.slug)
    links = open_signup_links(institution)
    registration_available = institution.public_registration or bool(links)
    show_register = institution.show_register_button and registration_available
    register_url = ""
    if show_register:
        register_url = links[0].path() if links else reverse("register_choice")

    context = {
        "institution": institution,
        "show_register": show_register,
        "hide_register": not show_register,
        "register_url": register_url,
        "registration_available": registration_available,
        "og_image": absolute_logo_url(institution, request, 512),
        "contact_email": CONTACT_EMAIL,
        "contact_phone": CONTACT_PHONE,
        "contact_whatsapp": CONTACT_WHATSAPP,
    }
    return render(request, "public/institution.html", context)


def robots_txt(request):
    """Keep the private application out of search results."""
    lines = ["User-agent: *"]
    lines.extend(f"Disallow: {path}" for path in ROBOTS_DISALLOW)
    lines.extend(f"Allow: {path}" for path in ROBOTS_ALLOW)
    lines.extend(
        [
            "",
            f"Sitemap: {request.build_absolute_uri(reverse('sitemap'))}",
        ]
    )
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")
