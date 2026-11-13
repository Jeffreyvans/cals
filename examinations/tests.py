from datetime import date, time

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from academics.factories import (
    AcademicYearFactory,
    StudentFactory,
    SubjectFactory,
    TermFactory,
)
from accounts.factories import UserFactory
from core.models import Institution
from core.modules import set_module
from core.roles import ensure_roles
from examinations.models import Exam, ExamCandidate, ExamPaper
from examinations.services import (
    allocate_seats,
    board_rows,
    candidate_rows,
    clash_report,
    moderate_paper,
    release_paper,
    release_results,
    reopen_paper,
    student_paper_clashes,
    submit_paper,
)


class ExaminationTests(TestCase):
    def test_a_sitting_cannot_end_before_it_starts(self):
        exam = Exam(name="Mock exams", starts_on="2026-03-01", ends_on="2026-02-01")
        with self.assertRaises(ValidationError):
            exam.clean()

        exam.ends_on = "2026-03-10"
        exam.clean()

    def test_a_paper_needs_exactly_one_subject_or_course(self):
        paper = ExamPaper(date="2026-03-01", start_time="08:00")
        with self.assertRaises(ValidationError):
            paper.clean()


class ExaminationBase(TestCase):
    def setUp(self):
        self.institution = Institution.objects.get(slug="default")
        self.term = TermFactory(
            academic_year=AcademicYearFactory(name="2026", is_current=True)
        )
        self.exam = Exam.objects.create(
            institution=self.institution,
            name="Mid-year exams",
            term=self.term,
            starts_on=date(2026, 6, 1),
            ends_on=date(2026, 6, 12),
        )
        self.maths = SubjectFactory(code="MTH", name="Mathematics")
        self.english = SubjectFactory(code="ENG", name="English")
        self.student = StudentFactory(status="active")
        self.invigilator = UserFactory(
            username="exam_invigilator", role="lecturer", institution=self.institution
        )

    def make_paper(self, subject=None, start=time(9, 0), minutes=60, **extra):
        return ExamPaper.objects.create(
            institution=self.institution,
            exam=self.exam,
            subject=subject or self.maths,
            date=extra.pop("date", date(2026, 6, 1)),
            start_time=start,
            duration_minutes=minutes,
            **extra,
        )


class ExamClashTests(ExaminationBase):
    def test_a_paper_must_fall_inside_its_sitting(self):
        paper = self.make_paper(date=date(2026, 7, 1))
        with self.assertRaises(ValidationError) as caught:
            paper.clean()
        self.assertIn("outside the sitting", str(caught.exception))

    def test_a_venue_cannot_be_double_booked(self):
        self.make_paper(venue="Hall")
        second = self.make_paper(subject=self.english, start=time(9, 30), venue="hall")
        with self.assertRaises(ValidationError) as caught:
            second.clean()
        self.assertIn("Venue", str(caught.exception))

    def test_the_same_invigilator_cannot_cover_two_overlapping_papers(self):
        self.make_paper(invigilator=self.invigilator)
        second = self.make_paper(
            subject=self.english, start=time(9, 30), invigilator=self.invigilator
        )
        with self.assertRaises(ValidationError) as caught:
            second.clean()
        self.assertIn("already invigilating", str(caught.exception))

    def test_back_to_back_papers_do_not_clash(self):
        self.make_paper(venue="Hall", invigilator=self.invigilator, minutes=60)
        second = self.make_paper(
            subject=self.english,
            start=time(10, 0),
            minutes=60,
            venue="Hall",
            invigilator=self.invigilator,
        )
        second.clean()

    def test_a_candidate_cannot_sit_two_overlapping_papers(self):
        first = self.make_paper()
        second = self.make_paper(subject=self.english, start=time(9, 30))
        ExamCandidate.objects.create(
            institution=self.institution, paper=first, student=self.student
        )
        ExamCandidate.objects.create(
            institution=self.institution, paper=second, student=self.student
        )
        issues = clash_report()
        self.assertTrue(any(issue["kind"] == "Candidate" for issue in issues))
        self.assertTrue(student_paper_clashes(second, self.student))

    def test_a_candidate_cannot_be_entered_for_a_clashing_paper(self):
        first = self.make_paper()
        second = self.make_paper(subject=self.english, start=time(9, 30))
        ExamCandidate.objects.create(
            institution=self.institution, paper=first, student=self.student
        )
        candidate = ExamCandidate(paper=second, student=self.student)
        with self.assertRaises(ValidationError) as caught:
            candidate.clean()
        self.assertIn("already sitting", str(caught.exception))

    def test_the_report_does_not_repeat_the_same_venue_clash(self):
        self.make_paper(venue="Hall")
        self.make_paper(subject=self.english, start=time(9, 15), venue="Hall")
        venue_issues = [i for i in clash_report() if i["kind"] == "Venue"]
        self.assertEqual(len(venue_issues), 1)


