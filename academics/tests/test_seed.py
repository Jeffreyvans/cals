from django.core.management import call_command
from django.test import TestCase

from decimal import Decimal

from accounts.models import User
from academics.models import (AcademicYear, Announcement, Attendance,
                              ClassTeacher, Course, ExamSitting, GradeBand,
                              Guardian, Programme, SchoolClass, SchoolSettings,
                              Stream, Student, Subject, Term, TimetableEntry)
from finance.models import Invoice, MoneyLog, Payment, Waiver


class SeedCommandTests(TestCase):
    def test_seed_creates_demo_data(self):
        call_command("seed")
        self.assertTrue(User.objects.filter(username="admin").exists())
        self.assertTrue(User.objects.filter(role="registrar").exists())
        self.assertTrue(Programme.objects.exists())
        self.assertTrue(Course.objects.exists())
        self.assertGreater(Student.objects.count(), 0)
        self.assertGreater(Invoice.objects.count(), 0)
        self.assertGreater(Payment.objects.count(), 0)

    def test_seed_creates_the_academic_structure(self):
        call_command("seed")
        self.assertTrue(AcademicYear.objects.filter(is_current=True).exists())
        self.assertTrue(Term.objects.filter(is_current=True).exists())
        self.assertGreaterEqual(SchoolClass.objects.count(), 4)
        self.assertGreaterEqual(Stream.objects.count(), 4)
        self.assertGreaterEqual(Subject.objects.count(), 4)
        self.assertGreaterEqual(ClassTeacher.objects.count(), 1)
        self.assertGreaterEqual(TimetableEntry.objects.count(), 3)
        self.assertTrue(SchoolSettings.objects.exists())
        pupil = Student.objects.get(user__username="student4")
        self.assertIsNotNone(pupil.school_class_id)
        self.assertIsNotNone(pupil.stream_id)
        self.assertTrue(pupil.enrollments.filter(subject__isnull=False).exists())

    def test_seed_creates_grade_bands_and_demo_marks(self):
        call_command("seed")
        self.assertEqual(GradeBand.objects.count(), 5)
        self.assertEqual(GradeBand.objects.get(letter="A").min_mark, 75)
        self.assertTrue(Subject.objects.filter(lecturer__isnull=False).exists())
        student1 = Student.objects.get(user__username="student1")
        it101 = student1.enrollments.get(course__code="IT101")
        self.assertEqual(it101.mark, 72)
        self.assertEqual(it101.grade, "B")

    def test_seed_creates_demo_attendance(self):
        call_command("seed")
        student1 = Student.objects.get(user__username="student1")
        it101 = student1.enrollments.get(course__code="IT101")
        self.assertEqual(Attendance.objects.filter(enrollment=it101).count(), 3)
        self.assertEqual(Attendance.objects.filter(enrollment=it101,
                                                   present=True).count(), 2)

    def test_seed_links_a_demo_parent(self):
        call_command("seed")
        parent = User.objects.get(username="parent")
        self.assertEqual(parent.role, "parent")
        children = {g.student.user.username for g in parent.wards.all()}
        self.assertEqual(children, {"student4", "student5"})

    def test_seed_posts_audience_and_class_notices(self):
        call_command("seed")
        g3 = Announcement.objects.get(title="Grade 3 sports day Friday")
        self.assertEqual(g3.audience, "student")
        self.assertEqual(g3.school_class.name, "Grade 3")
        self.assertEqual(g3.posted_by.username, "registrar")
        self.assertTrue(Announcement.objects.filter(audience="parent").exists())

    def test_seed_enters_everyone_for_term_1_exams(self):
        call_command("seed")
        sittings = ExamSitting.objects.filter(term__name="Term 1")
        self.assertEqual(sittings.count(), 5)
        self.assertEqual(set(sittings.values_list("venue", flat=True)), {"Hall A"})
        self.assertEqual(sittings.values_list("exam_no", flat=True).distinct().count(),
                         5)
        blocked = sittings.get(student__user__username="student2")
        self.assertFalse(blocked.cleared)
        self.assertEqual(blocked.note, "Fee balance outstanding")

    def test_seed_can_run_twice(self):
        call_command("seed")
        before = Student.objects.count()
        lessons = TimetableEntry.objects.count()
        call_command("seed")
        self.assertEqual(Student.objects.count(), before)
        self.assertEqual(TimetableEntry.objects.count(), lessons)

    def test_seed_writes_off_part_of_the_fees(self):
        call_command("seed")
        s5 = Student.objects.get(user__username="student5")
        self.assertEqual(s5.total_waived, Decimal("100.00"))
        self.assertEqual(s5.balance, s5.total_billed - s5.total_paid - Decimal("100.00"))
        w = Waiver.objects.get()
        self.assertTrue(w.note_no.startswith("CN"))
        self.assertEqual(w.recorded_by.username, "bursar")

    def test_seed_leaves_an_audit_trail(self):
        call_command("seed")
        self.assertEqual(MoneyLog.objects.filter(action="invoice").count(),
                         Invoice.objects.count())
        self.assertEqual(MoneyLog.objects.filter(action="payment").count(),
                         Payment.objects.count())
        self.assertEqual(MoneyLog.objects.filter(action="waiver").count(), 1)
        entry = MoneyLog.objects.filter(action="payment").first()
        self.assertEqual(entry.actor.username, "bursar")
        self.assertEqual(MoneyLog.objects.exclude(description="").count(),
                         MoneyLog.objects.count(),
                         "every entry should say what it was for")
