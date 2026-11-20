"""The public learn pages, institution profile, sitemap and robots rules."""

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from accounts.models import SignupLink
from accounts.registration import CONTACT_EMAIL
from config.brand import PRODUCT_NAME
from core.learn import LEARN_PAGES
from core.models import Institution

User = get_user_model()


class LearnPageTests(TestCase):
    def test_the_learn_index_links_to_every_page(self):
        response = self.client.get(reverse("learn_index"))
        self.assertEqual(response.status_code, 200)
        for page in LEARN_PAGES:
            with self.subTest(page=page["slug"]):
                self.assertContains(response, reverse(page["name"]))

    def test_every_learn_page_renders_with_its_own_words(self):
        for page in LEARN_PAGES:
            with self.subTest(page=page["slug"]):
                response = self.client.get(reverse(page["name"]))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, page["heading"])
                self.assertContains(response, escape(page["lead"]))
                self.assertContains(response, "css/public.css")

    def test_every_learn_page_has_metadata_and_a_clean_url(self):
        for page in LEARN_PAGES:
            with self.subTest(page=page["slug"]):
                response = self.client.get(reverse(page["name"]))
                self.assertContains(response, f"<title>{page['title']}</title>")
                self.assertContains(response, escape(page["description"]))
                self.assertContains(response, 'rel="canonical"')
                self.assertContains(response, 'property="og:title"')

    def test_the_old_learn_address_redirects_to_the_clean_one(self):
        response = self.client.get(reverse("learn_alias", args=["features"]))
        self.assertRedirects(
            response, reverse("learn_features"), status_code=301,
            fetch_redirect_response=False,
        )

    def test_an_unknown_learn_page_is_a_404(self):
        self.assertEqual(
            self.client.get(reverse("learn_alias", args=["nope"])).status_code, 404
        )

    def test_the_faq_page_publishes_structured_answers(self):
        response = self.client.get(reverse("learn_faq"))
        self.assertContains(response, "application/ld+json")
        self.assertContains(response, f"What is {PRODUCT_NAME}?")

    def test_the_pricing_page_does_not_invent_a_price(self):
        response = self.client.get(reverse("learn_pricing_and_contact"))
        self.assertContains(response, "do not publish a price list")
        self.assertNotContains(response, "$")

    def test_the_header_links_to_the_learn_pages(self):
        response = self.client.get(reverse("login"))
        for page in LEARN_PAGES:
            with self.subTest(page=page["slug"]):
                self.assertContains(response, reverse(page["name"]))


class DemoRequestTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []

    def post(self, **overrides):
        data = {
            "name": "Head Teacher",
            "institution": "Mavambo College",
            "email": "head@example.test",
            "phone": "",
            "message": "We would like to see fees and results.",
        }
        data.update(overrides)
        return self.client.post(reverse("learn_pricing_and_contact"), data)

    def test_a_demo_request_emails_the_contact_address(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thank you")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [CONTACT_EMAIL])
        self.assertIn("Mavambo College", mail.outbox[0].body)

    def test_the_honeypot_sends_nothing(self):
        response = self.post(website="https://spam.example")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox, [])

    def test_a_bad_email_address_is_refused(self):
        response = self.post(email="not-an-email")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox, [])

    def test_the_demo_form_is_rate_limited(self):
        for _ in range(6):
            self.post()
        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many messages", status_code=429)


class InstitutionPublicPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.institution = Institution.objects.create(
            name="Alpha College",
            slug="alpha",
            type="college",
            tagline="Learn. Serve. Lead.",
            about="A college in Harare.",
            public_registration=True,
            contact_email="office@alpha.test",
            contact_phone="+263771000111",
            primary_color="#123456",
            accent_color="#abcdef",
        )

    def page(self):
        return reverse("institution_public", args=[self.institution.slug])

    def test_the_page_shows_the_sign_in_card_and_powered_by_footer(self):
        response = self.client.get(self.page())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("institution_login", args=["alpha"]))
        self.assertContains(response, "Only accounts belonging to Alpha College")
        self.assertContains(response, f"Powered by {PRODUCT_NAME}")
        self.assertContains(response, reverse("login"))

    def test_the_page_can_be_switched_off(self):
        self.institution.public_page_enabled = False
        self.institution.save(update_fields=["public_page_enabled"])
        self.assertEqual(self.client.get(self.page()).status_code, 404)

    def test_the_register_button_can_be_switched_off(self):
        self.institution.show_register_button = False
        self.institution.save(update_fields=["show_register_button"])
        response = self.client.get(self.page())
        self.assertNotContains(response, reverse("register_choice"))
        self.assertContains(response, reverse("institution_login", args=["alpha"]))

    def test_no_register_button_when_registration_is_closed(self):
        self.institution.public_registration = False
        self.institution.save(update_fields=["public_registration"])
        response = self.client.get(self.page())
        self.assertNotContains(response, reverse("register_choice"))

    def test_an_open_signup_link_offers_registration(self):
        self.institution.public_registration = False
        self.institution.save(update_fields=["public_registration"])
        link = SignupLink.objects.create(
            institution=self.institution, kind=SignupLink.Kind.ANY
        )
        response = self.client.get(self.page())
        self.assertContains(response, link.path())
        self.assertNotContains(response, reverse("register_choice"))

    def test_the_page_does_not_publish_student_records(self):
        User.objects.create_user(
            "student1",
            password="Str0ngPassw0rd",
            role="student",
            institution=self.institution,
            first_name="Tariro",
            last_name="Moyo",
            email="tariro@example.test",
        )
        response = self.client.get(self.page())
        self.assertNotContains(response, "Tariro")
        self.assertNotContains(response, "tariro@example.test")


class SeoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.visible = Institution.objects.create(
            name="Visible College", slug="visible", public_page_enabled=True
        )
        cls.hidden = Institution.objects.create(
            name="Hidden College", slug="hidden", public_page_enabled=False
        )

    def test_the_sitemap_lists_public_pages_only(self):
        response = self.client.get(reverse("sitemap"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for page in LEARN_PAGES:
            self.assertIn(reverse(page["name"]), body)
        self.assertIn(reverse("institution_public", args=["visible"]), body)
        self.assertNotIn(reverse("institution_public", args=["hidden"]), body)
        self.assertNotIn(reverse("login"), body)
        self.assertNotIn(reverse("register_choice"), body)

    def test_robots_keeps_the_private_app_out(self):
        response = self.client.get(reverse("robots_txt"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Disallow: /admin/", body)
        self.assertIn("Disallow: /finance/", body)
        self.assertIn("Disallow: /students/", body)
        self.assertIn(f"Sitemap: http://testserver{reverse('sitemap')}", body)

    def test_the_institution_page_is_indexable_and_private_pages_are_not(self):
        response = self.client.get(
            reverse("institution_public", args=[self.visible.slug])
        )
        self.assertContains(response, 'content="index,follow"')
        login = self.client.get(
            reverse("institution_login", args=[self.visible.slug])
        )
        self.assertContains(login, 'content="noindex,follow"')