class ExamOperationsTests(ExaminationBase):
    def test_seats_are_allocated_in_candidate_number_order(self):
        paper = self.make_paper()
        second_student = StudentFactory(status="active")
        later = ExamCandidate.objects.create(
            institution=self.institution,
            paper=paper,
            student=self.student,
            candidate_no="002",
        )
        earlier = ExamCandidate.objects.create(
            institution=self.institution,
            paper=paper,
            student=second_student,
            candidate_no="001",
        )
        self.assertEqual(allocate_seats(paper), 2)
        earlier.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(earlier.seat_no, "1")
        self.assertEqual(later.seat_no, "2")

    def test_releasing_results_publishes_every_candidate(self):
        paper = self.make_paper()
        candidate = ExamCandidate.objects.create(
            institution=self.institution,
            paper=paper,
            student=self.student,
            mark=72,
            grade="A",
        )
        submit_paper(paper)
        moderate_paper(paper)
        self.assertEqual(release_results(self.exam), 1)
        candidate.refresh_from_db()
        paper.refresh_from_db()
        self.exam.refresh_from_db()
        self.assertTrue(candidate.published)
        self.assertEqual(paper.mark_status, ExamPaper.MarkStatus.RELEASED)
        self.assertEqual(self.exam.status, Exam.Status.PUBLISHED)

    def test_results_cannot_be_released_before_moderation(self):
        paper = self.make_paper()
        ExamCandidate.objects.create(
            institution=self.institution,
            paper=paper,
            student=self.student,
            mark=72,
        )
        with self.assertRaises(ValidationError):
            release_results(self.exam)

    def test_a_paper_needs_every_mark_before_submission(self):
        paper = self.make_paper()
        ExamCandidate.objects.create(
            institution=self.institution, paper=paper, student=self.student
        )
        with self.assertRaises(ValidationError) as caught:
            submit_paper(paper)
        self.assertIn("no mark", str(caught.exception))

    def test_marks_are_locked_after_moderation_and_reopened_on_request(self):
        paper = self.make_paper()
        candidate = ExamCandidate.objects.create(
            institution=self.institution,
            paper=paper,
            student=self.student,
            mark=72,
            grade="A",
        )
        submit_paper(paper)
        moderate_paper(paper, note="Checked")
        paper.refresh_from_db()
        candidate.refresh_from_db()
        self.assertEqual(paper.mark_status, ExamPaper.MarkStatus.MODERATED)
        self.assertEqual(paper.moderation_note, "Checked")

        candidate.mark = 80
        with self.assertRaises(ValidationError):
            candidate.clean()

        reopen_paper(paper)
        candidate.refresh_from_db()
        candidate.mark = 80
        candidate.clean()

    def test_reopening_a_released_paper_withdraws_its_results(self):
        paper = self.make_paper()
        candidate = ExamCandidate.objects.create(
            institution=self.institution,
            paper=paper,
            student=self.student,
            mark=72,
        )
        submit_paper(paper)
        moderate_paper(paper)
        release_paper(paper)
        candidate.refresh_from_db()
        self.assertTrue(candidate.published)
        reopen_paper(paper)
        candidate.refresh_from_db()
        paper.refresh_from_db()
        self.assertFalse(candidate.published)
        self.assertEqual(paper.mark_status, ExamPaper.MarkStatus.ENTERING)

    def test_board_exports_only_include_moderated_results(self):
        paper = self.make_paper()
        ExamCandidate.objects.create(
            institution=self.institution,
            paper=paper,
            student=self.student,
            candidate_no="001",
            mark=72,
            grade="A",
        )
        self.assertEqual(list(board_rows(self.exam, "zimsec")), [])
        submit_paper(paper)
        moderate_paper(paper)
        zimsec = list(board_rows(self.exam, "zimsec"))
        self.assertEqual(len(zimsec), 1)
        self.assertIn("MTH", zimsec[0])
        hexco = list(board_rows(self.exam, "hexco"))
        self.assertEqual(len(hexco), 1)
        self.assertEqual(hexco[0][5], "MTH")

    def test_candidate_rows_follow_the_timetable(self):
        paper = self.make_paper()
        ExamCandidate.objects.create(
            institution=self.institution,
            paper=paper,
            student=self.student,
            candidate_no="007",
        )
        rows = list(candidate_rows(self.exam))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4], "007")
        self.assertEqual(rows[0][0], "Mid-year exams")


