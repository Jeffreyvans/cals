"""One end-to-end pilot journey, from a new institution to PDF documents.

The test deliberately goes through the real platform and institution screens.
A helper asserts every response, so a silent form error or a broken PDF fails
the scenario instead of leaving half the journey untested.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from academics.models import (
    AcademicYear,
    Attendance,
    DocumentVerification,
    Enrollment,
    GradeLevel,
    Programme,
    ResultSheet,
    SchoolClass,
    Student,
    Subject,
    Term,
)
from core.models import ImportBatch, Institution
from core.roles import ensure_roles
from core.tenancy import use_institution
from finance.models import FeeStructure, Invoice, Payment
from platform_admin.tests.base import STRONG_PASSWORD, PlatformTestBase

User = get_user_model()
MESSY_SAMPLE = (
    Path(__file__).resolve().parents[1] / "samples" / "pilot_students_messy.csv"
)


class PilotScenarioTests(PlatformTestBase):
    """Create one school and carry it through the complete pilot workflow."""

    def get_ok(self, url, *contains):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f"GET {url} returned {response.status_code}")
        for text in contains:
            self.assertContains(response, text)
        return response

    def post_redirect(self, url, data, expected_url=None):
        response = self.client.post(url, data)
        self.assertEqual(
            response.status_code, 302,
            f"POST {url} returned {response.status_code}: {getattr(response, 'context', None) and getattr(response.context.get('form'), 'errors', None)}",
        )
        if expected_url is not None:
            self.assertEqual(response.url, expected_url)
        return response

    def get_pdf(self, url, marker):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f"GET {url} returned {response.status_code}")
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn(marker, response["Content-Disposition"])
        return response

    def test_complete_pilot_journey(self):
        # 1. Platform administrator creates the institution.
        self.login(self.main)
        create_url = reverse("platform_admin:institution_create")
        self.get_ok(create_url)
        response = self.post_redirect(create_url, {
            "name": "Pilot High School",
            "slug": "pilot-high-school",
            "type": "secondary",
            "address": "1 Pilot Road, Harare",
            "contact_email": "office@pilot-high-school.example",
            "contact_phone": "+263771000000",
            "access_grant_max_hours": "24",
        })
        institution = Institution.objects.get(slug="pilot-high-school")
        self.assertEqual(
            response.url,
            reverse("platform_admin:institution_detail", args=[institution.pk]),
        )
        self.get_ok(response.url, "Pilot High School")

        # 2. Platform administrator creates the first school administrator.
        admin_url = reverse(
            "platform_admin:onboarding_admin_create", args=[institution.pk])
        self.get_ok(admin_url)
        self.post_redirect(admin_url, {
            "full_name": "Farai Moyo",
            "email": "farai@pilot-high-school.example",
            "phone": "+263771000001",
            "access_mode": "temp_password",
        })
        admin = institution.users.get(email="farai@pilot-high-school.example")
        self.assertEqual(admin.role, "admin")
        self.assertEqual(admin.institution_id, institution.pk)

        credential_url = reverse("platform_admin:onboarding_credential", args=[
            institution.pk, admin.pk])
        credential = self.get_ok(credential_url, "Temporary password")
        temporary_password = credential.context["secret"]
        self.assertTrue(temporary_password)
        self.assertEqual(self.client.get(credential_url).status_code, 302)

        with use_institution(institution):
            roles = ensure_roles(institution)
            registrar = User.objects.create_user(
                "pilot-registrar", email="registrar@pilot-high-school.example",
                password=STRONG_PASSWORD, role="registrar", institution=institution,
                primary_role=roles["registrar"])
            bursar = User.objects.create_user(
                "pilot-bursar", email="bursar@pilot-high-school.example",
                password=STRONG_PASSWORD, role="bursar", institution=institution,
                primary_role=roles["bursar"])
            lecturer = User.objects.create_user(
                "pilot-lecturer", email="lecturer@pilot-high-school.example",
                password=STRONG_PASSWORD, role="lecturer", institution=institution,
                primary_role=roles["lecturer"])

        # 3. The administrator signs in, changes the temporary password and
        #    completes the five guided setup steps.
        self.client.logout()
        login = self.client.post(reverse("login"), {
            "username": admin.username, "password": temporary_password})
        self.assertEqual(login.status_code, 302)
        dashboard = reverse("dashboard")
        password_change = reverse("password_change")
        forced = self.client.get(dashboard)
        self.assertEqual(forced.status_code, 302)
        self.assertEqual(forced.url, f"{password_change}?next={dashboard}")
        self.get_ok(forced.url)
        self.post_redirect(forced.url, {
            "old_password": temporary_password,
            "new_password1": STRONG_PASSWORD,
            "new_password2": STRONG_PASSWORD,
            "next": dashboard,
        }, dashboard)
        setup = reverse("institution_setup")
        self.assertEqual(self.client.get(dashboard).url, setup)
        self.get_ok(setup)

        self.post_redirect(setup, {"action": "skip"}, setup)  # password already set
        self.post_redirect(setup, {}, setup)  # no logo needed for the pilot
        self.post_redirect(setup, {
            "tagline": "A one-school pilot",
            "contact_email": "office@pilot-high-school.example",
            "contact_phone": "+263771000000",
            "primary_color": "#123456",
            "accent_color": "#abcdef",
        }, setup)
        self.post_redirect(setup, {
            "year_name": "2026",
            "year_start": "2026-01-01",
            "year_end": "2026-12-31",
            "term_name": "Term 1",
            "term_start": "2026-01-12",
            "term_end": "2026-04-10",
        }, setup)
        link_step = self.client.post(setup, {
            "action": "next", "label": "Pilot staff sign-in", "kind": "staff"})
        self.assertEqual(link_step.status_code, 200)
        self.assertTrue(link_step.context["created_url"])
        self.post_redirect(setup, {"action": "finish"}, dashboard)
        self.get_ok(dashboard)

        # 4. Academic structure: school mode, a level, a class, a programme and
        #    a subject taught by the lecturer created above.
        self.post_redirect(reverse("school_settings"), {"mode": "school"},
                           reverse("school_settings"))
        self.post_redirect(reverse("grades_classes"), {
            "action": "add_level", "name": "Form 2", "code": "F2",
            "kind": "secondary", "order": "2", "is_active": "on",
        }, reverse("grades_classes"))
        with use_institution(institution):
            level = GradeLevel.objects.get(code="F2")
        self.post_redirect(reverse("class_list"), {
            "name": "Form 2", "level": "secondary", "grade_level": level.pk,
            "capacity": "40", "room": "A1", "is_active": "on",
        }, reverse("class_list"))
        self.post_redirect(reverse("programme_list"), {
            "action": "new_programme", "name": "Pilot Diploma", "code": "PDIP",
            "level": "diploma", "duration_months": "12", "tuition_fee": "0.00",
        }, reverse("programme_list"))
        self.post_redirect(reverse("subject_list"), {
            "code": "MAT", "name": "Mathematics", "level": "secondary",
            "lecturer": lecturer.pk,
        }, reverse("subject_list"))
        with use_institution(institution):
            school_class = SchoolClass.objects.get(name="Form 2")
            programme = Programme.objects.get(code="PDIP")
            subject = Subject.objects.get(code="MAT")
            year = AcademicYear.objects.get(is_current=True)
            term = Term.objects.get(is_current=True)
        self.assertEqual(school_class.grade_level_id, level.pk)
        self.assertEqual(subject.lecturer_id, lecturer.pk)

        # 5. Import the documented messy file: two good rows, one rejected row.
        upload = SimpleUploadedFile(
            MESSY_SAMPLE.name, MESSY_SAMPLE.read_bytes(), content_type="text/csv")
        self.post_redirect(reverse("import_start"), {
            "kind": "students", "file": upload, "access_mode": "none",
        })
        batch = ImportBatch.all_objects.filter(institution=institution).latest("pk")
        self.assertEqual(batch.filename, MESSY_SAMPLE.name)
        self.assertIn("First Name (2)", batch.headers)
        self.assertEqual(batch.mapping["first_name"], "First Name")
        self.assertEqual(batch.mapping["last_name"], "Last Name")
        self.post_redirect(
            reverse("import_map", args=[batch.pk]),
            {f"col_{key}": column for key, column in batch.mapping.items()},
            reverse("import_preview", args=[batch.pk]),
        )
        preview = self.get_ok(
            reverse("import_preview", args=[batch.pk]), "2 ready", "1 with a problem")
        self.assertEqual(preview.context["ready"], 2)
        self.assertEqual(preview.context["broken"], 1)
        self.post_redirect(
            reverse("import_run", args=[batch.pk]), {},
            reverse("import_result", args=[batch.pk]),
        )
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.IMPORTED)
        self.assertEqual(batch.imported_count, 2)
        self.assertEqual(batch.error_count, 1)
        self.get_ok(reverse("import_result", args=[batch.pk]), batch.filename)

        with use_institution(institution):
            students = list(Student.objects.filter(
                student_no__in=["PIL-001", "PIL-002"]).order_by("student_no"))
        self.assertEqual([student.student_no for student in students],
                         ["PIL-001", "PIL-002"])
        self.assertFalse(Student.all_objects.filter(
            institution=institution, student_no="PIL-003").exists())

        # 6. Registrar enrols both imported students in the subject.
        self.client.force_login(registrar)
        for student in students:
            self.post_redirect(
                reverse("enroll", args=[student.pk]),
                {"subject": subject.pk, "term": term.pk},
                reverse("student_detail", args=[student.pk]),
            )
        with use_institution(institution):
            enrollments = list(Enrollment.objects.filter(
                subject=subject, term=term, student__in=students
            ).order_by("student__student_no"))
        self.assertEqual(len(enrollments), 2)

        # 7. Bursar creates a fee structure, generates invoices, records a
        #    payment and downloads a real receipt PDF.
        self.client.force_login(bursar)
        self.post_redirect(reverse("fee_structure_create"), {
            "code": "PILOT-2026", "name": "Pilot Term 1 fees",
            "academic_year": year.pk, "programme": programme.pk,
            "term": term.pk, "is_active": "on",
        })
        with use_institution(institution):
            structure = FeeStructure.objects.get(code="PILOT-2026")
        self.assertEqual(
            self.client.post(
                reverse("fee_structure_detail", args=[structure.pk]),
                {"name": "Tuition", "kind": "tuition", "amount": "500.00",
                 "tax_category": "exempt", "is_mandatory": "on"},
            ).status_code,
            302,
        )
        self.get_ok(reverse("fee_structure_detail", args=[structure.pk]),
                    "Tuition", "500.00")

        preview_invoices = self.client.post(reverse("generate_invoices"), {
            "structure": structure.pk, "term": term.pk})
        self.assertEqual(preview_invoices.status_code, 200)
        self.assertContains(preview_invoices, "500.00")
        confirmed_invoices = self.client.post(reverse("generate_invoices"), {
            "structure": structure.pk, "term": term.pk, "action": "confirm"})
        self.assertEqual(confirmed_invoices.status_code, 200)
        self.assertContains(confirmed_invoices, "2 invoices created")
        with use_institution(institution):
            invoices = list(Invoice.objects.filter(
                structure=structure, term=term).order_by("student__student_no"))
        self.assertEqual(len(invoices), 2)
        self.assertEqual([invoice.amount for invoice in invoices],
                         [Decimal("500.00"), Decimal("500.00")])
        self.get_ok(reverse("invoice_pdf", args=[invoices[0].pk]))

        payment_response = self.client.post(
            reverse("record_payment", args=[students[0].pk]), {
                "invoice": invoices[0].pk, "amount": "500.00", "method": "cash",
                "reference": "PILOT-RECEIPT-1",
            })
        self.assertEqual(payment_response.status_code, 302)
        with use_institution(institution):
            payment = Payment.objects.get(reference="PILOT-RECEIPT-1")
            invoices[0].refresh_from_db()
        self.assertEqual(invoices[0].balance, Decimal("0.00"))
        self.assertEqual(payment_response.url,
                         reverse("receipt", args=[payment.pk]))
        self.get_ok(reverse("receipt", args=[payment.pk]), payment.receipt_no)
        self.get_pdf(reverse("receipt_pdf", args=[payment.pk]), "receipt-")
        self.get_ok(reverse("verify_receipt", args=[payment.verification_token]),
                    payment.receipt_no)

        # 8. Lecturer records attendance and marks, then submits the sheet.
        self.client.force_login(lecturer)
        roster = reverse("subject_roster", args=[subject.pk])
        self.get_ok(roster, "Mathematics")
        self.post_redirect(roster, {
            "attendance_date": "2026-03-03",
            f"status_{enrollments[0].pk}": "present",
            f"mark_{enrollments[0].pk}": "78.50",
            f"status_{enrollments[1].pk}": "absent",
            f"reason_{enrollments[1].pk}": "Medical appointment",
            f"mark_{enrollments[1].pk}": "64.00",
        }, roster)
        with use_institution(institution):
            enrollments = list(Enrollment.objects.filter(
                subject=subject, term=term, student__in=students
            ).order_by("student__student_no"))
            attendance = list(Attendance.objects.filter(
                enrollment__in=enrollments).order_by("enrollment__student__student_no"))
        self.assertEqual([row.mark for row in enrollments],
                         [Decimal("78.50"), Decimal("64.00")])
        self.assertEqual([row.status for row in attendance], ["present", "absent"])

        workflow = reverse("results_workflow", args=["subject", subject.pk])
        self.post_redirect(workflow, {"term": term.pk, "action": "submit"}, workflow)
        with use_institution(institution):
            sheet = ResultSheet.objects.get(subject=subject, term=term)
        self.assertEqual(sheet.status, ResultSheet.Status.SUBMITTED)

        # 9. Registrar moderates, approves and releases the results.
        self.client.force_login(registrar)
        for action, status in (
            ("moderate", ResultSheet.Status.MODERATED),
            ("approve", ResultSheet.Status.APPROVED),
            ("release", ResultSheet.Status.RELEASED),
        ):
            self.post_redirect(workflow, {"term": term.pk, "action": action}, workflow)
            sheet.refresh_from_db()
            self.assertEqual(sheet.status, status)

        # 10. Registrar issues a report-card PDF and the public verification
        #     page confirms its identity without exposing marks.
        report = reverse("student_report", args=[students[0].pk])
        self.get_ok(report, students[0].name)
        self.get_pdf(reverse("student_report_pdf", args=[students[0].pk]),
                     "report-card")
        with use_institution(institution):
            document = DocumentVerification.objects.get(
                student=students[0], kind=DocumentVerification.Kind.REPORT_CARD)
        self.client.logout()
        self.get_ok(reverse("verify_document", args=[document.token]),
                    "Document verified", students[0].name)
