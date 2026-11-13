import random
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from academics.models import Enrollment, Student
from core.access import module_required, user_has_code

from .forms import (
    AssignmentForm,
    ChoiceForm,
    ContentItemForm,
    CourseSiteForm,
    ForumForm,
    GradeCategoryForm,
    GradeEntryForm,
    GradeItemForm,
    GradeSubmissionForm,
    LiveSessionForm,
    MatchingPairForm,
    PostForm,
    QuestionForm,
    QuizForm,
    SiteSectionForm,
    SubmissionForm,
    ThreadForm,
)
from .models import (
    Assignment,
    Choice,
    CourseSite,
    Forum,
    ForumPost,
    ForumThread,
    GradeEntry,
    GradeItem,
    LiveSession,
    Question,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    Submission,
)
from .services import (
    check_assignment_plagiarism,
    ensure_default_categories,
    grade_attempt_answers,
    grade_submission,
    gradebook_data,
    regrade_attempt,
    submissions_for_grading,
)


def _student_for(user):
    return Student.objects.filter(user=user).first()


def _can_manage(user, site):
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.role == "admin":
        return True
    if user_has_code(user, "lms.manage"):
        return True
    return site.created_by_id == user.pk or site.course.lecturer_id == user.pk


def _can_grade(user, site):
    return _can_manage(user, site) or user_has_code(user, "lms.grade")


def _is_enrolled(user, site):
    student = _student_for(user)
    if student is None:
        return False
    return Enrollment.objects.filter(
        student=student, course=site.course).exclude(
        status=Enrollment.Status.DROPPED).exists()


def _can_view(user, site):
    return _can_grade(user, site) or _is_enrolled(user, site)


def _site_or_404(request, pk, manage=False):
    site = get_object_or_404(
        CourseSite.objects.select_related("course", "course__programme"), pk=pk)
    if manage:
        if not _can_manage(request.user, site):
            raise PermissionDenied("Only the course team can change this site.")
    elif not _can_view(request.user, site):
        raise PermissionDenied("You are not enrolled on this course.")
    if not site.is_published and not _can_manage(request.user, site):
        raise Http404("This course site is not published yet.")
    return site


def _students_for_site(site):
    return Student.objects.filter(
        enrollments__course=site.course,
    ).exclude(enrollments__status=Enrollment.Status.DROPPED).distinct().select_related(
        "user", "programme")


def _sections_for(site, manage):
    sections = list(site.sections.prefetch_related("items"))
    if not manage:
        sections = [section for section in sections if section.is_visible]
    for section in sections:
        items = list(section.items.all())
        if not manage:
            items = [item for item in items if item.is_visible]
        section.visible_items = items
    return sections


def _form_page(request, form, heading, cancel_url, **url_kwargs):
    return render(request, "lms/form.html", {
        "form": form, "heading": heading,
        "cancel_url": reverse(cancel_url, kwargs=url_kwargs)})


@login_required
@module_required("lms")
def course_list(request):
    user = request.user
    student = _student_for(user)
    if user.is_superuser or user.role == "admin" or user_has_code(user, "lms.manage"):
        sites = CourseSite.objects.select_related("course", "course__programme")
    elif student is not None:
        sites = CourseSite.objects.filter(
            course__enrollments__student=student,
            is_published=True).distinct().select_related("course", "course__programme")
    else:
        sites = CourseSite.objects.filter(
            Q(course__lecturer=user) | Q(created_by=user)
        ).distinct().select_related("course", "course__programme")
    return render(request, "lms/course_list.html", {"sites": sites})


