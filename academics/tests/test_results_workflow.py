from datetime import date
from decimal import Decimal

from django.urls import reverse

from academics.models import (
    Guardian,
    ResultClearance,
    ResultSheet,
)
from academics.services import (
    ResultsError,
    approve_results,
    clear_result_hold,
    moderate_results,
    release_results,
    result_sheet_map,
    return_results,
    submit_results,
)
from accounts.models import User
from core.models import AuditEntry
from finance.models import Invoice

from .test_reports import BaseAssessment


class ResultWorkflowServiceTests(BaseAssessment):
    def submit(self):
        return submit_results("course", self.course, self.term, self.lecturer)

    def test_submitting_requires_at_least_one_mark(self):
        with self.assertRaisesMessage(ResultsError, "Enter at least one mark"):
            self.submit()

    def test_sheet_moves_through_moderation_approval_and_release(self):
        self.e_a.mark = Decimal("80")
        self.e_a.save()
        sheet = self.submit()
        self.assertEqual(sheet.status, ResultSheet.Status.SUBMITTED)
        self.assertEqual(sheet.entered_by, self.lecturer)
        self.assertIsNotNone(sheet.submitted_at)
        self.assertEqual(self.submit().pk, sheet.pk)

        moderate_results(sheet, self.registrar)
        self.assertEqual(sheet.status, ResultSheet.Status.MODERATED)
        self.assertEqual(sheet.moderated_by, self.registrar)

        approve_results(sheet, self.registrar)
        self.assertEqual(sheet.status, ResultSheet.Status.APPROVED)
        self.assertEqual(sheet.approved_by, self.registrar)

        release_results(sheet, self.registrar)
        self.assertEqual(sheet.status, ResultSheet.Status.RELEASED)
        self.assertEqual(sheet.released_by, self.registrar)
        self.assertIsNotNone(sheet.released_at)

        actions = set(AuditEntry.objects.values_list("action", flat=True))
        self.assertTrue({
            "academics.results_submitted",
            "academics.results_moderated",
            "academics.results_approved",
            "academics.results_released",
        }.issubset(actions))

    def test_stages_cannot_be_skipped(self):
        self.e_a.mark = Decimal("80")
        self.e_a.save()
        sheet = self.submit()
        with self.assertRaisesMessage(ResultsError, "approved"):
            release_results(sheet, self.registrar)
        with self.assertRaisesMessage(ResultsError, "moderated"):
            approve_results(sheet, self.registrar)

    def test_returning_a_sheet_unlocks_it_for_changes(self):
        self.e_a.mark = Decimal("80")
        self.e_a.save()
        sheet = self.submit()
        moderate_results(sheet, self.registrar)
        return_results(sheet, self.registrar, "Check question 3")
        self.assertEqual(sheet.status, ResultSheet.Status.SUBMITTED)
        self.assertIsNone(sheet.moderated_by)
        self.assertEqual(sheet.note, "Check question 3")

    def test_sheet_map_uses_the_course_or_subject_and_term(self):
        self.e_a.mark = Decimal("80")
        self.e_a.save()
        sheet = self.submit()
        self.assertEqual(
            result_sheet_map([self.e_a])[(self.course.pk, None, self.term.pk)],
            sheet)