class ExaminationScreenTests(ExaminationBase):
    def setUp(self):
        super().setUp()
        ensure_roles(self.institution)
        set_module(self.institution, "exams", True)
        self.admin = UserFactory(
            username="exams_admin", role="admin", institution=self.institution
        )
        self.student_user = UserFactory(
            username="exams_student", role="student", institution=self.institution
        )
        self.paper = self.make_paper(venue="Hall")
        self.candidate = ExamCandidate.objects.create(
            institution=self.institution,
            paper=self.paper,
            student=self.student,
            candidate_no="001",
            mark=68,
            grade="B",
        )

    def test_the_clash_check_lists_a_double_booked_venue(self):
        self.make_paper(subject=self.english, start=time(9, 30), venue="Hall")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("examinations:clash_check"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Venue")

    def test_the_sitting_screen_shows_and_releases_results(self):
        self.client.force_login(self.admin)
        page = self.client.get(
            reverse("examinations:exam_manage", args=[self.exam.pk])
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Submit")
        for action in ("submit", "moderate"):
            self.client.post(
                reverse(
                    "examinations:paper_action", args=[self.paper.pk, action]
                )
            )
        response = self.client.post(
            reverse("examinations:exam_release", args=[self.exam.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.candidate.refresh_from_db()
        self.paper.refresh_from_db()
        self.assertTrue(self.candidate.published)
        self.assertEqual(self.paper.mark_status, ExamPaper.MarkStatus.RELEASED)

    def test_a_paper_can_be_moderated_from_the_sitting_screen(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("examinations:paper_action", args=[self.paper.pk, "submit"])
        )
        response = self.client.post(
            reverse("examinations:paper_action", args=[self.paper.pk, "moderate"]),
            {"note": "Second marker agreed"},
        )
        self.assertEqual(response.status_code, 302)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.mark_status, ExamPaper.MarkStatus.MODERATED)
        self.assertEqual(self.paper.moderation_note, "Second marker agreed")

    def test_the_seating_plan_allocates_seats(self):
        self.client.force_login(self.admin)
        page = self.client.get(
            reverse("examinations:seating_plan", args=[self.paper.pk])
        )
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            reverse("examinations:seating_plan", args=[self.paper.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.seat_no, "1")

    def test_the_candidate_export_is_a_csv(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("examinations:candidate_export"), {"exam": self.exam.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        body = response.content.decode()
        self.assertIn("Candidate no", body)
        self.assertIn("001", body)

    def test_the_zimsec_export_contains_subject_and_mark(self):
        submit_paper(self.paper)
        moderate_paper(self.paper)
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("examinations:zimsec_export"), {"exam": self.exam.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        body = response.content.decode()
        self.assertIn("Subject code", body)
        self.assertIn("MTH", body)
        self.assertIn("68", body)

    def test_the_hexco_export_uses_hexco_columns(self):
        submit_paper(self.paper)
        moderate_paper(self.paper)
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("examinations:hexco_export"), {"exam": self.exam.pk}
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Programme", body)
        self.assertIn("Course code", body)

    def test_a_student_cannot_open_the_exam_screens(self):
        self.client.force_login(self.student_user)
        self.assertEqual(
            self.client.get(reverse("examinations:clash_check")).status_code, 403
        )
        self.assertEqual(
            self.client.get(
                reverse("examinations:seating_plan", args=[self.paper.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("examinations:zimsec_export")).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("examinations:paper_action", args=[self.paper.pk, "submit"])
            ).status_code,
            403,
        )