@login_required
@module_required("lms")
def course_site(request, pk):
    site = _site_or_404(request, pk)
    manage = _can_manage(request.user, site)
    student = _student_for(request.user)
    assignments = site.assignments.select_related("grade_item")
    quizzes = site.quizzes.select_related("grade_item")
    if not manage:
        assignments = assignments.filter(is_published=True)
        quizzes = quizzes.filter(is_published=True)
    context = {
        "site": site,
        "manage": manage,
        "sections": _sections_for(site, manage),
        "assignments": assignments,
        "quizzes": quizzes,
        "forums": site.forums.filter(is_published=True) if not manage else site.forums.all(),
        "live_sessions": (site.live_sessions.filter(is_published=True)
                          if not manage else site.live_sessions.all()),
        "section_form": SiteSectionForm(),
        "content_form": ContentItemForm(site=site),
        "gradebook": (gradebook_data(site, [student]) if student is not None
                      else None),
    }
    if manage:
        context["students_count"] = _students_for_site(site).count()
    return render(request, "lms/course_site.html", context)


@login_required
@module_required("lms")
def site_create(request):
    if not (request.user.is_superuser or request.user.role in ("admin", "lecturer", "hod", "dean")
            or user_has_code(request.user, "lms.manage")):
        raise PermissionDenied("Only course staff can create a course site.")
    form = CourseSiteForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        site = form.save(commit=False)
        site.created_by = request.user
        site.save()
        ensure_default_categories(site)
        messages.success(request, "Course site created.")
        return redirect("lms:course_site", pk=site.pk)
    return _form_page(request, form, "Create a course site", "lms:course_list")


@login_required
@module_required("lms")
def site_edit(request, pk):
    site = _site_or_404(request, pk, manage=True)
    form = CourseSiteForm(request.user, request.POST or None, instance=site)
    if request.method == "POST" and form.is_valid():
        form.save()
        ensure_default_categories(site)
        messages.success(request, "Course site saved.")
        return redirect("lms:course_site", pk=site.pk)
    return _form_page(request, form, f"Edit {site.display_title}",
                      "lms:course_site", pk=site.pk)


@login_required
@module_required("lms")
def section_create(request, pk):
    site = _site_or_404(request, pk, manage=True)
    form = SiteSectionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        section = form.save(commit=False)
        section.site = site
        section.save()
        messages.success(request, "Section added.")
        return redirect("lms:course_site", pk=site.pk)
    return _form_page(request, form, "Add a section", "lms:course_site", pk=site.pk)


