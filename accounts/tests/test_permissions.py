from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import Course, Programme, Student
from finance.models import Invoice, Payment

User = get_user_model()


class BaseAccountsData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("root", password="pw123456", role="admin",
                                              is_staff=True, is_superuser=True)
        cls.registrar = User.objects.create_user("reg", password="pw123456", role="registrar")
        cls.bursar = User.objects.create_user("bur", password="pw123456", role="bursar")
        cls.lecturer = User.objects.create_user("lec", password="pw123456", role="lecturer")
        cls.lecturer2 = User.objects.create_user("lec2", password="pw123456", role="lecturer")

        cls.programme = Programme.objects.create(name="Diploma in IT", code="DIT",
                                                  level="diploma", tuition_fee=500)
        cls.student_user = User.objects.create_user("stu1", password="pw123456", role="student")
        cls.student = Student.objects.create(user=cls.student_user, programme=cls.programme,
                                             student_no="S0001")
        cls.other_user = User.objects.create_user("stu2", password="pw123456", role="student")
        cls.other = Student.objects.create(user=cls.other_user, programme=cls.programme,
                                           student_no="S0002")

        cls.own_invoice = Invoice.objects.create(student=cls.student, description="Tuition",
                                                 amount=200, due_date=date.today())
        cls.own_payment = Payment.objects.create(invoice=cls.own_invoice, amount=50,
                                                 received_by=cls.bursar)
        cls.other_invoice = Invoice.objects.create(student=cls.other, description="Tuition",
                                                   amount=300, due_date=date.today())
        cls.other_payment = Payment.objects.create(invoice=cls.other_invoice, amount=300,
                                                   received_by=cls.bursar)

        cls.course = Course.objects.create(code="IT101", title="Programming",
                                           programme=cls.programme, lecturer=cls.lecturer)
        cls.other_course = Course.objects.create(code="IT102", title="Networking",
                                                 programme=cls.programme, lecturer=cls.lecturer2)

    def login(self, user):
        self.client.force_login(user)


class AnonymousAccessTests(BaseAccountsData):
    def test_anonymous_users_are_sent_to_login(self):
        urls = [
            reverse("dashboard"),
            reverse("student_search"),
            reverse("student_search_api"),
            reverse("student_create"),
            reverse("lecturer_portal"),
            reverse("student_portal"),
            reverse("student_detail", args=[self.student.pk]),
            reverse("course_roster", args=[self.course.pk]),
            reverse("record_payment", args=[self.student.pk]),
            reverse("create_invoice", args=[self.student.pk]),
            reverse("receipt", args=[self.own_payment.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 302)
                self.assertIn(reverse("login"), resp["Location"])


class StudentPermissionTests(BaseAccountsData):
    """A student must never see another student's data."""

    def setUp(self):
        self.login(self.student_user)

    def test_student_cannot_open_staff_pages(self):
        forbidden = [
            reverse("student_search"),
            reverse("student_search_api"),
            reverse("student_create"),
            reverse("lecturer_portal"),
            reverse("student_detail", args=[self.other.pk]),
            reverse("course_roster", args=[self.course.pk]),
            reverse("record_payment", args=[self.other.pk]),
            reverse("create_invoice", args=[self.other.pk]),
        ]
        for url in forbidden:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_dashboard_sends_student_to_own_portal(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertRedirects(resp, reverse("student_portal"))

    def test_student_portal_shows_own_record_only(self):
        resp = self.client.get(reverse("student_portal"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("S0001", content)
        self.assertNotIn("S0002", content)
        self.assertNotIn(self.other.name, content)

    def test_student_can_read_own_receipt_only(self):
        own = self.client.get(reverse("receipt", args=[self.own_payment.pk]))
        self.assertEqual(own.status_code, 200)
        self.assertContains(own, self.own_payment.receipt_no)
        other = self.client.get(reverse("receipt", args=[self.other_payment.pk]))
        self.assertEqual(other.status_code, 403)

    def test_student_cannot_search_other_students(self):
        resp = self.client.get(reverse("student_search_api"), {"q": "S0002"})
        self.assertEqual(resp.status_code, 403)


class LecturerPermissionTests(BaseAccountsData):
    def setUp(self):
        self.login(self.lecturer)

    def test_lecturer_sees_own_portal_and_roster(self):
        self.assertEqual(self.client.get(reverse("lecturer_portal")).status_code, 200)
        self.assertEqual(self.client.get(reverse("course_roster", args=[self.course.pk])).status_code, 200)

    def test_lecturer_cannot_open_someone_elses_roster(self):
        self.assertEqual(self.client.get(reverse("course_roster", args=[self.other_course.pk])).status_code, 403)

    def test_lecturer_cannot_post_to_someone_elses_roster(self):
        resp = self.client.post(reverse("course_roster", args=[self.other_course.pk]), {"mark_1": "80"})
        self.assertEqual(resp.status_code, 403)

    def test_lecturer_cannot_touch_finance_or_registration(self):
        for url in [reverse("record_payment", args=[self.student.pk]),
                    reverse("create_invoice", args=[self.student.pk]),
                    reverse("student_create"),
                    reverse("student_search")]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_lecturer_sees_no_fee_information(self):
        resp = self.client.get(reverse("student_detail", args=[self.student.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Remaining balance")
        self.assertNotContains(resp, "Tuition")


class RegistrarPermissionTests(BaseAccountsData):
    def setUp(self):
        self.login(self.registrar)

    def test_registrar_can_manage_students(self):
        self.assertEqual(self.client.get(reverse("student_search")).status_code, 200)
        self.assertEqual(self.client.get(reverse("student_create")).status_code, 200)
        self.assertEqual(self.client.get(reverse("student_detail", args=[self.student.pk])).status_code, 200)

    def test_registrar_cannot_receive_payments(self):
        for url in [reverse("record_payment", args=[self.student.pk]),
                    reverse("create_invoice", args=[self.student.pk]),
                    reverse("lecturer_portal")]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_registrar_can_view_but_not_edit_marks(self):
        self.assertEqual(self.client.get(reverse("course_roster", args=[self.course.pk])).status_code, 200)
        resp = self.client.post(reverse("course_roster", args=[self.course.pk]), {"mark_1": "77"})
        self.assertEqual(resp.status_code, 403)


class BursarPermissionTests(BaseAccountsData):
    def setUp(self):
        self.login(self.bursar)

    def test_bursar_can_work_with_fees(self):
        self.assertEqual(self.client.get(reverse("student_search")).status_code, 200)
        self.assertEqual(self.client.get(reverse("record_payment", args=[self.student.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("create_invoice", args=[self.student.pk])).status_code, 200)

    def test_bursar_cannot_register_students_or_enter_marks(self):
        for url in [reverse("student_create"),
                    reverse("lecturer_portal"),
                    reverse("course_roster", args=[self.course.pk])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)


class AdminPermissionTests(BaseAccountsData):
    def setUp(self):
        self.login(self.admin)

    def test_admin_can_open_every_portal(self):
        for url in [reverse("dashboard"), reverse("student_search"), reverse("student_create"),
                    reverse("lecturer_portal"), reverse("course_roster", args=[self.course.pk]),
                    reverse("record_payment", args=[self.student.pk]),
                    reverse("create_invoice", args=[self.student.pk]),
                    reverse("receipt", args=[self.other_payment.pk])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
