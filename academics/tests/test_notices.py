from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from academics.models import (Announcement, Guardian, Programme, SchoolClass,
                              SchoolSettings, Student)


class BaseNotices(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.registrar = User.objects.create_user("reg", password="pw123456",
                                                 first_name="Regina", last_name="Zulu",
                                                 role="registrar")
        cls.bursar = User.objects.create_user("bur", password="pw123456", role="bursar")
        cls.lecturer = User.objects.create_user("lec", password="pw123456",
                                                role="lecturer")
        cls.g3 = SchoolClass.objects.create(name="Grade 3", level="primary")
        cls.g4 = SchoolClass.objects.create(name="Grade 4", level="primary")
        cls.programme = Programme.objects.create(name="Primary School", code="PRI",
                                                 level="primary")
        u3 = User.objects.create_user("kid_g3", password="pw123456", role="student")
        u4 = User.objects.create_user("kid_g4", password="pw123456", role="student")
        uc = User.objects.create_user("kid_clg", password="pw123456", role="student")
        cls.s_g3 = Student.objects.create(user=u3, programme=cls.programme,
                                          school_class=cls.g3)
        cls.s_g4 = Student.objects.create(user=u4, programme=cls.programme,
                                          school_class=cls.g4)
        cls.s_clg = Student.objects.create(user=uc, programme=cls.programme)
        up = User.objects.create_user("mama", password="pw123456", role="parent")
        Guardian.objects.create(parent=up, student=cls.s_g3)
        cls.parent = up
        cls.notices = {
            "all": Announcement.objects.create(title="All hands", body="For everyone.",
                                               audience="all"),
            "student": Announcement.objects.create(title="Students only",
                                                   body="Students read this.",
                                                   audience="student"),
            "lecturer": Announcement.objects.create(title="Lecturers only",
                                                    body="Lecturers read this.",
                                                    audience="lecturer"),
            "parent": Announcement.objects.create(title="Parents only",
                                                  body="Parents read this.",
                                                  audience="parent"),
            "g3": Announcement.objects.create(title="G3 notice", body="Class notice.",
                                              audience="student", school_class=cls.g3),
            "g4": Announcement.objects.create(title="G4 notice", body="Other class.",
                                              audience="student", school_class=cls.g4),
        }


class VisibleToTests(BaseNotices):
    def titles(self, qs):
        return {a.title for a in qs}

    def test_staff_see_everything(self):
        expected = set(Announcement.objects.values_list("title", flat=True))
        for user in (self.registrar, self.bursar):
            self.assertEqual(self.titles(Announcement.visible_to(user)), expected)

    def test_lecturer_sees_all_and_lecturer_notices(self):
        got = self.titles(Announcement.visible_to(self.lecturer))
        self.assertEqual(got, {"All hands", "Lecturers only"})

    def test_class_student_sees_class_scoped_notices(self):
        got = self.titles(Announcement.visible_to(self.s_g3.user))
        self.assertEqual(got, {"All hands", "Students only", "G3 notice"})

    def test_student_in_another_class_sees_their_own(self):
        got = self.titles(Announcement.visible_to(self.s_g4.user))
        self.assertEqual(got, {"All hands", "Students only", "G4 notice"})

    def test_classless_college_student_ignores_class_notices(self):
        got = self.titles(Announcement.visible_to(self.s_clg.user))
        self.assertEqual(got, {"All hands", "Students only"})

    def test_parent_sees_child_class_and_parent_notices(self):
        got = self.titles(Announcement.visible_to(self.parent))
        self.assertEqual(got, {"All hands", "Students only", "Parents only",
                               "G3 notice"})

    def test_unknown_role_sees_nothing(self):
        stranger = User.objects.create_user("guest", password="pw123456", role="student")
        stranger.role = "guest"
        self.assertEqual(Announcement.visible_to(stranger).count(), 0)


class NoticesPageTests(BaseNotices):
    def url(self):
        return reverse("notices")

    def test_all_staff_can_open_the_page(self):
        for user in (self.registrar, self.bursar, self.lecturer):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_parents_and_students_are_refused(self):
        for user in (self.parent, self.s_g3.user):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_only_registrar_sees_the_compose_form(self):
        self.client.force_login(self.registrar)
        self.assertContains(self.client.get(self.url()), "Post a notice")
        self.client.force_login(self.bursar)
        self.assertNotContains(self.client.get(self.url()), "Post a notice")

    def test_registrar_posts_a_notice_attributed_to_themselves(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(self.url(), {"title": "Fire drill 10am",
                                             "body": "Everyone gathers outside.",
                                             "audience": "all"}, follow=True)
        a = Announcement.objects.get(title="Fire drill 10am")
        self.assertEqual(a.posted_by, self.registrar)
        self.assertContains(resp, "Notice posted")

    def test_registrar_can_target_one_class_in_school_mode(self):
        st = SchoolSettings.get()
        st.mode = "school"
        st.save()
        self.client.force_login(self.registrar)
        self.client.post(self.url(), {"title": "G4 assembly", "body": "Be there.",
                                      "audience": "student",
                                      "school_class": str(self.g4.pk)})
        a = Announcement.objects.get(title="G4 assembly")
        self.assertEqual(a.school_class, self.g4)

    def test_invalid_post_re_renders_with_errors(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(self.url(), {"title": "", "body": "x",
                                             "audience": "all"})
        self.assertContains(resp, "This field is required")
        self.assertFalse(Announcement.objects.filter(title="").exists())

    def test_lecturer_cannot_post(self):
        self.client.force_login(self.lecturer)
        resp = self.client.post(self.url(), {"title": "Sneaky", "body": "x",
                                             "audience": "all"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Announcement.objects.filter(title="Sneaky").exists())

    def test_bursar_cannot_delete(self):
        self.client.force_login(self.bursar)
        resp = self.client.post(self.url(),
                                {"action": "delete",
                                 "pk": str(self.notices["all"].pk)})
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Announcement.objects.filter(pk=self.notices["all"].pk).exists())

    def test_registrar_deletes_a_notice(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(self.url(), {"action": "delete",
                                             "pk": str(self.notices["parent"].pk)},
                                follow=True)
        self.assertFalse(Announcement.objects.filter(
            pk=self.notices["parent"].pk).exists())
        self.assertContains(resp, "Notice removed")

    def test_staff_nav_links_to_notices(self):
        self.client.force_login(self.registrar)
        self.assertContains(self.client.get(reverse("dashboard")), 'href="/notices/"')
        self.client.force_login(self.lecturer)
        self.assertContains(self.client.get(reverse("lecturer_portal")),
                            'href="/notices/"')


class PortalFilterTests(BaseNotices):
    def test_student_portal_filters_by_audience_and_class(self):
        self.client.force_login(self.s_g3.user)
        resp = self.client.get(reverse("student_portal"))
        for title in ("All hands", "Students only", "G3 notice"):
            self.assertContains(resp, title)
        for title in ("Lecturers only", "Parents only", "G4 notice"):
            self.assertNotContains(resp, title)

    def test_parent_portal_shows_child_class_notices(self):
        self.client.force_login(self.parent)
        resp = self.client.get(reverse("parent_portal"))
        for title in ("All hands", "Students only", "Parents only", "G3 notice"):
            self.assertContains(resp, title)
        for title in ("Lecturers only", "G4 notice"):
            self.assertNotContains(resp, title)

    def test_lecturer_portal_shows_lecturer_notices(self):
        self.client.force_login(self.lecturer)
        resp = self.client.get(reverse("lecturer_portal"))
        for title in ("All hands", "Lecturers only"):
            self.assertContains(resp, title)
        for title in ("Students only", "Parents only", "G3 notice"):
            self.assertNotContains(resp, title)

    def test_child_detail_uses_the_same_filter(self):
        self.client.force_login(self.parent)
        resp = self.client.get(reverse("child_detail", args=[self.s_g3.pk]))
        self.assertContains(resp, "G3 notice")
        self.assertNotContains(resp, "G4 notice")
