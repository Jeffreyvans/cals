from django.test import TestCase
from django.urls import reverse

from academics.models import Course, Enrollment, Guardian, Programme, Student
from accounts.models import User


class BaseParents(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.parent = User.objects.create_user("parent", password="pw123456",
                                              first_name="Perry", last_name="Moyo",
                                              role="parent")
        cls.other_parent = User.objects.create_user("parent2", password="pw123456",
                                                    role="parent")
        cls.registrar = User.objects.create_user("reg", password="pw123456",
                                                 role="registrar")
        cls.lecturer = User.objects.create_user("lec", password="pw123456",
                                                role="lecturer")
        cls.programme = Programme.objects.create(name="Primary School", code="PRI",
                                                 level="primary")
        ua = User.objects.create_user("kid1", password="pw123456", first_name="Kuda",
                                      last_name="Moyo", role="student")
        ub = User.objects.create_user("kid2", password="pw123456", first_name="Kuda",
                                      last_name="Banda", role="student")
        cls.a = Student.objects.create(user=ua, programme=cls.programme)
        cls.b = Student.objects.create(user=ub, programme=cls.programme)
        Guardian.objects.create(parent=cls.parent, student=cls.a)
        cls.course = Course.objects.create(code="PS101", title="Reading",
                                           programme=cls.programme,
                                           lecturer=cls.lecturer)
        e = Enrollment.objects.create(student=cls.a, course=cls.course, term="Term 1")
        e.mark = 66
        e.save()


class ParentPortalTests(BaseParents):
    def test_parent_sees_their_children_only(self):
        self.client.force_login(self.parent)
        resp = self.client.get(reverse("parent_portal"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Kuda Moyo")
        self.assertNotContains(resp, "Kuda Banda")

    def test_parent_can_open_a_linked_child(self):
        self.client.force_login(self.parent)
        resp = self.client.get(reverse("child_detail", args=[self.a.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Kuda Moyo")
        self.assertContains(resp, "66")

    def test_parent_cannot_open_an_unlinked_child(self):
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(reverse("child_detail", args=[self.b.pk])).status_code,
                         404)

    def test_other_parents_child_detail_is_hidden(self):
        self.client.force_login(self.other_parent)
        self.assertEqual(self.client.get(reverse("child_detail", args=[self.a.pk])).status_code,
                         404)

    def test_dashboard_redirects_parents_to_their_children(self):
        self.client.force_login(self.parent)
        resp = self.client.get(reverse("dashboard"))
        self.assertRedirects(resp, reverse("parent_portal"))

    def test_parents_cannot_open_staff_pages(self):
        self.client.force_login(self.parent)
        for name, args in [("student_search", []), ("student_create", []),
                           ("student_detail", [self.a.pk]), ("timetable", []),
                           ("grade_bands", [])]:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)

    def test_students_and_lecturers_cannot_open_the_parent_portal(self):
        for user in (self.a.user, self.lecturer):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("parent_portal")).status_code, 403,
                             user.username)


class LinkGuardianTests(BaseParents):
    def link_url(self):
        return reverse("link_guardian", args=[self.a.pk])

    def post(self, **extra):
        data = {"username": "newparent", "first_name": "Neo", "last_name": "Moyo",
                "password": "secret123"}
        data.update(extra)
        return self.client.post(self.link_url(), data, follow=True)

    def test_registrar_creates_and_links_a_new_parent(self):
        self.client.force_login(self.registrar)
        resp = self.post()
        user = User.objects.get(username="newparent")
        self.assertEqual(user.role, "parent")
        self.assertTrue(user.check_password("secret123"))
        self.assertTrue(Guardian.objects.filter(parent=user, student=self.a).exists())
        self.assertContains(resp, "can now follow")

    def test_registrar_links_an_existing_parent(self):
        self.client.force_login(self.registrar)
        resp = self.post(username="parent2", first_name="", last_name="", password="")
        self.assertTrue(Guardian.objects.filter(parent=self.other_parent,
                                                student=self.a).exists())
        self.assertEqual(User.objects.filter(username="parent2").count(), 1)
        self.assertContains(resp, "can now follow")

    def test_linking_a_non_parent_username_is_refused(self):
        self.client.force_login(self.registrar)
        resp = self.post(username="kid1")
        self.assertFalse(Guardian.objects.filter(student=self.a,
                                                 parent__username="kid1").exists())
        self.assertContains(resp, "not a parent account")

    def test_new_parent_requires_a_password_and_first_name(self):
        self.client.force_login(self.registrar)
        resp = self.post(password="", first_name="")
        self.assertFalse(User.objects.filter(username="newparent").exists())
        self.assertContains(resp, "Set a password")
        self.assertContains(resp, "first name")

    def test_duplicate_link_is_refused(self):
        self.client.force_login(self.registrar)
        resp = self.post(username="parent", first_name="", last_name="", password="")
        self.assertEqual(Guardian.objects.filter(parent=self.parent,
                                                 student=self.a).count(), 1)
        self.assertContains(resp, "already linked")

    def test_lecturers_cannot_link_parents(self):
        self.client.force_login(self.lecturer)
        resp = self.client.post(self.link_url(), {"username": "x", "password": "secret123",
                                                  "first_name": "X"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Guardian.objects.filter(parent__username="x").exists())

    def test_unlink_removes_the_connection(self):
        self.client.force_login(self.registrar)
        link = Guardian.objects.get(parent=self.parent, student=self.a)
        resp = self.client.post(reverse("unlink_guardian", args=[self.a.pk, link.pk]),
                                follow=True)
        self.assertFalse(Guardian.objects.filter(pk=link.pk).exists())
        self.assertContains(resp, "no longer linked")
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(reverse("child_detail", args=[self.a.pk])).status_code,
                         404)
