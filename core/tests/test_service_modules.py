"""Access, tenancy and shared screens for the Phase 7 service modules."""


from django.test import TestCase
from django.urls import reverse

from academics.factories import ProgrammeFactory, StudentFactory
from accounts.factories import UserFactory
from core.models import Institution, Permission, Role, RolePermission, SensitiveViewLog
from core.module_directory import service_modules
from core.modules import set_module
from core.roles import ensure_roles
from core.tenancy import use_institution
from health.models import HealthProfile
from library.models import Book, Loan


class ServiceModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institution = Institution.objects.get(slug="default")
        ensure_roles(cls.institution)
        for code, _namespace, module in service_modules():
            set_module(cls.institution, module.get("flag", code), True)
        cls.admin = UserFactory(
            username="services_admin", role="admin", institution=cls.institution
        )
        cls.student_user = UserFactory(
            username="services_student", role="student", institution=cls.institution
        )
        cls.student = StudentFactory(
            user=cls.student_user,
            programme=ProgrammeFactory(),
            status="active",
        )

    def _officer(self, role_code, username):
        user = UserFactory(username=username, role="student", institution=self.institution)
        user.primary_role = Role.all_objects.get(
            institution=self.institution, code=role_code
        )
        user.save()
        return user

    def test_admin_can_open_every_service_module_and_register(self):
        self.client.force_login(self.admin)
        for code, namespace, module in service_modules():
            response = self.client.get(reverse(f"{namespace}:hub"))
            self.assertEqual(response.status_code, 200, code)
            self.assertContains(response, module["title"])
            summary = self.client.get(reverse(f"{namespace}:api_summary"))
            self.assertEqual(summary.status_code, 200, code)
            self.assertEqual(summary.json()["module"], code)
            for kind in module["kinds"]:
                response = self.client.get(
                    reverse(f"{namespace}:list", args=[kind["slug"]])
                )
                self.assertEqual(
                    response.status_code, 200, f"{code}/{kind['slug']}"
                )

    def test_a_switched_off_service_module_is_not_found(self):
        set_module(self.institution, "library", False)
        self.addCleanup(set_module, self.institution, "library", True)
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("library:hub")).status_code, 404
        )
        self.assertEqual(
            self.client.get(reverse("library:list", args=["books"])).status_code, 404
        )

    def test_a_student_is_refused_and_sees_nothing(self):
        self.client.force_login(self.student_user)
        directory = self.client.get(reverse("module_directory"))
        self.assertEqual(directory.status_code, 200)
        self.assertContains(directory, "No service modules")
        self.assertEqual(
            self.client.get(reverse("library:hub")).status_code, 403
        )

    def test_the_directory_lists_only_the_modules_a_role_can_open(self):
        officer = self._officer("sports_officer", "services_sports")
        self.client.force_login(officer)
        response = self.client.get(reverse("module_directory"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sports and clubs")
        self.assertNotContains(response, "Library")
        self.assertNotContains(response, "Health and sick bay")

    def test_a_borrow_only_role_can_read_but_not_change_a_register(self):
        role = Role.all_objects.create(
            institution=self.institution, code="borrower", name="Borrower"
        )
        RolePermission.objects.create(
            role=role, permission=Permission.objects.get(code="library.borrow")
        )
        user = UserFactory(
            username="services_borrower", role="student", institution=self.institution
        )
        user.primary_role = role
        user.save()
        self.client.force_login(user)
        self.assertEqual(
            self.client.get(reverse("library:list", args=["books"])).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("library:create", args=["books"])).status_code, 403
        )
        self.assertEqual(
            self.client.post(reverse("library:create", args=["books"]), {}).status_code,
            403,
        )

    def test_a_counsellor_can_open_counselling_but_not_health(self):
        counsellor = self._officer("counsellor", "services_counsellor")
        self.client.force_login(counsellor)
        self.assertEqual(
            self.client.get(reverse("counselling:hub")).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("health:hub")).status_code, 403
        )

    def test_sensitive_records_are_logged_when_viewed(self):
        HealthProfile.objects.create(
            institution=self.institution,
            student=self.student,
            blood_group="O+",
            allergies="Penicillin",
        )
        self.client.force_login(self.admin)
        self.client.get(reverse("health:list", args=["profiles"]))
        self.client.get(reverse("health:detail", args=["profiles", self.student.health_profile.pk]))
        self.assertGreaterEqual(
            SensitiveViewLog.objects.filter(record_type="HealthProfile").count(), 2
        )

    def test_a_service_record_from_another_institution_is_not_visible(self):
        other = Institution.objects.create(
            name="Other College", slug="other-service-college", type="college"
        )
        with use_institution(other):
            hidden = Book.all_objects.create(
                institution=other, title="Hidden title", copies_total=1, copies_available=1
            )
        self.client.force_login(self.admin)
        listing = self.client.get(reverse("library:list", args=["books"]))
        self.assertNotContains(listing, "Hidden title")
        detail = self.client.get(reverse("library:detail", args=["books", hidden.pk]))
        self.assertEqual(detail.status_code, 404)

    def test_a_library_loan_updates_the_available_copies(self):
        book = Book.objects.create(
            institution=self.institution,
            title="Things Fall Apart",
            copies_total=2,
            copies_available=2,
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("library:create", args=["loans"]),
            {
                "book": book.pk,
                "student": self.student.pk,
                "borrowed_on": "2026-02-01",
                "due_on": "2026-02-15",
                "status": "borrowed",
                "fine": "0",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        book.refresh_from_db()
        self.assertEqual(book.copies_available, 1)
        loan = Loan.objects.get(book=book, student=self.student)
        response = self.client.post(
            reverse("library:edit", args=["loans", loan.pk]),
            {
                "book": book.pk,
                "student": self.student.pk,
                "borrowed_on": "2026-02-01",
                "due_on": "2026-02-15",
                "returned_on": "2026-02-10",
                "status": "returned",
                "fine": "0",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        book.refresh_from_db()
        self.assertEqual(book.copies_available, 2)

    def test_a_record_can_be_created_edited_and_removed(self):
        self.client.force_login(self.admin)
        created = self.client.post(
            reverse("library:create", args=["books"]),
            {
                "title": "A new book",
                "author": "An author",
                "isbn": "",
                "category": "textbook",
                "shelf": "A1",
                "copies_total": "1",
                "copies_available": "1",
                "is_active": "on",
            },
        )
        self.assertEqual(created.status_code, 302)
        book = Book.objects.get(title="A new book")
        edited = self.client.post(
            reverse("library:edit", args=["books", book.pk]),
            {
                "title": "A renamed book",
                "author": "An author",
                "isbn": "",
                "category": "textbook",
                "shelf": "A1",
                "copies_total": "1",
                "copies_available": "1",
                "is_active": "on",
            },
        )
        self.assertEqual(edited.status_code, 302)
        book.refresh_from_db()
        self.assertEqual(book.title, "A renamed book")
        deleted = self.client.post(
            reverse("library:delete", args=["books", book.pk])
        )
        self.assertEqual(deleted.status_code, 302)
        self.assertFalse(Book.objects.filter(pk=book.pk).exists())

    def test_the_json_register_returns_tenant_rows(self):
        Book.objects.create(
            institution=self.institution,
            title="Discrete Mathematics",
            copies_total=1,
            copies_available=1,
        )
        other = Institution.objects.create(
            name="Another College", slug="another-service-college", type="college"
        )
        with use_institution(other):
            Book.all_objects.create(
                institution=other, title="Not here", copies_total=1, copies_available=1
            )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("library:api_records", args=["books"]))
        self.assertEqual(response.status_code, 200)
        labels = [row["label"] for row in response.json()["results"]]
        self.assertIn("Discrete Mathematics", labels)
        self.assertNotIn("Not here", labels)
