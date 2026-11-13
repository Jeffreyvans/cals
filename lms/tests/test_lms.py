from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.factories import (
    CourseFactory,
    EnrollmentFactory,
    ProgrammeFactory,
    StudentFactory,
    TermFactory,
)
from accounts.factories import UserFactory
from core.models import Institution
from core.modules import set_module
from lms.models import (
    Assignment,
    Choice,
    CourseSite,
    Forum,
    GradeEntry,
    GradeItem,
    LiveSession,
    Question,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    Submission,
)
from lms.services import (
    check_assignment_plagiarism,
    ensure_default_categories,
    grade_attempt_answers,
    grade_submission,
    gradebook_data,
)


class LmsBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institution = Institution.objects.get(slug="default")
        set_module(cls.institution, "lms", True)
        cls.lecturer = UserFactory(role="lecturer", username="lecturer")
        cls.student_user = UserFactory(role="student", username="student")
        cls.outsider = UserFactory(role="student", username="outsider")
        cls.programme = ProgrammeFactory()
        cls.term = TermFactory()
        cls.course = CourseFactory(programme=cls.programme, lecturer=cls.lecturer)
        cls.student = StudentFactory(user=cls.student_user, programme=cls.programme)
        EnrollmentFactory(student=cls.student, course=cls.course, term=cls.term)
        cls.site = CourseSite.objects.create(
            course=cls.course, title="Web Development", created_by=cls.lecturer)

    def login_lecturer(self):
        self.client.force_login(self.lecturer)

    def login_student(self):
        self.client.force_login(self.student_user)


