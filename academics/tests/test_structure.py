from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from academics.forms import EnrollForm, StudentRegistrationForm
from academics.models import (
    AcademicYear,
    ClassTeacher,
    Course,
    Enrollment,
    Programme,
    SchoolClass,
    SchoolSettings,
    Stream,
    Student,
    Subject,
    Term,
    TimetableEntry,
)

User = get_user_model()


class AcademicYearTests(TestCase):
    def test_only_one_year_can_be_current(self):
        a = AcademicYear.objects.create(name="2025", start_date=date(2025, 1, 1),
                                        end_date=date(2025, 12, 31), is_current=True)
        b = AcademicYear.objects.create(name="2026", start_date=date(2026, 1, 1),
                                        end_date=date(2026, 12, 31), is_current=True)
        a.refresh_from_db()
        self.assertFalse(a.is_current)
        self.assertTrue(b.is_current)

    def test_year_dates_must_run_forwards(self):
        y = AcademicYear(name="2027", start_date=date(2027, 6, 1),
                         end_date=date(2027, 3, 1))
        with self.assertRaises(ValidationError):
            y.full_clean()

    def test_term_dates_must_sit_inside_the_year(self):
        y = AcademicYear.objects.create(name="2026", start_date=date(2026, 1, 1),
                                        end_date=date(2026, 12, 31))
        bad = Term(academic_year=y, name="Term 1", start_date=date(2025, 12, 1),
                   end_date=date(2026, 4, 10))
        with self.assertRaises(ValidationError):
            bad.full_clean()
        good = Term(academic_year=y, name="Term 1", start_date=date(2026, 1, 12),
                    end_date=date(2026, 4, 10))
        good.full_clean()

    def test_only_one_term_can_be_current(self):
        y = AcademicYear.objects.create(name="2026", start_date=date(2026, 1, 1),
                                        end_date=date(2026, 12, 31))
        t1 = Term.objects.create(academic_year=y, name="Term 1",
                                 start_date=date(2026, 1, 12), end_date=date(2026, 4, 10),
                                 is_current=True)
        t2 = Term.objects.create(academic_year=y, name="Term 2",
                                 start_date=date(2026, 5, 4), end_date=date(2026, 8, 28),
                                 is_current=True)
        t1.refresh_from_db()
        self.assertFalse(t1.is_current)
        self.assertTrue(t2.is_current)

    def test_same_term_name_twice_in_one_year_is_rejected(self):
        y = AcademicYear.objects.create(name="2026", start_date=date(2026, 1, 1),
                                        end_date=date(2026, 12, 31))
        Term.objects.create(academic_year=y, name="Term 1", start_date=date(2026, 1, 12),
                            end_date=date(2026, 4, 10))
        dup = Term(academic_year=y, name="Term 1", start_date=date(2026, 5, 4),
                   end_date=date(2026, 8, 28))
        with self.assertRaises(ValidationError):
            dup.full_clean()


class ClassAndStreamTests(TestCase):
    def setUp(self):
        self.g3 = SchoolClass.objects.create(name="Grade 3", level="primary")

    def test_stream_names_are_unique_inside_a_class(self):
        Stream.objects.create(school_class=self.g3, name="A")
        dup = Stream(school_class=self.g3, name="A")
        with self.assertRaises(ValidationError):
            dup.full_clean()
        Stream.objects.create(school_class=self.g3, name="B")
        self.assertEqual(self.g3.streams.count(), 2)

    def test_class_teacher_is_set_once_per_year(self):
        y = AcademicYear.objects.create(name="2026", start_date=date(2026, 1, 1),
                                        end_date=date(2026, 12, 31))
        lec = User.objects.create_user("lec", password="pw123456", role="lecturer")
        ClassTeacher.objects.create(school_class=self.g3, academic_year=y, teacher=lec)
        dup = ClassTeacher(school_class=self.g3, academic_year=y, teacher=lec)
        with self.assertRaises(ValidationError):
            dup.full_clean()

    def test_class_label_shows_class_and_stream(self):
        stream = Stream.objects.create(school_class=self.g3, name="A")
        programme = Programme.objects.create(name="Primary", code="PRI", level="primary")
        u = User.objects.create_user("s1", password="pw123456", role="student")
        s = Student.objects.create(user=u, programme=programme, school_class=self.g3,
                                   stream=stream)
        self.assertEqual(s.class_label, "Grade 3 A")
        s2user = User.objects.create_user("s2", password="pw123456", role="student")
        s2 = Student.objects.create(user=s2user, programme=programme)
        self.assertEqual(s2.class_label, "1")


class TimetableBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(name="2026", start_date=date(2026, 1, 1),
                                               end_date=date(2026, 12, 31), is_current=True)
        cls.term = Term.objects.create(academic_year=cls.year, name="Term 1",
                                       start_date=date(2026, 1, 12),
                                       end_date=date(2026, 4, 10), is_current=True)
        cls.other_term = Term.objects.create(academic_year=cls.year, name="Term 2",
                                             start_date=date(2026, 5, 4),
                                             end_date=date(2026, 8, 28))
        cls.lecturer = User.objects.create_user("lec", password="pw123456", role="lecturer")
        cls.lecturer2 = User.objects.create_user("lec2", password="pw123456", role="lecturer")
        cls.g3 = SchoolClass.objects.create(name="Grade 3", level="primary")
        cls.g4 = SchoolClass.objects.create(name="Grade 4", level="primary")
        cls.stream_a = Stream.objects.create(school_class=cls.g3, name="A")
        cls.stream_b = Stream.objects.create(school_class=cls.g3, name="B")
        cls.mth = Subject.objects.create(code="MTH", name="Mathematics", level="primary")
        cls.eng = Subject.objects.create(code="ENG", name="English", level="primary")
        cls.programme = Programme.objects.create(name="Diploma in IT", code="DIT",
                                                 level="diploma")
        cls.course = Course.objects.create(code="IT101", title="Programming",
                                           programme=cls.programme, lecturer=cls.lecturer)

    def make(self, **kw):
        base = dict(academic_year=self.year, term=self.term, day=1,
                    start_time=time(8, 0), end_time=time(8, 45),
                    subject=self.mth, school_class=self.g3, lecturer=self.lecturer)
        base.update(kw)
        return TimetableEntry(**base)


class TimetableClashTests(TimetableBase):
    def test_valid_lesson_saves(self):
        self.make(venue="Room 4").save()
        self.assertEqual(TimetableEntry.objects.count(), 1)

    def test_a_lesson_must_end_after_it_starts(self):
        with self.assertRaises(ValidationError) as cm:
            self.make(start_time=time(9, 0), end_time=time(8, 0)).save()
        self.assertIn("end after it starts", str(cm.exception))

    def test_subject_lessons_need_a_class(self):
        with self.assertRaises(ValidationError):
            self.make(school_class=None).save()

    def test_college_lessons_cannot_have_a_class(self):
        with self.assertRaises(ValidationError):
            self.make(subject=None, course=self.course, school_class=self.g3).save()

    def test_stream_must_belong_to_the_lesson_class(self):
        other_stream = Stream.objects.create(school_class=self.g4, name="A")
        with self.assertRaises(ValidationError) as cm:
            self.make(stream=other_stream).save()
        self.assertIn("another class", str(cm.exception))

    def test_term_must_match_the_academic_year(self):
        next_year = AcademicYear.objects.create(name="2027",
                                                start_date=date(2027, 1, 1),
                                                end_date=date(2027, 12, 31))
        with self.assertRaises(ValidationError) as cm:
            self.make(academic_year=next_year).save()
        self.assertIn("different academic year", str(cm.exception))

    def test_lecturer_cannot_teach_twice_at_once(self):
        self.make(start_time=time(8, 0), end_time=time(8, 45)).save()
        with self.assertRaises(ValidationError) as cm:
            self.make(school_class=self.g4, subject=self.eng,
                      start_time=time(8, 30), end_time=time(9, 15)).save()
        self.assertIn("already teaching", str(cm.exception))

    def test_class_cannot_have_two_lessons_at_once(self):
        self.make().save()
        with self.assertRaises(ValidationError) as cm:
            self.make(lecturer=self.lecturer2, subject=self.eng,
                      start_time=time(8, 30), end_time=time(9, 15)).save()
        self.assertIn("Grade 3 already has", str(cm.exception))

    def test_whole_class_lesson_clashes_with_a_stream_lesson(self):
        self.make(stream=None).save()
        with self.assertRaises(ValidationError):
            self.make(stream=self.stream_b, lecturer=self.lecturer2).save()

    def test_split_streams_can_run_at_the_same_time(self):
        self.make(stream=self.stream_a).save()
        self.make(stream=self.stream_b, lecturer=self.lecturer2,
                  subject=self.eng).save()
        self.assertEqual(TimetableEntry.objects.count(), 2)

    def test_same_lecturer_cannot_cover_two_streams_at_once(self):
        self.make(stream=self.stream_a).save()
        with self.assertRaises(ValidationError):
            self.make(stream=self.stream_b, subject=self.eng).save()

    def test_venue_cannot_be_double_booked(self):
        self.make(start_time=time(10, 0), end_time=time(11, 0), venue="Hall",
                  stream=self.stream_a).save()
        with self.assertRaises(ValidationError) as cm:
            self.make(start_time=time(10, 0), end_time=time(11, 0), venue="hall",
                      stream=self.stream_b, lecturer=self.lecturer2,
                      subject=self.eng).save()
        self.assertIn("Venue", str(cm.exception))

    def test_lessons_in_different_terms_do_not_clash(self):
        self.make().save()
        self.make(term=self.other_term).save()
        self.assertEqual(TimetableEntry.objects.count(), 2)

    def test_editing_a_lesson_does_not_clash_with_itself(self):
        entry = self.make(end_time=time(9, 0))
        entry.save()
        entry.subject = self.eng
        entry.end_time = time(9, 30)
        entry.save()
        entry.refresh_from_db()
        self.assertEqual(entry.subject, self.eng)


