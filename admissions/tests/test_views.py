from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.factories import StudentFactory
from accounts.models import User
from admissions.factories import (
    AlumniRecordFactory,
    ApplicationFactory,
    CertificateFactory,
    EntryRequirementFactory,
    GraduationClearanceFactory,
    OfferFactory,
    StudentCardFactory,
)
from admissions.models import Offer


class StaffPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "admissions-admin", password="pw123456", role="admin",
            is_staff=True, is_superuser=True)
        cls.application = ApplicationFactory()
        cls.offer = OfferFactory(application=cls.application)
        cls.student = StudentFactory(status="enrolled")
        cls.graduated = StudentFactory(status="graduated")
        GraduationClearanceFactory(student=cls.graduated)
        cls.certificate = CertificateFactory(student=cls.graduated)
        cls.card = StudentCardFactory(student=cls.student)
        cls.alumni = AlumniRecordFactory()
        EntryRequirementFactory(programme=cls.application.programme)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_staff_list_and_detail_pages_render(self):
        urls = [
            reverse("admissions:application_list"),
            reverse("admissions:application_detail", args=[self.application.pk]),
            reverse("admissions:offer_list"),
            reverse("admissions:offer_detail", args=[self.offer.pk]),
            reverse("admissions:entry_requirements"),
            reverse("admissions:entry_requirement_edit",
                    args=[self.application.programme.pk]),
            reverse("admissions:lifecycle_list"),
            reverse("admissions:lifecycle_detail", args=[self.student.pk]),
            reverse("admissions:graduation_list"),
            reverse("admissions:graduation_detail", args=[self.graduated.pk]),
            reverse("admissions:certificate_list"),
            reverse("admissions:certificate_detail", args=[self.certificate.pk]),
            reverse("admissions:id_cards"),
            reverse("admissions:alumni_list"),
            reverse("admissions:alumni_detail", args=[self.alumni.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_pdf_endpoints_return_pdf(self):
        urls = [
            reverse("admissions:offer_pdf", args=[self.offer.pk]),
            reverse("admissions:certificate_pdf", args=[self.certificate.pk]),
            reverse("admissions:id_card_pdf", args=[self.card.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/pdf")
                self.assertTrue(response.content.startswith(b"%PDF"))

    def test_bulk_card_print_can_be_empty(self):
        response = self.client.post(reverse("admissions:id_card_bulk"),
                                    {"student": [self.student.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_staff_page_requires_a_login(self):
        self.client.logout()
        response = self.client.get(reverse("admissions:application_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])


class PublicOfferAcceptanceTests(TestCase):
    def setUp(self):
        self.application = ApplicationFactory()
        self.offer = OfferFactory(application=self.application)

    def test_offer_acceptance_page_renders(self):
        response = self.client.get(
            reverse("admissions:offer_accept",
                    args=[self.application.tracking_token]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.application.name)

    def test_accepting_online_shows_the_login_details(self):
        response = self.client.post(
            reverse("admissions:offer_accept",
                    args=[self.application.tracking_token]), {
                "accepted_by_name": "Tendai Moyo",
                "deposit_method": Offer.DepositMethod.ONLINE,
                "deposit_reference": "PAY-1",
                "agree": "on",
            })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Temporary password")
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, Offer.Status.ACCEPTED)

    def test_expired_offer_page_explains_the_problem(self):
        self.offer.acceptance_deadline = timezone.localdate() - timedelta(days=1)
        self.offer.save(update_fields=["acceptance_deadline"])
        response = self.client.post(
            reverse("admissions:offer_accept",
                    args=[self.application.tracking_token]), {
                "accepted_by_name": "Tendai Moyo",
                "deposit_method": Offer.DepositMethod.RECORDED,
                "agree": "on",
            })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "deadline has passed")
