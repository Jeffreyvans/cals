"""Phase 2: the academic structure - levels, closed periods and promotion."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import (
    AcademicYear,
    ClassSubjectAllocation,
    GradeLevel,
    Programme,
    SchoolClass,
    Stream,
    Student,
    Subject,
    Term,
)
from academics.services import close_term, close_year, promote_students, promotion_plan
from core.audit import AuditEntry
from core.models import Institution
from core.roles import ensure_roles
from core.tenancy import reset_current_institution, set_current_institution, use_institution

User = get_user_model()


class StructureFixture(TestCase):
    """A small school: Grade 3 with a stream, Grade 4 waiting for them."""

    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha School", slug="alpha", type="secondary")
        ensure_roles(cls.inst)
        with use_institution(cls.inst):
            cls.year = AcademicYear.objects.create(
                name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                is_current=True)
            cls.term = Term.objects.create(
                academic_year=cls.year, name="Term 1", start_date=date(2026, 1, 12),
                end_date=date(2026, 4, 10), is_current=True)
            cls.level3 = GradeLevel.objects.create(
                name="Grade 3", kind="primary", order=3)
            cls.level4 = GradeLevel.objects.create(
                name="Grade 4", kind="primary", order=4)
            cls.class3 = SchoolClass.objects.create(
                name="Grade 3", level="primary", grade_level=cls.level3)
            cls.class4 = SchoolClass.objects.create(
                name="Grade 4", level="primary", grade_level=cls.level4)
            cls.stream = Stream.objects.create(school_class=cls.class3, name="A")
            cls.other_stream = Stream.objects.create(school_class=cls.class4, name="A")
            cls.programme = Programme.objects.create(
                code="SEC", name="Secondary", level="secondary")
            cls.student = Student.objects.create(
                user=User.objects.create_user(
                    "tariro", password="pw123456", role="student", institution=cls.inst),
                programme=cls.programme, school_class=cls.class3, stream=cls.stream,
                year_or_grade="3")
        cls.registrar = User.objects.create_user(
            "reg", password="pw123456", role="registrar", institution=cls.inst)
        cls.lecturer = User.objects.create_user(
            "teach", password="pw123456", role="lecturer", institution=cls.inst)

    def setUp(self):
        # Every read and write in this test belongs to the school under test.
        token = set_current_institution(self.inst)
        self.addCleanup(reset_current_institution, token)


class GradeLevelTests(StructureFixture):
    def test_a_level_knows_the_level_above_it(self):
        self.assertEqual(self.level3.next_level, self.level4)
        self.assertIsNone(self.level4.next_level)

    def test_a_level_only_moves_within_its_own_kind(self):
        secondary = GradeLevel.objects.create(name="Form 1", kind="secondary", order=1)
        self.assertEqual(self.level3.next_level, self.level4)
        self.assertIsNone(secondary.next_level)
        self.assertNotEqual(secondary.kind, self.level3.kind)

    def test_two_levels_cannot_share_a_name(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GradeLevel.objects.create(name="Grade 3", kind="primary", order=9)


class PromotionTests(StructureFixture):
    def test_the_plan_lists_a_student_who_can_move_up(self):
        plan = promotion_plan(self.inst)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["student"], self.student)
        self.assertEqual(plan[0]["from_class"], self.class3)
        self.assertEqual(plan[0]["to_class"], self.class4)

    def test_a_student_without_a_class_is_not_in_the_plan(self):
        Student.objects.filter(pk=self.student.pk).update(school_class=None)
        self.assertEqual(promotion_plan(self.inst), [])

    def test_a_student_who_is_not_active_is_left_alone(self):
        Student.objects.filter(pk=self.student.pk).update(status=Student.Status.WITHDRAWN)
        self.assertEqual(promotion_plan(self.inst), [])

    def test_promoting_moves_the_student_and_keeps_the_stream(self):
        moved = promote_students(self.inst, self.registrar)
        self.assertEqual(len(moved), 1)
        self.student.refresh_from_db()
        self.assertEqual(self.student.school_class, self.class4)
        self.assertEqual(self.student.stream, self.other_stream)
        self.assertEqual(self.student.year_or_grade, "Grade 4")

    def test_the_stream_is_dropped_when_the_new_class_has_none(self):
        Stream.objects.filter(school_class=self.class4).delete()
        promote_students(self.inst, self.registrar)
        self.student.refresh_from_db()
        self.assertEqual(self.student.school_class, self.class4)
        self.assertIsNone(self.student.stream)

    def test_the_move_is_written_to_the_audit_log(self):
        promote_students(self.inst, self.registrar)
        entry = AuditEntry.all_objects.filter(action="students.promoted").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.before["class"], "Grade 3")
        self.assertEqual(entry.after["class"], "Grade 4")

    def test_a_student_is_not_moved_when_there_is_no_class_above(self):
        GradeLevel.objects.filter(pk=self.level4.pk).delete()
        SchoolClass.objects.filter(pk=self.class4.pk).update(grade_level=None)
        self.assertEqual(promotion_plan(self.inst), [])


class ClosedPeriodTests(StructureFixture):
    def test_a_closed_term_records_the_day_it_was_closed(self):
        close_term(self.term, user=self.registrar)
        self.term.refresh_from_db()
        self.assertTrue(self.term.is_closed)
        self.assertEqual(self.term.closed_on, date.today())

    def test_closing_a_year_closes_its_terms(self):
        close_year(self.year, user=self.registrar)
        self.year.refresh_from_db()
        self.term.refresh_from_db()
        self.assertTrue(self.year.is_closed)
        self.assertTrue(self.term.is_closed)

    def test_the_screen_closes_and_opens_a_term(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(reverse("academic_years"),
                                {"action": "close_term", "pk": self.term.pk})
        self.assertEqual(resp.status_code, 302)
        self.term.refresh_from_db()
        self.assertTrue(self.term.is_closed)
        resp = self.client.post(reverse("academic_years"),
                                {"action": "reopen_term", "pk": self.term.pk})
        self.term.refresh_from_db()
        self.assertFalse(self.term.is_closed)

    def test_a_lecturer_cannot_close_a_term(self):
        self.client.force_login(self.lecturer)
        resp = self.client.post(reverse("academic_years"),
                                {"action": "close_term", "pk": self.term.pk})
        self.assertEqual(resp.status_code, 403)
        self.term.refresh_from_db()
        self.assertFalse(self.term.is_closed)


class LevelScreenTests(StructureFixture):
    def test_the_registrar_sees_the_levels(self):
        self.client.force_login(self.registrar)
        resp = self.client.get(reverse("grade_levels"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Grade 3")

    def test_a_lecturer_is_refused(self):
        self.client.force_login(self.lecturer)
        resp = self.client.get(reverse("grade_levels"))
        self.assertEqual(resp.status_code, 403)

    def test_a_class_can_be_put_on_a_level(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(reverse("grade_levels"),
                                {"action": "link", "class": self.class3.pk,
                                 "level": self.level4.pk})
        self.assertEqual(resp.status_code, 302)
        self.class3.refresh_from_db()
        self.assertEqual(self.class3.grade_level, self.level4)

    def test_a_class_can_be_taken_off_a_level(self):
        self.client.force_login(self.registrar)
        self.client.post(reverse("grade_levels"),
                         {"action": "unlink", "class": self.class3.pk})
        self.class3.refresh_from_db()
        self.assertIsNone(self.class3.grade_level)

    def test_a_level_can_be_added(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(reverse("grade_levels"),
                                {"action": "new_level", "name": "Grade 5",
                                 "kind": "primary", "order": 5})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(GradeLevel.objects.filter(name="Grade 5").exists())


class PromotionScreenTests(StructureFixture):
    def test_the_registrar_sees_the_plan(self):
        self.client.force_login(self.registrar)
        resp = self.client.get(reverse("promotion"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Grade 4")

    def test_a_lecturer_is_refused(self):
        self.client.force_login(self.lecturer)
        resp = self.client.get(reverse("promotion"))
        self.assertEqual(resp.status_code, 403)

    def test_the_students_that_are_ticked_are_moved(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(reverse("promotion"),
                                {"students": [str(self.student.pk)]})
        self.assertEqual(resp.status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.school_class, self.class4)

    def test_nobody_is_moved_when_nothing_is_ticked(self):
        self.client.force_login(self.registrar)
        self.client.post(reverse("promotion"), {})
        self.student.refresh_from_db()
        self.assertEqual(self.student.school_class, self.class3)


class AllocationTests(StructureFixture):
    def setUp(self):
        super().setUp()
        self.subject = Subject.objects.create(
            code="MTH", name="Mathematics", level="primary")

    def test_a_subject_is_added_to_the_class(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(reverse("class_detail", args=[self.class3.pk]),
                                {"action": "allocate", "subject": self.subject.pk,
                                 "lessons_per_week": 4})
        self.assertEqual(resp.status_code, 302)
        allocation = ClassSubjectAllocation.objects.filter(
            school_class=self.class3, subject=self.subject).first()
        self.assertIsNotNone(allocation)
        self.assertEqual(allocation.academic_year, self.year)
        self.assertEqual(allocation.lessons_per_week, 4)

    def test_the_same_subject_cannot_be_added_twice_in_a_year(self):
        self.client.force_login(self.registrar)
        url = reverse("class_detail", args=[self.class3.pk])
        self.client.post(url, {"action": "allocate", "subject": self.subject.pk})
        self.client.post(url, {"action": "allocate", "subject": self.subject.pk})
        self.assertEqual(ClassSubjectAllocation.objects.filter(
            school_class=self.class3, subject=self.subject).count(), 1)

    def test_a_lecturer_cannot_add_a_subject(self):
        self.client.force_login(self.lecturer)
        resp = self.client.post(reverse("class_detail", args=[self.class3.pk]),
                                {"action": "allocate", "subject": self.subject.pk})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(ClassSubjectAllocation.objects.count(), 0)

    def test_the_class_page_lists_the_subjects(self):
        ClassSubjectAllocation.objects.create(
            school_class=self.class3, subject=self.subject, academic_year=self.year)
        self.client.force_login(self.registrar)
        resp = self.client.get(reverse("class_detail", args=[self.class3.pk]))
        self.assertContains(resp, "Mathematics")