class TimetableForStudentTests(TimetableBase):
    def student_in(self, username, stream=None, programme=None):
        u = User.objects.create_user(username, password="pw123456", role="student")
        return Student.objects.create(user=u, programme=programme or self.programme,
                                      school_class=self.g3, stream=stream)

    def test_school_student_sees_own_class_and_stream_only(self):
        self.make(stream=None).save()                      # whole class
        self.make(stream=self.stream_a, lecturer=self.lecturer2,
                  subject=self.eng, start_time=time(9, 0),
                  end_time=time(9, 45)).save()             # stream A only
        self.make(school_class=self.g4, subject=self.mth, lecturer=self.lecturer2,
                  start_time=time(10, 0), end_time=time(10, 45)).save()  # other class
        student = self.student_in("pupil1", stream=self.stream_a)
        titles = [e.title for e in TimetableEntry.for_student(student)]
        self.assertEqual(titles.count("MTH – Mathematics"), 1)
        self.assertEqual(titles.count("ENG – English"), 1)
        self.assertEqual(len(titles), 2)

    def test_stream_b_student_does_not_see_stream_a_lessons(self):
        self.make(stream=self.stream_a, lecturer=self.lecturer2, subject=self.eng).save()
        student = self.student_in("pupil2", stream=self.stream_b)
        self.assertEqual(list(TimetableEntry.for_student(student)), [])

    def test_college_student_sees_lessons_for_enrolled_courses_only(self):
        lesson = TimetableEntry.objects.create(
            academic_year=self.year, term=self.term, day=3, start_time=time(9, 0),
            end_time=time(10, 30), course=self.course, lecturer=self.lecturer,
            venue="Lab 1")
        enrolled_user = User.objects.create_user("col1", password="pw123456",
                                                 role="student")
        enrolled = Student.objects.create(user=enrolled_user, programme=self.programme)
        Enrollment.objects.create(student=enrolled, course=self.course, term=self.term)
        other_user = User.objects.create_user("col2", password="pw123456",
                                              role="student")
        other = Student.objects.create(user=other_user, programme=self.programme)
        self.assertIn(lesson, list(TimetableEntry.for_student(enrolled)))
        self.assertEqual(list(TimetableEntry.for_student(other)), [])


class StructurePermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        settings_obj = SchoolSettings.get()
        settings_obj.mode = "school"
        settings_obj.save()
        cls.registrar = User.objects.create_user("reg", password="pw123456", role="registrar")
        cls.bursar = User.objects.create_user("bur", password="pw123456", role="bursar")
        cls.lecturer = User.objects.create_user("lec", password="pw123456", role="lecturer")
        cls.admin = User.objects.create_user("root", password="pw123456", role="admin")
        cls.student_user = User.objects.create_user("stu", password="pw123456", role="student")
        cls.year = AcademicYear.objects.create(name="2026", start_date=date(2026, 1, 1),
                                               end_date=date(2026, 12, 31), is_current=True)
        cls.term = Term.objects.create(academic_year=cls.year, name="Term 1",
                                       start_date=date(2026, 1, 12),
                                       end_date=date(2026, 4, 10), is_current=True)
        cls.g3 = SchoolClass.objects.create(name="Grade 3", level="primary")
        cls.mth = Subject.objects.create(code="MTH", name="Mathematics", level="primary")
        cls.programme = Programme.objects.create(name="Diploma in IT", code="DIT",
                                                 level="diploma")
        cls.course = Course.objects.create(code="IT101", title="Programming",
                                           programme=cls.programme, lecturer=cls.lecturer)
        cls.lesson = TimetableEntry.objects.create(
            academic_year=cls.year, term=cls.term, day=1, start_time=time(8, 0),
            end_time=time(8, 45), subject=cls.mth, school_class=cls.g3,
            lecturer=cls.lecturer)
        programme_user = User.objects.create_user("stu2", password="pw123456",
                                                  role="student")
        cls.student = Student.objects.create(user=programme_user, programme=cls.programme,
                                             school_class=cls.g3)

    def urls(self):
        return {
            "years": reverse("academic_years"),
            "classes": reverse("class_list"),
            "class": reverse("class_detail", args=[self.g3.pk]),
            "subjects": reverse("subject_list"),
            "programmes": reverse("programme_list"),
            "timetable": reverse("timetable"),
            "my_timetable": reverse("my_timetable"),
            "settings": reverse("school_settings"),
        }

    def assert_blocked(self, user, names):
        self.client.force_login(user)
        u = self.urls()
        for name in names:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(u[name]).status_code, 403)

    def assert_allowed(self, user, names):
        self.client.force_login(user)
        u = self.urls()
        for name in names:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(u[name]).status_code, 200)

    def test_students_cannot_open_any_structure_page(self):
        self.assert_blocked(self.student_user, list(self.urls()))

    def test_lecturer_can_view_but_not_manage(self):
        self.assert_allowed(self.lecturer, ["class", "subjects", "my_timetable"])
        self.assert_blocked(self.lecturer, ["years", "classes", "programmes",
                                            "timetable", "settings"])

    def test_bursar_cannot_open_academic_structure(self):
        self.assert_blocked(self.bursar, ["years", "classes", "subjects", "programmes",
                                          "timetable", "my_timetable", "settings"])

    def test_registrar_manages_everything_except_settings(self):
        self.assert_allowed(self.registrar, ["years", "classes", "class", "subjects",
                                             "programmes", "timetable"])
        self.assert_blocked(self.registrar, ["settings"])

    def test_admin_can_open_settings(self):
        self.assert_allowed(self.admin, ["settings", "timetable", "years"])

    def test_lecturer_cannot_add_lessons(self):
        self.client.force_login(self.lecturer)
        resp = self.client.post(reverse("timetable"), {})
        self.assertEqual(resp.status_code, 403)

    def test_lecturer_cannot_assign_class_teacher(self):
        self.client.force_login(self.lecturer)
        resp = self.client.post(reverse("class_detail", args=[self.g3.pk]),
                                {"action": "teacher", "teacher": self.lecturer.pk})
        self.assertEqual(resp.status_code, 403)

    def test_registrar_adding_a_clashing_lesson_is_refused(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(reverse("timetable"), {
            "academic_year": self.year.pk, "term": self.term.pk, "day": 1,
            "start_time": "08:30", "end_time": "09:15", "subject": self.mth.pk,
            "school_class": self.g3.pk, "lecturer": self.lecturer.pk, "venue": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(TimetableEntry.objects.count(), 1)
        self.assertTrue(resp.context["form"].non_field_errors())

    def test_registrar_can_save_a_valid_lesson(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(reverse("timetable"), {
            "academic_year": self.year.pk, "term": self.term.pk, "day": 2,
            "start_time": "10:00", "end_time": "10:45", "subject": self.mth.pk,
            "school_class": self.g3.pk, "lecturer": self.lecturer.pk, "venue": "Room 2"})
        self.assertRedirects(resp, reverse("timetable"))
        self.assertEqual(TimetableEntry.objects.count(), 2)

    def test_registrar_can_set_the_class_teacher(self):
        self.client.force_login(self.registrar)
        self.client.post(reverse("class_detail", args=[self.g3.pk]),
                         {"action": "teacher", "teacher": self.lecturer.pk})
        self.assertTrue(ClassTeacher.objects.filter(
            school_class=self.g3, academic_year=self.year,
            teacher=self.lecturer).exists())

    def test_lecturer_sees_only_their_own_lessons(self):
        other_lecturer = User.objects.create_user("lec2", password="pw123456",
                                                  role="lecturer")
        other_lesson = TimetableEntry.objects.create(
            academic_year=self.year, term=self.term, day=2, start_time=time(8, 0),
            end_time=time(8, 45), subject=self.mth, school_class=self.g3,
            lecturer=other_lecturer)
        self.client.force_login(self.lecturer)
        resp = self.client.get(reverse("my_timetable"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Grade 3")
        entries = resp.context["groups"]
        flat = [e for _, group in entries for e in group]
        self.assertIn(self.lesson, flat)
        self.assertNotIn(other_lesson, flat)


class ModeSwitchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("root", password="pw123456", role="admin")
        cls.registrar = User.objects.create_user("reg", password="pw123456", role="registrar")
        cls.year = AcademicYear.objects.create(name="2026", start_date=date(2026, 1, 1),
                                               end_date=date(2026, 12, 31), is_current=True)
        cls.g3 = SchoolClass.objects.create(name="Grade 3", level="primary")
        cls.mth = Subject.objects.create(code="MTH", name="Mathematics", level="primary")
        cls.programme = Programme.objects.create(name="Primary", code="PRI",
                                                 level="primary")

    def set_mode(self, mode):
        s = SchoolSettings.get()
        s.mode = mode
        s.save()

    def test_admin_can_switch_mode(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("school_settings"), {"mode": "school"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["MODE"], "school")
        self.assertTrue(resp.context["IS_SCHOOL"])

    def test_navigation_follows_the_mode(self):
        self.client.force_login(self.registrar)
        self.set_mode("college")
        dash = self.client.get(reverse("dashboard"))
        self.assertNotContains(dash, 'href="/classes/"')
        self.set_mode("school")
        dash = self.client.get(reverse("dashboard"))
        self.assertContains(dash, 'href="/classes/"')

    def test_school_mode_registration_form_asks_for_a_class(self):
        self.set_mode("school")
        form = StudentRegistrationForm()
        self.assertIn("school_class", form.fields)
        self.assertNotIn("year_or_grade", form.fields)
        data = {"first_name": "A", "last_name": "B", "username": "newpupil",
                "password": "secret123", "programme": self.programme.pk}
        missing = StudentRegistrationForm(data=data)
        self.assertFalse(missing.is_valid())
        self.assertIn("Choose the class", str(missing.errors))
        data["school_class"] = self.g3.pk
        ok = StudentRegistrationForm(data=data)
        self.assertTrue(ok.is_valid(), ok.errors)

    def test_college_mode_registration_form_hides_class_fields(self):
        self.set_mode("college")
        form = StudentRegistrationForm()
        self.assertNotIn("school_class", form.fields)
        self.assertIn("year_or_grade", form.fields)

    def test_enroll_form_offers_subjects_in_school_mode(self):
        self.set_mode("school")
        form = EnrollForm()
        self.assertIn("subject", form.fields)
        self.assertNotIn("course", form.fields)
        self.assertIn("term", form.fields)

    def test_enroll_form_offers_courses_in_college_mode(self):
        self.set_mode("college")
        course = Course.objects.create(code="IT101", title="Programming",
                                       programme=self.programme)
        form = EnrollForm()
        self.assertIn("course", form.fields)
        self.assertNotIn("subject", form.fields)
        self.assertEqual(list(form.fields["course"].queryset), [course])