class CourseSiteTests(LmsBase):
    def test_lecturer_can_create_a_site_with_default_grade_categories(self):
        other_course = CourseFactory(programme=self.programme, lecturer=self.lecturer)
        self.login_lecturer()
        response = self.client.post(reverse("lms:site_create"), {
            "course": other_course.pk,
            "title": "Data Structures",
            "description": "Weekly lessons",
            "welcome": "Welcome",
            "is_published": "on",
        })
        site = CourseSite.objects.get(course=other_course)
        self.assertRedirects(response, reverse("lms:course_site", args=[site.pk]))
        self.assertEqual(site.grade_categories.count(), 3)

    def test_enrolled_student_can_open_a_published_site(self):
        self.login_student()
        response = self.client.get(reverse("lms:course_site", args=[self.site.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Web Development")

    def test_an_unenrolled_student_cannot_open_the_site(self):
        self.client.force_login(self.outsider)
        response = self.client.get(reverse("lms:course_site", args=[self.site.pk]))
        self.assertEqual(response.status_code, 403)

    def test_a_student_cannot_create_a_site(self):
        self.login_student()
        response = self.client.post(reverse("lms:site_create"), {})
        self.assertEqual(response.status_code, 403)


class AssignmentTests(LmsBase):
    def setUp(self):
        self.login_lecturer()
        self.assignment = Assignment.objects.create(
            site=self.site, title="Build a page",
            instructions="Use semantic HTML", due_at=timezone.now() + timedelta(days=2),
            max_score=Decimal("100"), created_by=self.lecturer)
        self.login_student()
        self.submission = Submission.objects.create(
            assignment=self.assignment, student=self.student, text="My answer")

    def test_assignment_creates_a_grade_item_in_the_assignments_category(self):
        item = GradeItem.objects.get(assignment=self.assignment)
        self.assertEqual(item.category.name, "Assignments")
        self.assertEqual(item.max_score, Decimal("100"))

    def test_grading_a_submission_syncs_the_gradebook(self):
        grade_submission(self.submission, Decimal("80"), "Good work", self.lecturer)
        entry = GradeEntry.objects.get(item__assignment=self.assignment,
                                       student=self.student)
        self.assertEqual(entry.score, Decimal("80"))

    def test_late_penalty_is_applied_before_the_grade_is_saved(self):
        self.assignment.due_at = timezone.now() - timedelta(days=1)
        self.assignment.late_policy = Assignment.LatePolicy.PENALTY
        self.assignment.late_penalty_percent = Decimal("10")
        self.assignment.save()
        self.submission.refresh_from_db()
        grade_submission(self.submission, Decimal("80"), "", self.lecturer)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.mark, Decimal("72.00"))

    def test_lecturer_can_open_the_grading_screen(self):
        self.login_lecturer()
        response = self.client.get(reverse("lms:assignment_grade", args=[
            self.assignment.pk, self.submission.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My answer")


class QuizTests(LmsBase):
    def setUp(self):
        self.quiz = Quiz.objects.create(
            site=self.site, title="HTML quiz", max_attempts=2,
            randomize_questions=False, created_by=self.lecturer)
        self.question = Question.objects.create(
            quiz=self.quiz, kind=Question.Kind.MULTIPLE_CHOICE,
            text="Which tag makes a heading?", points=Decimal("5"))
        self.correct = Choice.objects.create(
            question=self.question, text="h1", is_correct=True)
        self.wrong = Choice.objects.create(question=self.question, text="p")

    def test_a_correct_choice_is_auto_graded_and_synced(self):
        attempt = QuizAttempt.objects.create(
            quiz=self.quiz, student=self.student,
            question_order=[self.question.pk])
        QuizAnswer.objects.create(
            attempt=attempt, question=self.question, selected_choice=self.correct)
        grade_attempt_answers(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, Decimal("5.00"))
        self.assertEqual(attempt.status, QuizAttempt.Status.GRADED)
        entry = GradeEntry.objects.get(item__quiz=self.quiz, student=self.student)
        self.assertEqual(entry.score, Decimal("5.00"))

    def test_a_wrong_choice_scores_zero(self):
        attempt = QuizAttempt.objects.create(
            quiz=self.quiz, student=self.student,
            question_order=[self.question.pk])
        QuizAnswer.objects.create(
            attempt=attempt, question=self.question, selected_choice=self.wrong)
        grade_attempt_answers(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, Decimal("0.00"))

    def test_the_quiz_grade_item_tracks_question_points(self):
        item = GradeItem.objects.get(quiz=self.quiz)
        self.assertEqual(item.max_score, Decimal("5.00"))

    def test_student_can_start_an_open_quiz(self):
        self.login_student()
        response = self.client.post(reverse("lms:quiz_start", args=[self.quiz.pk]))
        attempt = QuizAttempt.objects.get(quiz=self.quiz, student=self.student)
        self.assertRedirects(response, reverse("lms:quiz_take", args=[attempt.pk]))


class PlagiarismTests(LmsBase):
    def test_similar_submissions_are_flagged(self):
        assignment = Assignment.objects.create(
            site=self.site, title="Essay", due_at=timezone.now() + timedelta(days=1),
            created_by=self.lecturer)
        other_student = StudentFactory(
            user=UserFactory(role="student", username="student2"),
            programme=self.programme)
        EnrollmentFactory(student=other_student, course=self.course, term=self.term)
        text = "The quick brown fox jumps over the lazy dog. " * 5
        Submission.objects.create(
            assignment=assignment, student=self.student, text=text)
        Submission.objects.create(
            assignment=assignment, student=other_student, text=text)
        reports = check_assignment_plagiarism(assignment)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].similarity, Decimal("100.00"))


class GradebookTests(LmsBase):
    def test_weighted_overall_uses_only_categories_with_items(self):
        categories = {c.name: c for c in ensure_default_categories(self.site)}
        assignment_item = GradeItem.objects.create(
            site=self.site, category=categories["Assignments"],
            title="Assignment", max_score=Decimal("100"))
        quiz_item = GradeItem.objects.create(
            site=self.site, category=categories["Quizzes"],
            title="Quiz", max_score=Decimal("100"))
        GradeEntry.objects.create(item=assignment_item, student=self.student,
                                  score=Decimal("80"))
        GradeEntry.objects.create(item=quiz_item, student=self.student,
                                  score=Decimal("60"))
        data = gradebook_data(self.site, [self.student])
        self.assertEqual(data["rows"][0]["overall"], Decimal("71.43"))


class DiscussionAndLiveTests(LmsBase):
    def test_student_can_start_a_discussion_and_reply(self):
        forum = Forum.objects.create(site=self.site, title="Week 1 questions")
        self.login_student()
        response = self.client.post(reverse("lms:thread_create", args=[forum.pk]), {
            "title": "Help with HTML", "body": "Which tag is best?"})
        thread = forum.threads.get()
        self.assertRedirects(response, reverse("lms:thread_detail", args=[thread.pk]))
        response = self.client.post(reverse("lms:post_create", args=[thread.pk]), {
            "body": "I found the answer."})
        self.assertRedirects(response, reverse("lms:thread_detail", args=[thread.pk]))
        self.assertEqual(thread.posts.count(), 1)

    def test_the_join_link_opens_fifteen_minutes_before_the_class(self):
        session = LiveSession.objects.create(
            site=self.site, title="Live lesson",
            join_url="https://example.com/class",
            starts_at=timezone.now() + timedelta(hours=1),
            ends_at=timezone.now() + timedelta(hours=2),
            created_by=self.lecturer)
        self.login_student()
        response = self.client.get(reverse("lms:live_join", args=[session.pk]))
        self.assertRedirects(response, reverse("lms:course_site", args=[self.site.pk]))
        session.starts_at = timezone.now() + timedelta(minutes=5)
        session.ends_at = timezone.now() + timedelta(minutes=65)
        session.save()
        response = self.client.get(reverse("lms:live_join", args=[session.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://example.com/class")