@login_required
@module_required("lms")
def content_create(request, pk):
    site = _site_or_404(request, pk, manage=True)
    form = ContentItemForm(site, request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.created_by = request.user
        item.save()
        messages.success(request, "Content added.")
        return redirect("lms:course_site", pk=site.pk)
    return _form_page(request, form, "Add course content", "lms:course_site", pk=site.pk)


@login_required
@module_required("lms")
def assignment_create(request, pk):
    site = _site_or_404(request, pk, manage=True)
    form = AssignmentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        assignment = form.save(commit=False)
        assignment.site = site
        assignment.created_by = request.user
        assignment.save()
        messages.success(request, "Assignment created.")
        return redirect("lms:assignment_detail", pk=assignment.pk)
    return _form_page(request, form, "Create an assignment", "lms:course_site",
                      pk=site.pk)


@login_required
@module_required("lms")
def assignment_detail(request, pk):
    assignment = get_object_or_404(
        Assignment.objects.select_related("site__course"), pk=pk)
    site = _site_or_404(request, assignment.site_id)
    can_grade = _can_grade(request.user, site)
    student = _student_for(request.user)
    submission = None
    if student is not None:
        submission = assignment.submissions.filter(student=student).first()
    submissions = (submissions_for_grading(assignment) if can_grade else [])
    rubric = getattr(assignment, "rubric", None)
    can_submit = bool(
        student is not None and assignment.is_published
        and assignment.accepts_submission()
        and (submission is None or assignment.allow_resubmission))
    return render(request, "lms/assignment_detail.html", {
        "site": site,
        "assignment": assignment,
        "can_grade": can_grade,
        "submission": submission,
        "submissions": submissions,
        "rubric": rubric,
        "can_submit": can_submit,
        "submission_form": SubmissionForm(instance=submission),
    })


@login_required
@module_required("lms")
def assignment_submit(request, pk):
    assignment = get_object_or_404(
        Assignment.objects.select_related("site__course"), pk=pk)
    _site_or_404(request, assignment.site_id)
    student = _student_for(request.user)
    if student is None:
        raise PermissionDenied("Only students can submit work.")
    if request.method != "POST":
        return redirect("lms:assignment_detail", pk=assignment.pk)
    if not assignment.accepts_submission():
        messages.error(request, "This assignment is closed to submissions.")
        return redirect("lms:assignment_detail", pk=assignment.pk)
    submission = assignment.submissions.filter(student=student).first()
    if submission is not None and not assignment.allow_resubmission:
        messages.error(request, "This assignment does not allow resubmission.")
        return redirect("lms:assignment_detail", pk=assignment.pk)
    form = SubmissionForm(request.POST, request.FILES, instance=submission)
    if form.is_valid():
        row = form.save(commit=False)
        row.assignment = assignment
        row.student = student
        row.submitted_at = timezone.now()
        row.attempt_count = (submission.attempt_count + 1) if submission else 1
        row.save()
        messages.success(request, "Work submitted.")
    else:
        messages.error(request, " ".join(
            " ".join(values) for values in form.errors.values()))
    return redirect("lms:assignment_detail", pk=assignment.pk)


@login_required
@module_required("lms")
def assignment_grade(request, pk, submission_pk):
    submission = get_object_or_404(
        Submission.objects.select_related("assignment__site", "student__user"),
        pk=submission_pk, assignment_id=pk)
    site = _site_or_404(request, submission.assignment.site_id)
    if not _can_grade(request.user, site):
        raise PermissionDenied("Only the course team can grade this work.")
    rubric = getattr(submission.assignment, "rubric", None)
    if request.method == "POST":
        form = GradeSubmissionForm(request.POST)
        if form.is_valid():
            criteria = {}
            if rubric is not None:
                for criterion in rubric.criteria.all():
                    raw = request.POST.get(f"criterion_{criterion.pk}", "").strip()
                    if not raw:
                        continue
                    try:
                        criteria[str(criterion.pk)] = Decimal(raw)
                    except InvalidOperation:
                        messages.error(request, "Rubric scores must be numbers.")
                        return redirect("lms:assignment_grade", pk=pk,
                                        submission_pk=submission.pk)
            grade_submission(
                submission, form.cleaned_data.get("mark"),
                form.cleaned_data.get("feedback", ""), request.user,
                criteria=criteria or None)
            messages.success(request, "Submission graded.")
            return redirect("lms:assignment_detail", pk=pk)
    else:
        form = GradeSubmissionForm(initial={
            "mark": submission.mark, "feedback": submission.feedback})
    scores = {score.criterion_id: score for score in
              submission.criterion_scores.all()} if rubric else {}
    criteria = list(rubric.criteria.all()) if rubric else []
    for criterion in criteria:
        criterion.saved_score = scores.get(criterion.pk)
    return render(request, "lms/assignment_grade.html", {
        "site": site,
        "assignment": submission.assignment,
        "submission": submission,
        "form": form,
        "rubric": rubric,
        "criteria": criteria,
    })


@login_required
@module_required("lms")
def plagiarism_check(request, pk):
    assignment = get_object_or_404(
        Assignment.objects.select_related("site__course"), pk=pk)
    site = _site_or_404(request, assignment.site_id)
    if not _can_grade(request.user, site):
        raise PermissionDenied("Only the course team can run a similarity check.")
    if request.method == "POST":
        reports = check_assignment_plagiarism(assignment)
        messages.success(
            request, f"Similarity check finished with {len(reports)} flagged pair(s).")
    return redirect("lms:plagiarism_report", pk=assignment.pk)


@login_required
@module_required("lms")
def plagiarism_report(request, pk):
    assignment = get_object_or_404(
        Assignment.objects.select_related("site__course"), pk=pk)
    site = _site_or_404(request, assignment.site_id)
    if not _can_grade(request.user, site):
        raise PermissionDenied("Only the course team can see similarity reports.")
    reports = assignment.plagiarism_reports.select_related(
        "submission_a__student__user", "submission_b__student__user")
    return render(request, "lms/plagiarism_report.html", {
        "site": site, "assignment": assignment, "reports": reports})


@login_required
@module_required("lms")
def quiz_create(request, pk):
    site = _site_or_404(request, pk, manage=True)
    form = QuizForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        quiz = form.save(commit=False)
        quiz.site = site
        quiz.created_by = request.user
        quiz.save()
        messages.success(request, "Quiz created. Add the questions next.")
        return redirect("lms:quiz_detail", pk=quiz.pk)
    return _form_page(request, form, "Create a quiz", "lms:course_site", pk=site.pk)


@login_required
@module_required("lms")
def quiz_detail(request, pk):
    quiz = get_object_or_404(
        Quiz.objects.select_related("site__course").prefetch_related("questions"),
        pk=pk)
    site = _site_or_404(request, quiz.site_id)
    can_grade = _can_grade(request.user, site)
    student = _student_for(request.user)
    attempts = (quiz.attempts.filter(student=student) if student is not None
                else quiz.attempts.none())
    if can_grade:
        attempts = quiz.attempts.select_related("student__user")
    return render(request, "lms/quiz_detail.html", {
        "site": site, "quiz": quiz, "can_grade": can_grade,
        "attempts": attempts,
        "attempts_left": quiz.attempts_left(student) if student is not None else 0,
        "is_open": quiz.is_open(),
        "question_form": QuestionForm(),
    })


@login_required
@module_required("lms")
def quiz_start(request, pk):
    quiz = get_object_or_404(Quiz.objects.select_related("site__course"), pk=pk)
    _site_or_404(request, quiz.site_id)
    student = _student_for(request.user)
    if student is None:
        raise PermissionDenied("Only students can take a quiz.")
    if request.method != "POST":
        return redirect("lms:quiz_detail", pk=quiz.pk)
    if not quiz.is_open():
        messages.error(request, "This quiz is not open.")
        return redirect("lms:quiz_detail", pk=quiz.pk)
    if quiz.attempts_left(student) <= 0:
        messages.error(request, "You have used all your attempts.")
        return redirect("lms:quiz_detail", pk=quiz.pk)
    questions = list(quiz.questions.order_by("order", "pk"))
    if quiz.randomize_questions:
        random.shuffle(questions)
    choice_order = {}
    for question in questions:
        choices = list(question.choices.order_by("order", "pk"))
        if quiz.randomize_choices:
            random.shuffle(choices)
        choice_order[str(question.pk)] = [choice.pk for choice in choices]
    attempt = QuizAttempt.objects.create(
        institution=quiz.institution, quiz=quiz, student=student,
        question_order=[question.pk for question in questions],
        choice_order=choice_order)
    return redirect("lms:quiz_take", pk=attempt.pk)


@login_required
@module_required("lms")
def quiz_take(request, pk):
    attempt = get_object_or_404(
        QuizAttempt.objects.select_related("quiz__site__course", "student__user"),
        pk=pk)
    site = _site_or_404(request, attempt.quiz.site_id)
    student = _student_for(request.user)
    if not _can_grade(request.user, site) and (
            student is None or attempt.student_id != student.pk):
        raise PermissionDenied("That attempt belongs to another student.")
    if attempt.status != QuizAttempt.Status.IN_PROGRESS:
        return redirect("lms:quiz_attempt", pk=attempt.pk)
    if attempt.is_expired and request.method == "POST":
        grade_attempt_answers(attempt)
        messages.error(request, "Time is up. Your answers were submitted.")
        return redirect("lms:quiz_results", pk=attempt.quiz_id)
    questions = attempt.ordered_questions
    choice_order = attempt.choice_order or {}
    for question in questions:
        choices = list(question.choices.all())
        order = choice_order.get(str(question.pk))
        if order:
            by_id = {choice.pk: choice for choice in choices}
            choices = [by_id[pk] for pk in order if pk in by_id]
        question.take_choices = choices
        question.take_right_options = sorted(
            {pair.right_text for pair in question.matching_pairs.all()})
    if request.method == "POST":
        for question in questions:
            answer, _created = QuizAnswer.objects.get_or_create(
                institution=attempt.institution, attempt=attempt,
                question=question)
            if question.kind in (Question.Kind.MULTIPLE_CHOICE,
                                 Question.Kind.TRUE_FALSE):
                raw = request.POST.get(f"q_{question.pk}", "").strip()
                answer.selected_choice_id = int(raw) if raw.isdigit() else None
                answer.text_answer = ""
                answer.matching_answer = {}
            elif question.kind == Question.Kind.SHORT_ANSWER:
                answer.text_answer = request.POST.get(
                    f"q_{question.pk}", "").strip()
                answer.selected_choice_id = None
                answer.matching_answer = {}
            else:
                mapping = {}
                for pair in question.matching_pairs.all():
                    value = request.POST.get(
                        f"match_{question.pk}_{pair.pk}", "").strip()
                    if value:
                        mapping[str(pair.pk)] = value
                answer.matching_answer = mapping
                answer.selected_choice_id = None
                answer.text_answer = ""
            answer.save()
        grade_attempt_answers(attempt)
        messages.success(request, "Quiz submitted.")
        return redirect("lms:quiz_results", pk=attempt.quiz_id)
    return render(request, "lms/quiz_take.html", {
        "attempt": attempt, "quiz": attempt.quiz, "site": site,
        "questions": questions, "remaining": attempt.remaining_seconds,
        "expired": attempt.is_expired})


@login_required
@module_required("lms")
def quiz_results(request, pk):
    quiz = get_object_or_404(Quiz.objects.select_related("site__course"), pk=pk)
    site = _site_or_404(request, quiz.site_id)
    student = _student_for(request.user)
    if _can_grade(request.user, site):
        attempts = quiz.attempts.select_related("student__user")
    elif student is not None:
        attempts = quiz.attempts.filter(student=student)
    else:
        raise PermissionDenied("You are not enrolled on this course.")
    return render(request, "lms/quiz_results.html", {
        "site": site, "quiz": quiz, "attempts": attempts})


@login_required
@module_required("lms")
def quiz_attempt(request, pk):
    attempt = get_object_or_404(
        QuizAttempt.objects.select_related("quiz__site__course", "student__user"),
        pk=pk)
    site = _site_or_404(request, attempt.quiz.site_id)
    student = _student_for(request.user)
    if not _can_grade(request.user, site) and (
            student is None or attempt.student_id != student.pk):
        raise PermissionDenied("That attempt belongs to another student.")
    answers = attempt.answers.select_related(
        "question", "selected_choice").prefetch_related(
        "question__matching_pairs")
    return render(request, "lms/quiz_attempt.html", {
        "site": site, "attempt": attempt, "quiz": attempt.quiz,
        "answers": answers, "can_grade": _can_grade(request.user, site)})


@login_required
@module_required("lms")
def quiz_grade(request, pk):
    attempt = get_object_or_404(
        QuizAttempt.objects.select_related("quiz__site__course", "student__user"),
        pk=pk)
    site = _site_or_404(request, attempt.quiz.site_id)
    if not _can_grade(request.user, site):
        raise PermissionDenied("Only the course team can mark this attempt.")
    if request.method == "POST":
        for answer in attempt.answers.select_related("question"):
            key = f"points_{answer.pk}"
            if key not in request.POST:
                continue
            raw = request.POST.get(key, "").strip()
            try:
                points = Decimal(raw) if raw else Decimal("0")
            except InvalidOperation:
                messages.error(request, "Marks must be numbers.")
                return redirect("lms:quiz_grade", pk=attempt.pk)
            if points < 0:
                points = Decimal("0")
            if points > answer.question.points:
                points = answer.question.points
            answer.awarded_points = points
            answer.is_correct = points == answer.question.points
            answer.needs_manual = False
            answer.feedback = request.POST.get(f"feedback_{answer.pk}", "").strip()
            answer.save()
        regrade_attempt(attempt)
        messages.success(request, "Attempt marked.")
        return redirect("lms:quiz_attempt", pk=attempt.pk)
    answers = attempt.answers.select_related("question")
    return render(request, "lms/quiz_grade.html", {
        "site": site, "attempt": attempt, "quiz": attempt.quiz,
        "answers": answers})


@login_required
@module_required("lms")
def quiz_analytics(request, pk):
    quiz = get_object_or_404(Quiz.objects.select_related("site__course"), pk=pk)
    site = _site_or_404(request, quiz.site_id)
    if not _can_grade(request.user, site):
        raise PermissionDenied("Only the course team can see quiz analytics.")
    answers = QuizAnswer.objects.filter(question__quiz=quiz)
    rows = []
    for question in quiz.questions.order_by("order", "pk"):
        question_answers = answers.filter(question=question)
        total = question_answers.count()
        correct = question_answers.filter(is_correct=True).count()
        manual = question_answers.filter(needs_manual=True).count()
        rows.append({
            "question": question, "total": total, "correct": correct,
            "manual": manual,
            "average": (question_answers.aggregate(t=Sum("awarded_points"))["t"]
                        / total if total else None),
        })
    return render(request, "lms/quiz_analytics.html", {
        "site": site, "quiz": quiz, "rows": rows})


@login_required
@module_required("lms")
def question_create(request, pk):
    quiz = get_object_or_404(Quiz.objects.select_related("site__course"), pk=pk)
    _site_or_404(request, quiz.site_id, manage=True)
    form = QuestionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        question = form.save(commit=False)
        question.quiz = quiz
        question.save()
        if question.kind == Question.Kind.TRUE_FALSE and not question.choices.exists():
            Choice.objects.create(institution=question.institution,
                                  question=question, text="True",
                                  is_correct=True, order=1)
            Choice.objects.create(institution=question.institution,
                                  question=question, text="False", order=2)
        messages.success(request, "Question added.")
        return redirect("lms:question_edit", pk=question.pk)
    return _form_page(request, form, f"Add a question to {quiz.title}",
                      "lms:quiz_detail", pk=quiz.pk)


@login_required
@module_required("lms")
def question_edit(request, pk):
    question = get_object_or_404(
        Question.objects.select_related("quiz__site__course"), pk=pk)
    site = _site_or_404(request, question.quiz.site_id, manage=True)
    form = QuestionForm(request.POST or None, instance=question)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Question saved.")
        return redirect("lms:question_edit", pk=question.pk)
    return render(request, "lms/question_edit.html", {
        "site": site, "quiz": question.quiz, "question": question,
        "form": form, "choice_form": ChoiceForm(),
        "pair_form": MatchingPairForm()})


@login_required
@module_required("lms")
def choice_create(request, pk):
    question = get_object_or_404(
        Question.objects.select_related("quiz__site__course"), pk=pk)
    _site_or_404(request, question.quiz.site_id, manage=True)
    form = ChoiceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        choice = form.save(commit=False)
        choice.question = question
        choice.save()
        messages.success(request, "Choice added.")
    return redirect("lms:question_edit", pk=question.pk)


@login_required
@module_required("lms")
def pair_create(request, pk):
    question = get_object_or_404(
        Question.objects.select_related("quiz__site__course"), pk=pk)
    _site_or_404(request, question.quiz.site_id, manage=True)
    form = MatchingPairForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        pair = form.save(commit=False)
        pair.question = question
        pair.save()
        messages.success(request, "Matching pair added.")
    return redirect("lms:question_edit", pk=question.pk)


@login_required
@module_required("lms")
def forum_create(request, pk):
    site = _site_or_404(request, pk, manage=True)
    form = ForumForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        forum = form.save(commit=False)
        forum.site = site
        forum.created_by = request.user
        forum.save()
        messages.success(request, "Forum created.")
        return redirect("lms:forum_detail", pk=forum.pk)
    return _form_page(request, form, "Create a discussion forum",
                      "lms:course_site", pk=site.pk)


@login_required
@module_required("lms")
def forum_detail(request, pk):
    forum = get_object_or_404(
        Forum.objects.select_related("site__course"), pk=pk)
    site = _site_or_404(request, forum.site_id)
    manage = _can_grade(request.user, site)
    threads = forum.threads.select_related("author")
    if not manage:
        threads = threads.filter(is_approved=True)
    return render(request, "lms/forum_detail.html", {
        "site": site, "forum": forum, "threads": threads, "manage": manage,
        "thread_form": ThreadForm()})


@login_required
@module_required("lms")
def thread_create(request, pk):
    forum = get_object_or_404(
        Forum.objects.select_related("site__course"), pk=pk)
    _site_or_404(request, forum.site_id)
    if request.method == "POST":
        form = ThreadForm(request.POST)
        if form.is_valid():
            thread = form.save(commit=False)
            thread.forum = forum
            thread.author = request.user
            thread.save()
            messages.success(request, "Discussion started.")
            return redirect("lms:thread_detail", pk=thread.pk)
        messages.error(request, " ".join(
            " ".join(values) for values in form.errors.values()))
    return redirect("lms:forum_detail", pk=forum.pk)


@login_required
@module_required("lms")
def thread_detail(request, pk):
    thread = get_object_or_404(
        ForumThread.objects.select_related("forum__site__course", "author"), pk=pk)
    site = _site_or_404(request, thread.forum.site_id)
    manage = _can_grade(request.user, site)
    if not thread.is_approved and not manage:
        raise Http404("This discussion is not visible.")
    posts = thread.posts.select_related("author")
    if not manage:
        posts = posts.filter(is_approved=True)
    return render(request, "lms/thread_detail.html", {
        "site": site, "forum": thread.forum, "thread": thread,
        "posts": posts, "manage": manage, "post_form": PostForm()})


@login_required
@module_required("lms")
def post_create(request, pk):
    thread = get_object_or_404(
        ForumThread.objects.select_related("forum__site__course"), pk=pk)
    site = _site_or_404(request, thread.forum.site_id)
    if thread.is_locked and not _can_grade(request.user, site):
        messages.error(request, "This discussion is locked.")
        return redirect("lms:thread_detail", pk=thread.pk)
    if request.method == "POST":
        form = PostForm(request.POST)
        if form.is_valid():
            post = form.save(commit=False)
            post.thread = thread
            post.author = request.user
            post.save()
            messages.success(request, "Reply posted.")
        else:
            messages.error(request, "Write a reply first.")
    return redirect("lms:thread_detail", pk=thread.pk)


@login_required
@module_required("lms")
def thread_moderate(request, pk):
    thread = get_object_or_404(
        ForumThread.objects.select_related("forum__site__course"), pk=pk)
    site = _site_or_404(request, thread.forum.site_id)
    if not _can_grade(request.user, site):
        raise PermissionDenied("Only the course team can moderate discussions.")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "pin":
            thread.is_pinned = not thread.is_pinned
        elif action == "lock":
            thread.is_locked = not thread.is_locked
        elif action == "approve":
            thread.is_approved = not thread.is_approved
        thread.save()
        messages.success(request, "Discussion updated.")
    return redirect("lms:thread_detail", pk=thread.pk)


@login_required
@module_required("lms")
def post_moderate(request, pk):
    post = get_object_or_404(
        ForumPost.objects.select_related("thread__forum__site__course"), pk=pk)
    site = _site_or_404(request, post.thread.forum.site_id)
    if not _can_grade(request.user, site):
        raise PermissionDenied("Only the course team can moderate discussions.")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "approve":
            post.is_approved = not post.is_approved
        elif action == "flag":
            post.is_flagged = not post.is_flagged
        post.save()
        messages.success(request, "Post updated.")
    return redirect("lms:thread_detail", pk=post.thread_id)


@login_required
@module_required("lms")
def live_create(request, pk):
    site = _site_or_404(request, pk, manage=True)
    form = LiveSessionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        session = form.save(commit=False)
        session.site = site
        session.created_by = request.user
        session.save()
        messages.success(request, "Live class scheduled.")
        return redirect("lms:course_site", pk=site.pk)
    return _form_page(request, form, "Schedule a live class",
                      "lms:course_site", pk=site.pk)


@login_required
@module_required("lms")
def live_join(request, pk):
    session = get_object_or_404(
        LiveSession.objects.select_related("site__course"), pk=pk)
    site = _site_or_404(request, session.site_id)
    if not session.is_published and not _can_manage(request.user, site):
        raise Http404("That live class is not published.")
    if not session.can_join():
        messages.error(request, "The join link opens 15 minutes before the class.")
        return redirect("lms:course_site", pk=site.pk)
    return redirect(session.join_url)


@login_required
@module_required("lms")
def gradebook(request, pk):
    site = _site_or_404(request, pk)
    manage = _can_grade(request.user, site)
    student = _student_for(request.user)
    if manage:
        students = _students_for_site(site)
    elif student is not None:
        students = [student]
    else:
        raise PermissionDenied("You are not enrolled on this course.")
    data = gradebook_data(site, list(students), published_only=not manage)
    return render(request, "lms/gradebook.html", {
        "site": site, "manage": manage, "data": data,
        "entry_form": GradeEntryForm()})


@login_required
@module_required("lms")
def gradebook_setup(request, pk):
    site = _site_or_404(request, pk, manage=True)
    ensure_default_categories(site)
    return render(request, "lms/gradebook_setup.html", {
        "site": site,
        "categories": site.grade_categories.order_by("order", "pk"),
        "items": site.grade_items.select_related("category").order_by(
            "category__order", "order", "pk"),
        "category_form": GradeCategoryForm(),
        "item_form": GradeItemForm(site=site),
    })


@login_required
@module_required("lms")
def grade_category_create(request, pk):
    site = _site_or_404(request, pk, manage=True)
    form = GradeCategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        category = form.save(commit=False)
        category.site = site
        category.save()
        messages.success(request, "Category added.")
    else:
        messages.error(request, "Check the category details.")
    return redirect("lms:gradebook_setup", pk=site.pk)


@login_required
@module_required("lms")
def grade_item_create(request, pk):
    site = _site_or_404(request, pk, manage=True)
    form = GradeItemForm(site, request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.site = site
        item.save()
        messages.success(request, "Grade item added.")
    else:
        messages.error(request, "Check the grade item details.")
    return redirect("lms:gradebook_setup", pk=site.pk)


@login_required
@module_required("lms")
def grade_entry(request, pk):
    site = _site_or_404(request, pk, manage=True)
    if request.method == "POST":
        item = get_object_or_404(GradeItem, pk=request.POST.get("item"), site=site)
        student = get_object_or_404(
            Student, pk=request.POST.get("student"),
            enrollments__course=site.course)
        form = GradeEntryForm(request.POST)
        if form.is_valid():
            score = form.cleaned_data.get("score")
            if score is not None and (score < 0 or score > item.max_score):
                messages.error(request, "The score is outside this item's range.")
            else:
                GradeEntry.objects.update_or_create(
                    institution=site.institution, item=item, student=student,
                    defaults={"score": score,
                              "feedback": form.cleaned_data.get("feedback", ""),
                              "graded_at": timezone.now(),
                              "graded_by": request.user})
                messages.success(request, "Grade saved.")
        else:
            messages.error(request, "Check the score.")
    return redirect("lms:gradebook", pk=site.pk)
