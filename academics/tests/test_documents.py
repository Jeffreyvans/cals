import uuid
from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse

from academics.models import DocumentVerification, Guardian
from academics.tests.test_reports import BaseAssessment
from accounts.models import User


class DocumentPdfTests(BaseAssessment):
    def setUp(self):
        self.client.force_login(self.registrar)

    def test_staff_can_download_a_report_card_pdf(self):
        response = self.client.get(reverse("student_report_pdf", args=[self.a.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("report-card", response["Content-Disposition"])
        document = DocumentVerification.objects.get(
            student=self.a, kind=DocumentVerification.Kind.REPORT_CARD)
        self.assertEqual(document.issued_by, self.registrar)

    def test_staff_can_download_a_transcript_pdf(self):
        response = self.client.get(
            reverse("student_transcript_pdf", args=[self.a.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("transcript", response["Content-Disposition"])
        self.assertTrue(DocumentVerification.objects.filter(
            student=self.a, kind=DocumentVerification.Kind.TRANSCRIPT).exists())

    def test_repeated_downloads_keep_the_same_verification_token(self):
        url = reverse("student_report_pdf", args=[self.a.pk])
        self.client.get(url)
        first = DocumentVerification.objects.get(student=self.a).token
        self.client.get(url)
        self.assertEqual(DocumentVerification.objects.filter(student=self.a).count(), 1)
        self.assertEqual(DocumentVerification.objects.get(student=self.a).token, first)

    def test_pdf_embeds_the_public_verification_url(self):
        with patch("academics.views.build_academic_document",
                   return_value=b"%PDF-test") as build:
            self.client.get(reverse("student_report_pdf", args=[self.a.pk]))
        document = DocumentVerification.objects.get(student=self.a)
        verify_url = build.call_args.args[3]
        self.assertIn(f"/verify/{document.token}/", verify_url)

    def test_student_can_download_only_their_own_documents(self):
        self.client.force_login(self.a.user)
        report = self.client.get(reverse("my_report_pdf"))
        transcript = self.client.get(reverse("my_transcript_pdf"))
        self.assertEqual(report.status_code, 200)
        self.assertEqual(transcript.status_code, 200)
        self.assertEqual(DocumentVerification.objects.filter(student=self.a).count(), 2)
        forbidden = self.client.get(reverse("student_report_pdf", args=[self.b.pk]))
        self.assertEqual(forbidden.status_code, 403)

    def test_parent_can_download_a_linked_child_document(self):
        parent = User.objects.create_user("parent", password="pw123456",
                                          role="parent")
        Guardian.objects.create(parent=parent, student=self.a)
        self.client.force_login(parent)
        report = self.client.get(reverse("child_report_pdf", args=[self.a.pk]))
        transcript = self.client.get(
            reverse("child_transcript_pdf", args=[self.a.pk]))
        self.assertEqual(report.status_code, 200)
        self.assertEqual(transcript.status_code, 200)

    def test_parent_cannot_download_an_unlinked_child_document(self):
        parent = User.objects.create_user("other-parent", password="pw123456",
                                          role="parent")
        self.client.force_login(parent)
        response = self.client.get(reverse("child_report_pdf", args=[self.a.pk]))
        self.assertEqual(response.status_code, 404)


class DocumentVerificationPageTests(BaseAssessment):
    def issue(self, kind=DocumentVerification.Kind.REPORT_CARD):
        self.client.force_login(self.registrar)
        self.client.get(reverse("student_report_pdf", args=[self.a.pk]))
        return DocumentVerification.objects.get(student=self.a, kind=kind)

    def test_verification_page_is_public_and_shows_the_document_identity(self):
        document = self.issue()
        self.client.logout()
        response = self.client.get(
            reverse("verify_document", args=[document.token]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Document verified")
        self.assertContains(response, self.a.name)
        self.assertContains(response, self.a.student_no)
        self.assertContains(response, "Report card")

    def test_verification_page_does_not_expose_marks(self):
        self.e_a.mark = Decimal("63.47")
        self.e_a.save()
        document = self.issue()
        self.client.logout()
        response = self.client.get(
            reverse("verify_document", args=[document.token]))
        self.assertNotContains(response, "63.47")

    def test_unknown_token_returns_not_found(self):
        self.client.logout()
        response = self.client.get(reverse("verify_document", args=[uuid.uuid4()]))
        self.assertEqual(response.status_code, 404)
