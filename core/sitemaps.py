"""The public sitemap.

Only pages meant for search engines are listed: the platform page, the learn
pages, the privacy notice and institutions that have their public page switched
on. Signed-in screens, registration forms and token links are never included.
"""

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from core.learn import LEARN_PAGES
from core.models import Institution


class PlatformSitemap(Sitemap):
    changefreq = "monthly"

    def items(self):
        return ["landing", "learn_index", "privacy_notice"]

    def location(self, name):
        return reverse(name)

    def priority(self, name):
        return 1.0 if name == "landing" else 0.4


class LearnSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.8

    def items(self):
        return [page["name"] for page in LEARN_PAGES]

    def location(self, name):
        return reverse(name)


class InstitutionSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.6

    def items(self):
        return Institution.objects.filter(
            is_active=True, public_page_enabled=True
        ).order_by("name")

    def location(self, institution):
        return reverse("institution_public", args=[institution.slug])


SITEMAPS = {
    "platform": PlatformSitemap,
    "learn": LearnSitemap,
    "institutions": InstitutionSitemap,
}
