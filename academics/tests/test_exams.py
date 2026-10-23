from datetime import date

from django.test import TestCase
from django.urls import reverse

from academics.models import (
    AcademicYear,
    Course,
    Enrollment,
    ExamSitting,
    Guardian,
    Programme,
    Student,
    Term,
)
from accounts.models import User
from finance.models import Invoice


class BaseExams(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.registrar = User.objects.create_user("reg", password="pw123456",
                                                 role="registrar")
        cls.bursar = User.objects.create_user("bur", password="pw123456", role="bursar")
        cls.lecturer = User.objects.create_user("lec", password="pw123456",
                                                first_name="Lindiwe",
                                                last_name="Ncube", role="lecturer")
        cls.year = AcademicYear.objects.create(name="2026",
                                               start_date=date(2026, 1, 1),
                                               end_date=date(2026, 12, 31),
                                               is_current=True)
        cls.term1 = Term.objects.create(academic_year=cls.year, name="Term 1",
                                        start_date=date(2026, 1, 5),
                                        end_date=date(2026, 4, 10), is_current=True)
        cls.term2 = Term.objects.create(academic_year=cls.year, name="Term 2",
                                        start_date=date(2026, 5, 1),
                                        end_date=date(2026, 8, 10))
        cls.programme = Programme.objects.create(name="Diploma in Computing",
                                                 code="DIC", level="diploma")
        cls.course = Course.objects.create(code="IT101", title="Intro to IT",
                                           programme=cls.programme,
                                           lecturer=cls.lecturer)
        students = [("alice", "Alice", "Adams"), ("bob", "Bob", "Banda"),
                    ("carol", "Carol", "Chanda"), ("dave", "Dave", "Dube")]
        cls.students = {}
        for username, first, last in students:
            u = User.objects.create_user(username, password="pw123456",
                                         first_name=first, last_name=last,
                                         role="student")
            cls.students[username] = Student.objects.create(user=u,
                                                            programme=cls.programme)
        for username in ("alice", "bob", "carol"):
            Enrollment.objects.create(student=cls.students[username],
                                      course=cls.course, term=cls.term1)
        Invoice.objects.create(student=cls.students["bob"], description="Tuition",
                               amount=500, due_date=date(2026, 3, 1))
        parent = User.objects.create_user("mama", password="pw123456", role="parent")
        Guardian.objects.create(parent=parent, student=cls.students["alice"])
        cls.parent = parent


class EnterSittingsTests(BaseExams):
    def test_enter_numbers_students_in_name_order(self):
        made = ExamSitting.enter(self.term1,
                                 Student.objects.filter(status="active"))
        self.assertEqual(made, 4)
        nos = dict(ExamSitting.objects.values_list("student__user__username",
                                                   "exam_no"))
        self.assertEqual(nos["alice"], "TERM1-0001")
        self.assertEqual(nos["bob"], "TERM1-0002")
        self.assertEqual(nos["carol"], "TERM1-0003")
        self.assertEqual(nos["dave"], "TERM1-0004")

    def test_enter_only_touches_students_without_a_sitting(self):
        ExamSitting.enter(self.term1, Student.objects.filter(status="active"))
        first = dict(ExamSitting.objects.values_list("student_id", "exam_no"))
        made = ExamSitting.enter(self.term1, Student.objects.filter(status="active"))
        self.assertEqual(made, 0)
        self.assertEqual(first, dict(
            ExamSitting.objects.values_list("student_id", "exam_no")))

    def test_enter_can_be_limited_to_one_student(self):
        made = ExamSitting.enter(self.term1,
                                 Student.objects.filter(user__username="alice"))
        self.assertEqual(made, 1)
        self.assertEqual(ExamSitting.objects.get().exam_no, "TERM1-0001")

    def test_numbering_skips_used_numbers_after_a_removal(self):
        ExamSitting.enter(self.term1, Student.objects.filter(status="active"))
        ExamSitting.objects.get(student__user__username="bob").delete()
        ExamSitting.enter(self.term1, Student.objects.filter(user__username="bob"))
        self.assertEqual(
            ExamSitting.objects.get(student__user__username="bob").exam_no,
            "TERM1-0002")


class ExamsPageTests(BaseExams):
    def url(self, **params):
        return reverse("exams") + (f"?term={params['term']}" if "term" in params else "")

    def test_registrar_and_bursar_can_open_the_page(self):
        for user in (self.registrar, self.bursar):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                resp = self.client.get(self.url())
                self.assertEqual(resp.status_code, 200)
                self.assertContains(resp, "Enter everyone")

    def test_lecturers_and_students_are_refused(self):
        for user in (self.lecturer, self.students["alice"].user):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_bulk_enter_creates_sittings_then_stops(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(self.url(), {"action": "bulk"}, follow=True)
        self.assertEqual(ExamSitting.objects.count(), 3)
        self.assertFalse(ExamSitting.objects.filter(
            student__user__username="dave").exists())
        self.assertContains(resp, "3 students entered")
        self.client.post(self.url(), {"action": "bulk"})
        self.assertEqual(ExamSitting.objects.count(), 3)

    def test_enter_one_adds_a_single_student(self):
        self.client.force_login(self.registrar)
        self.client.post(self.url(), {"action": "enter_one",
                                      "pk": str(self.students["alice"].pk)})
        sitting = ExamSitting.objects.get()
        self.assertEqual(sitting.student, self.students["alice"])
        self.assertEqual(sitting.exam_no, "TERM1-0001")

    def test_enter_one_refuses_a_student_not_enrolled_this_term(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(self.url(), {"action": "enter_one",
                                             "pk": str(self.students["dave"].pk)},
                                follow=True)
        self.assertEqual(ExamSitting.objects.count(), 0)
        self.assertContains(resp, "not enrolled this term")

    def test_saving_sets_venue_clearance_and_note(self):
        ExamSitting.enter(self.term1, Student.objects.filter(status="active"))
        pk = ExamSitting.objects.get(student__user__username="alice").pk
        self.client.force_login(self.registrar)
        self.client.post(self.url(), {"action": "save", f"venue_{pk}": "Hall B",
                                      f"cleared_{pk}": "on"})
        sitting = ExamSitting.objects.get(pk=pk)
        self.assertEqual(sitting.venue, "Hall B")
        self.assertTrue(sitting.cleared)
        self.client.post(self.url(), {"action": "save", f"venue_{pk}": "Hall B",
                                      f"note_{pk}": "Arrears"})
        sitting.refresh_from_db()
        self.assertFalse(sitting.cleared)
        self.assertEqual(sitting.note, "Arrears")

    def test_remove_deletes_one_sitting(self):
        ExamSitting.enter(self.term1, Student.objects.filter(user__username="alice"))
        pk = ExamSitting.objects.get().pk
        self.client.force_login(self.registrar)
        resp = self.client.post(self.url(), {"action": f"remove_{pk}"}, follow=True)
        self.assertEqual(ExamSitting.objects.count(), 0)
        self.assertContains(resp, "no longer entered")

    def test_bursar_can_enter_students_too(self):
        self.client.force_login(self.bursar)
        self.client.post(self.url(), {"action": "bulk"})
        self.assertEqual(ExamSitting.objects.count(), 3)

    def test_term_picker_switches_terms(self):
        ExamSitting.enter(self.term1, Student.objects.filter(status="active"))
        self.client.force_login(self.registrar)
        resp = self.client.get(self.url(term=self.term2.pk))
        self.assertContains(resp, "Nobody is entered")
        self.assertContains(resp, f"?term={self.term1.pk}")

    def test_staff_nav_links_to_exams(self):
        self.client.force_login(self.registrar)
        self.assertContains(self.client.get(reverse("dashboard")), 'href="/exams/"')


class ExamCardTests(BaseExams):
    def test_student_without_a_sitting_is_told(self):
        self.client.force_login(self.students["alice"].user)
        resp = self.client.get(reverse("my_exam_card"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "not been entered")

    def test_student_card_shows_number_venue_and_exams(self):
        ExamSitting.enter(self.term1, Student.objects.filter(status="active"))
        ExamSitting.objects.update(venue="Hall A")
        self.client.force_login(self.students["alice"].user)
        resp = self.client.get(reverse("my_exam_card"))
        self.assertContains(resp, "TERM1-0001")
        self.assertContains(resp, "Hall A")
        self.assertContains(resp, "Cleared to sit")
        self.assertContains(resp, "IT101")

    def test_blocked_student_card_shows_the_note(self):
        ExamSitting.enter(self.term1, Student.objects.filter(status="active"))
        ExamSitting.objects.filter(student__user__username="bob").update(
            cleared=False, note="Fee balance outstanding")
        self.client.force_login(self.students["bob"].user)
        resp = self.client.get(reverse("my_exam_card"))
        self.assertContains(resp, "Not cleared")
        self.assertContains(resp, "Fee balance outstanding")

    def test_other_students_cannot_open_the_card(self):
        self.client.force_login(self.lecturer)
        self.assertEqual(self.client.get(reverse("my_exam_card")).status_code, 403)

    def test_student_portal_links_to_the_card(self):
        self.client.force_login(self.students["alice"].user)
        resp = self.client.get(reverse("student_portal"))
        self.assertContains(resp, 'href="/me/exam-card/"')

    def test_parent_sees_the_exam_card_section_on_the_child_page(self):
        self.client.force_login(self.parent)
        before = self.client.get(reverse("child_detail",
                                         args=[self.students["alice"].pk]))
        self.assertContains(before, "Not entered for any exam sitting")
        ExamSitting.enter(self.term1, Student.objects.filter(status="active"))
        ExamSitting.objects.filter(student__user__username="alice").update(
            venue="Hall C")
        after = self.client.get(reverse("child_detail",
                                        args=[self.students["alice"].pk]))
        self.assertContains(after, "TERM1-0001")
        self.assertContains(after, "Hall C")
        self.assertContains(after, "Cleared to sit the exams")
