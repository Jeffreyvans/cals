from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.factories import StudentFactory
from accounts.factories import UserFactory
from core.models import Institution, Notification, Permission, Role, RolePermission
from core.modules import set_module
from core.roles import ensure_roles
from library.models import Book, Loan, Reservation
from library.services import (
    assess_loan_fine,
    mark_overdue_loans,
    renew_loan,
    send_overdue_notices,
)


class LibraryTests(TestCase):
    def test_a_loan_takes_and_returns_a_copy(self):
        book = Book.objects.create(title="Statistics", copies_total=2, copies_available=2)
        student = StudentFactory(status="active")
        loan = Loan.objects.create(
            book=book,
            student=student,
            borrowed_on=timezone.localdate() - timedelta(days=10),
            due_on=timezone.localdate() - timedelta(days=1),
        )
        book.refresh_from_db()
        self.assertEqual(book.copies_available, 1)
        self.assertTrue(loan.is_overdue)
        loan.returned_on = timezone.localdate()
        loan.save(update_fields=["returned_on"])
        book.refresh_from_db()
        self.assertEqual(book.copies_available, 2)

    def test_a_loan_entered_as_already_returned_does_not_take_a_copy(self):
        book = Book.objects.create(title="Chemistry", copies_total=1, copies_available=1)
        student = StudentFactory(status="active")
        Loan.objects.create(
            book=book,
            student=student,
            borrowed_on=timezone.localdate() - timedelta(days=5),
            due_on=timezone.localdate() - timedelta(days=1),
            returned_on=timezone.localdate(),
            status=Loan.Status.RETURNED,
        )
        book.refresh_from_db()
        self.assertEqual(book.copies_available, 1)

    def test_available_copies_cannot_exceed_the_total(self):
        from django.core.exceptions import ValidationError

        book = Book(title="Bad count", copies_total=1, copies_available=2)
        with self.assertRaises(ValidationError):
            book.clean()


class LibraryServiceTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.book = Book.objects.create(
            title="Organic Chemistry", copies_total=2, copies_available=2
        )
        self.student = StudentFactory(status="active")
        self.loan = Loan.objects.create(
            book=self.book,
            student=self.student,
            borrowed_on=self.today - timedelta(days=7),
            due_on=self.today + timedelta(days=7),
        )

    def test_a_loan_can_be_renewed_twice_and_not_three_times(self):
        original_due = self.loan.due_on
        renew_loan(self.loan)
        self.assertEqual(self.loan.renewals, 1)
        self.assertEqual(self.loan.due_on, original_due + timedelta(days=14))
        renew_loan(self.loan)
        self.assertEqual(self.loan.renewals, 2)
        with self.assertRaises(ValidationError):
            renew_loan(self.loan)

    def test_an_overdue_loan_cannot_be_renewed(self):
        self.loan.due_on = self.today - timedelta(days=1)
        self.loan.save(update_fields=["due_on"])
        with self.assertRaises(ValidationError):
            renew_loan(self.loan)

    def test_overdue_notices_are_sent_once_a_day(self):
        self.loan.due_on = self.today - timedelta(days=1)
        self.loan.save(update_fields=["due_on"])
        self.assertEqual(mark_overdue_loans(self.today), 1)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.OVERDUE)
        self.assertEqual(send_overdue_notices(self.today), 1)
        self.assertEqual(Notification.objects.filter(user=self.student.user).count(), 1)
        self.assertEqual(send_overdue_notices(self.today), 0)
        self.assertEqual(Notification.objects.filter(user=self.student.user).count(), 1)

    def test_a_fine_is_invoiced_once(self):
        self.loan.fine = Decimal("5.00")
        self.loan.save(update_fields=["fine"])
        invoice = assess_loan_fine(self.loan)
        self.loan.refresh_from_db()
        self.assertEqual(invoice.amount, Decimal("5.00"))
        self.assertEqual(self.loan.fine_invoice_id, invoice.pk)
        with self.assertRaises(ValidationError):
            assess_loan_fine(self.loan)


class LibraryScreenTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.institution = Institution.objects.get(slug="default")
        ensure_roles(self.institution)
        set_module(self.institution, "library", True)
        self.admin = UserFactory(
            username="library_admin", role="admin", institution=self.institution
        )
        self.student_user = UserFactory(
            username="library_student", role="student", institution=self.institution
        )
        self.student = StudentFactory(
            user=self.student_user,
            status="active",
        )
        self.book = Book.objects.create(
            institution=self.institution,
            title="Introduction to Law",
            author="A. Lawyer",
            isbn="9780000000001",
            copies_total=2,
            copies_available=2,
        )
        self.loan = Loan.objects.create(
            institution=self.institution,
            book=self.book,
            student=self.student,
            borrowed_on=self.today,
            due_on=self.today + timedelta(days=14),
        )

    def _borrower(self):
        role = Role.all_objects.create(
            institution=self.institution, code="library_borrower", name="Borrower"
        )
        RolePermission.objects.create(
            role=role, permission=Permission.objects.get(code="library.borrow")
        )
        user = UserFactory(
            username="library_borrower", role="student", institution=self.institution
        )
        user.primary_role = role
        user.save()
        return user

    def test_a_borrower_can_renew_from_the_loan_screen(self):
        self.client.force_login(self._borrower())
        page = self.client.get(reverse("library:loan_manage", args=[self.loan.pk]))
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            reverse("library:loan_renew", args=[self.loan.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.renewals, 1)

    def test_a_student_cannot_open_the_loan_screen(self):
        self.client.force_login(self.student_user)
        self.assertEqual(
            self.client.get(reverse("library:loan_manage", args=[self.loan.pk])).status_code,
            403,
        )

    def test_the_quick_lookup_finds_a_book_by_isbn(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("library:lookup"), {"q": "9780000000001"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Introduction to Law")

    def test_a_managed_fine_is_charged_to_the_student(self):
        self.loan.fine = Decimal("3.50")
        self.loan.save(update_fields=["fine"])
        self.client.force_login(self.admin)
        response = self.client.post(reverse("library:loan_fine", args=[self.loan.pk]))
        self.assertEqual(response.status_code, 302)
        self.loan.refresh_from_db()
        self.assertIsNotNone(self.loan.fine_invoice_id)


class LibraryReservationTests(TestCase):
    def test_a_reservation_starts_waiting(self):
        student = StudentFactory(status="active")
        book = Book.objects.create(title="Physics", copies_total=1, copies_available=1)
        reservation = Reservation.objects.create(book=book, student=student)
        self.assertEqual(reservation.status, Reservation.Status.WAITING)


class LibraryQrTests(TestCase):
    def setUp(self):
        self.institution = Institution.objects.get(slug="default")
        ensure_roles(self.institution)
        set_module(self.institution, "library", True)
        self.admin = UserFactory(
            username="qr_admin", role="admin", institution=self.institution
        )
        self.book = Book.objects.create(
            institution=self.institution,
            title="A Brief History of Time",
            isbn="9780000000099",
            copies_total=1,
            copies_available=1,
        )

    def test_a_book_gets_a_stable_qr_token(self):
        token = self.book.qr_token
        self.book.refresh_from_db()
        self.assertEqual(token, self.book.qr_token)
        self.assertIsNotNone(token)

    def test_the_qr_lookup_finds_a_book_by_token(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("library:qr_lookup"), {"code": str(self.book.qr_token)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A Brief History of Time")

    def test_the_qr_lookup_accepts_an_isbn(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("library:qr_lookup"), {"code": "9780000000099"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A Brief History of Time")

    def test_the_qr_image_is_a_png(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("library:book_qr_png", args=[self.book.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))