class ResultWorkflowViewTests(BaseAssessment):
    def url(self):
        return reverse("results_workflow", args=["course", self.course.pk])

    def test_lecturer_submits_and_registrar_completes_the_workflow(self):
        self.e_a.mark = Decimal("75")
        self.e_a.save()
        self.client.force_login(self.lecturer)
        resp = self.client.post(self.url(), {"term": self.term.pk,
                                             "action": "submit"})
        self.assertRedirects(resp, self.url())
        sheet = ResultSheet.objects.get(course=self.course, term=self.term)
        self.assertEqual(sheet.status, ResultSheet.Status.SUBMITTED)

        self.client.force_login(self.registrar)
        for action, status in [("moderate", ResultSheet.Status.MODERATED),
                               ("approve", ResultSheet.Status.APPROVED),
                               ("release", ResultSheet.Status.RELEASED)]:
            resp = self.client.post(self.url(), {"term": self.term.pk,
                                                 "action": action})
            self.assertRedirects(resp, self.url())
            sheet.refresh_from_db()
            self.assertEqual(sheet.status, status)

    def test_lecturer_cannot_moderate_their_own_results(self):
        self.e_a.mark = Decimal("75")
        self.e_a.save()
        submit_results("course", self.course, self.term, self.lecturer)
        self.client.force_login(self.lecturer)
        resp = self.client.post(self.url(), {"term": self.term.pk,
                                             "action": "moderate"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(ResultSheet.objects.get().status,
                         ResultSheet.Status.SUBMITTED)

    def test_lecturer_cannot_resubmit_a_reviewed_sheet(self):
        self.e_a.mark = Decimal("75")
        self.e_a.save()
        sheet = submit_results("course", self.course, self.term, self.lecturer)
        moderate_results(sheet, self.registrar)
        self.client.force_login(self.lecturer)
        self.client.post(self.url(), {"term": self.term.pk, "action": "submit"})
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.Status.MODERATED)

    def test_other_lecturer_and_students_are_refused(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.client.force_login(self.a.user)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_registrar_can_open_the_workflow(self):
        self.client.force_login(self.registrar)
        resp = self.client.get(self.url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Results workflow")


class MarkEditLockTests(BaseAssessment):
    def url(self):
        return reverse("subject_roster", args=[self.subject.pk])

    def test_marks_can_change_while_submitted_but_not_after_moderation(self):
        self.sa.mark = Decimal("50")
        self.sa.save()
        submit_results("subject", self.subject, self.term, self.lecturer)
        self.client.force_login(self.lecturer)
        self.client.post(self.url(), {f"mark_{self.sa.pk}": "60"})
        self.sa.refresh_from_db()
        self.assertEqual(self.sa.mark, Decimal("60"))

        sheet = ResultSheet.objects.get(subject=self.subject, term=self.term)
        moderate_results(sheet, self.registrar)
        self.client.post(self.url(), {f"mark_{self.sa.pk}": "90"})
        self.sa.refresh_from_db()
        self.assertEqual(self.sa.mark, Decimal("60"))

    def test_returning_the_sheet_lets_the_teacher_edit_again(self):
        self.sa.mark = Decimal("50")
        self.sa.save()
        sheet = submit_results("subject", self.subject, self.term, self.lecturer)
        moderate_results(sheet, self.registrar)
        return_results(sheet, self.registrar)
        self.client.force_login(self.lecturer)
        self.client.post(self.url(), {f"mark_{self.sa.pk}": "70"})
        self.sa.refresh_from_db()
        self.assertEqual(self.sa.mark, Decimal("70"))


class ResultVisibilityTests(BaseAssessment):
    def release(self):
        self.e_a.mark = Decimal("67")
        self.e_a.save()
        sheet = submit_results("course", self.course, self.term, self.lecturer)
        moderate_results(sheet, self.registrar)
        approve_results(sheet, self.registrar)
        release_results(sheet, self.registrar)
        return sheet

    def test_submitted_results_are_hidden_from_students(self):
        self.e_a.mark = Decimal("67")
        self.e_a.save()
        submit_results("course", self.course, self.term, self.lecturer)
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("my_report"))
        self.assertNotContains(resp, "67.00")
        self.assertContains(resp, "Awaiting release")

    def test_released_results_are_visible_to_students(self):
        self.release()
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("my_report"))
        self.assertContains(resp, "67.00")

    def test_portal_hides_unreleased_marks(self):
        self.e_a.mark = Decimal("67")
        self.e_a.save()
        submit_results("course", self.course, self.term, self.lecturer)
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("student_portal"))
        self.assertNotContains(resp, "67.00")
        self.assertContains(resp, "Awaiting release")

    def test_parent_only_sees_released_marks(self):
        parent = User.objects.create_user("parent", password="pw123456",
                                          role="parent")
        Guardian.objects.create(parent=parent, student=self.a)
        self.e_a.mark = Decimal("67")
        self.e_a.save()
        submit_results("course", self.course, self.term, self.lecturer)
        self.client.force_login(parent)
        url = reverse("child_detail", args=[self.a.pk])
        resp = self.client.get(url)
        self.assertNotContains(resp, "67.00")
        self.assertContains(resp, "Awaiting release")

        sheet = ResultSheet.objects.get(course=self.course, term=self.term)
        moderate_results(sheet, self.registrar)
        approve_results(sheet, self.registrar)
        release_results(sheet, self.registrar)
        resp = self.client.get(url)
        self.assertContains(resp, "67.00")

    def test_outstanding_fees_withhold_released_results(self):
        self.release()
        Invoice.objects.create(student=self.a, description="Term fees",
                               amount=Decimal("100.00"),
                               due_date=date(2026, 2, 1))
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("my_report"))
        self.assertNotContains(resp, "67.00")
        self.assertContains(resp, "Fees outstanding")
        self.assertContains(resp, "Results withheld")

    def test_bursar_clearance_needs_a_reason_and_is_audited(self):
        self.release()
        Invoice.objects.create(student=self.a, description="Term fees",
                               amount=Decimal("100.00"),
                               due_date=date(2026, 2, 1))
        self.client.force_login(self.bursar)
        url = reverse("student_report", args=[self.a.pk])
        self.client.post(url, {"action": "clear", "term": self.term.pk,
                               "reason": "  "})
        self.assertFalse(ResultClearance.objects.exists())

        self.client.post(url, {"action": "clear", "term": self.term.pk,
                               "reason": "Director approved a payment plan"})
        clearance = ResultClearance.objects.get(student=self.a, term=self.term)
        self.assertEqual(clearance.granted_by, self.bursar)
        self.assertTrue(AuditEntry.objects.filter(
            action="academics.results_cleared", actor=self.bursar).exists())

        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("my_report"))
        self.assertContains(resp, "67.00")

    def test_registrar_cannot_grant_a_fee_clearance(self):
        self.release()
        Invoice.objects.create(student=self.a, description="Term fees",
                               amount=Decimal("100.00"),
                               due_date=date(2026, 2, 1))
        self.client.force_login(self.registrar)
        resp = self.client.post(reverse("student_report", args=[self.a.pk]),
                                {"action": "clear", "term": self.term.pk,
                                 "reason": "Please"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(ResultClearance.objects.exists())

    def test_clearance_service_rejects_an_empty_reason(self):
        with self.assertRaisesMessage(ResultsError, "Give a reason"):
            clear_result_hold(self.a, self.term, self.bursar, " ")

    def test_legacy_results_without_a_sheet_stay_visible(self):
        self.e_a.mark = Decimal("64")
        self.e_a.save()
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("my_report"))
        self.assertContains(resp, "64.00")
