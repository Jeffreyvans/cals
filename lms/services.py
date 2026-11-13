import re
from decimal import Decimal
from difflib import SequenceMatcher

from django.db.models import Sum
from django.utils import timezone

from .models import (
    CriterionScore,
    GradeCategory,
    GradeEntry,
    GradeItem,
    PlagiarismReport,
    Question,
    QuizAttempt,
    Submission,
)

DEFAULT_CATEGORIES = (("Assignments", Decimal("40")),
                      ("Quizzes", Decimal("30")),
                      ("Coursework", Decimal("30")))


def ensure_default_categories(site):
    if site.grade_categories.exists():
        return list(site.grade_categories.order_by("order", "pk"))
    return [
        GradeCategory.objects.create(
            institution=site.institution, site=site, name=name,
            weight=weight, order=index)
        for index, (name, weight) in enumerate(DEFAULT_CATEGORIES)
    ]


def _category_for(site, name, default_weight):
    category = site.grade_categories.filter(name=name).first()
    if category is not None:
        return category
    weight = default_weight if not site.grade_categories.exists() else Decimal("0")
    return GradeCategory.objects.create(
        institution=site.institution, site=site, name=name, weight=weight)


def ensure_grade_item(assignment):
    category = _category_for(assignment.site, "Assignments", Decimal("40"))
    item, created = GradeItem.objects.get_or_create(
        site=assignment.site, assignment=assignment,
        defaults={"category": category, "title": assignment.title,
                  "max_score": assignment.max_score,
                  "due_date": assignment.due_at.date()})
    if not created and (item.title != assignment.title
                        or item.max_score != assignment.max_score
                        or item.due_date != assignment.due_at.date()):
        item.title = assignment.title
        item.max_score = assignment.max_score
        item.due_date = assignment.due_at.date()
        item.save(update_fields=["title", "max_score", "due_date"])
    return item


def ensure_quiz_grade_item(quiz):
    category = _category_for(quiz.site, "Quizzes", Decimal("30"))
    item, created = GradeItem.objects.get_or_create(
        site=quiz.site, quiz=quiz,
        defaults={"category": category, "title": quiz.title,
                  "max_score": quiz.total_points or Decimal("100")})
    if not created:
        total = quiz.total_points or Decimal("100")
        if item.title != quiz.title or item.max_score != total:
            item.title = quiz.title
            item.max_score = total
            item.save(update_fields=["title", "max_score"])
    return item


def _write_grade(student, item, score, feedback="", user=None):
    entry, _created = GradeEntry.objects.update_or_create(
        item=item, student=student,
        defaults={"score": score, "feedback": feedback,
                  "graded_at": timezone.now(), "graded_by": user})
    return entry


def sync_assignment_grade(submission):
    if submission.mark is None:
        return None
    item = ensure_grade_item(submission.assignment)
    return _write_grade(submission.student, item, submission.mark,
                        submission.feedback, submission.graded_by)


def sync_best_quiz_grade(quiz, student):
    attempts = quiz.attempts.filter(
        student=student, status=QuizAttempt.Status.GRADED, score__isnull=False)
    best = attempts.order_by("-score", "submitted_at").first()
    if best is None:
        return None
    item = ensure_quiz_grade_item(quiz)
    return _write_grade(student, item, best.score)


def grade_submission(submission, mark, feedback="", user=None, criteria=None):
    if criteria:
        total = sum((Decimal(value) for value in criteria.values()), Decimal("0"))
        mark = total
    if mark is not None:
        mark = submission.assignment.apply_late_penalty(mark)
        if mark > submission.assignment.max_score:
            mark = submission.assignment.max_score
        if mark < 0:
            mark = Decimal("0")
    submission.mark = mark
    submission.feedback = feedback
    submission.graded_at = timezone.now() if mark is not None else None
    submission.graded_by = user
    submission.save()
    if criteria:
        rubric = getattr(submission.assignment, "rubric", None)
        if rubric is not None:
            by_id = {criterion.pk: criterion for criterion in rubric.criteria.all()}
            for criterion_id, points in criteria.items():
                criterion = by_id.get(int(criterion_id))
                if criterion is None:
                    continue
                CriterionScore.objects.update_or_create(
                    institution=submission.institution,
                    submission=submission, criterion=criterion,
                    defaults={"points": Decimal(points)})
    return submission


def _answer_is_correct(answer):
    question = answer.question
    if question.kind in (Question.Kind.MULTIPLE_CHOICE, Question.Kind.TRUE_FALSE):
        return bool(answer.selected_choice and answer.selected_choice.is_correct)
    if question.kind == Question.Kind.SHORT_ANSWER:
        accepted = question.accepted_answer_list
        return bool(accepted and answer.text_answer.strip().lower() in accepted)
    if question.kind == Question.Kind.MATCHING:
        pairs = list(question.matching_pairs.all())
        if not pairs:
            return False
        correct = 0
        for pair in pairs:
            if answer.matching_answer.get(str(pair.pk)) == pair.right_text:
                correct += 1
        return correct == len(pairs)
    return False


def grade_attempt_answers(attempt):
    total = Decimal("0")
    needs_manual = False
    for answer in attempt.answers.select_related("question", "selected_choice"):
        question = answer.question
        if question.kind == Question.Kind.SHORT_ANSWER:
            accepted = question.accepted_answer_list
            if not answer.text_answer.strip():
                answer.awarded_points = Decimal("0")
                answer.is_correct = False
                answer.needs_manual = False
            elif accepted:
                answer.is_correct = _answer_is_correct(answer)
                answer.awarded_points = question.points if answer.is_correct else Decimal("0")
                answer.needs_manual = False
            else:
                answer.is_correct = False
                answer.awarded_points = Decimal("0")
                answer.needs_manual = True
        elif question.kind == Question.Kind.MATCHING:
            pairs = list(question.matching_pairs.all())
            correct = sum(
                1 for pair in pairs
                if answer.matching_answer.get(str(pair.pk)) == pair.right_text)
            answer.is_correct = bool(pairs and correct == len(pairs))
            if pairs:
                answer.awarded_points = (
                    question.points * Decimal(correct) / Decimal(len(pairs))
                ).quantize(Decimal("0.01"))
            else:
                answer.awarded_points = Decimal("0")
            answer.needs_manual = False
        else:
            answer.is_correct = _answer_is_correct(answer)
            answer.awarded_points = question.points if answer.is_correct else Decimal("0")
            answer.needs_manual = False
        answer.save()
        total += answer.awarded_points
        needs_manual = needs_manual or answer.needs_manual
    attempt.score = total.quantize(Decimal("0.01"))
    attempt.submitted_at = attempt.submitted_at or timezone.now()
    attempt.status = (QuizAttempt.Status.SUBMITTED if needs_manual
                      else QuizAttempt.Status.GRADED)
    attempt.save()
    return attempt


def regrade_attempt(attempt):
    needs_manual = attempt.answers.filter(needs_manual=True).exists()
    attempt.score = attempt.answers.aggregate(total=Sum("awarded_points"))["total"] or Decimal("0")
    attempt.score = attempt.score.quantize(Decimal("0.01"))
    attempt.status = (QuizAttempt.Status.SUBMITTED if needs_manual
                      else QuizAttempt.Status.GRADED)
    attempt.save()
    return attempt


def _normalise(text):
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _submission_text(submission):
    parts = [submission.text or ""]
    if submission.file:
        try:
            with submission.file.open("rb") as handle:
                raw = handle.read(200_000)
            parts.append(raw.decode("utf-8", errors="ignore"))
        except (OSError, ValueError):
            pass
    return _normalise(" ".join(parts))[:20_000]


def check_assignment_plagiarism(assignment, threshold=Decimal("35")):
    submissions = list(assignment.submissions.select_related("student__user"))
    texts = {submission.pk: _submission_text(submission) for submission in submissions}
    reports = []
    checked_at = timezone.now()
    best = {submission.pk: Decimal("0") for submission in submissions}
    for index, first in enumerate(submissions):
        for second in submissions[index + 1:]:
            a, b = sorted((first, second), key=lambda row: row.pk)
            text_a, text_b = texts[a.pk], texts[b.pk]
            if len(text_a) < 20 or len(text_b) < 20:
                similarity = Decimal("0")
            else:
                similarity = (Decimal(str(SequenceMatcher(
                    None, text_a, text_b, autojunk=False).ratio())) * 100
                ).quantize(Decimal("0.01"))
            best[a.pk] = max(best[a.pk], similarity)
            best[b.pk] = max(best[b.pk], similarity)
            if similarity >= threshold:
                report, _created = PlagiarismReport.objects.update_or_create(
                    institution=assignment.institution, assignment=assignment,
                    submission_a=a, submission_b=b,
                    defaults={"similarity": similarity, "is_flagged": True})
                reports.append(report)
            else:
                PlagiarismReport.objects.filter(
                    assignment=assignment, submission_a=a, submission_b=b).delete()
    for submission in submissions:
        submission.plagiarism_score = best[submission.pk]
        submission.plagiarism_checked_at = checked_at
        submission.save(update_fields=["plagiarism_score", "plagiarism_checked_at"])
    return reports


def gradebook_data(site, students, published_only=True):
    categories = list(site.grade_categories.order_by("order", "pk"))
    items = list(site.grade_items.select_related("category").order_by(
        "category__order", "order", "pk"))
    if published_only:
        items = [item for item in items if item.is_published]
    items_by_category = {}
    for item in items:
        items_by_category.setdefault(item.category_id, []).append(item)
    entries = {
        (entry.item_id, entry.student_id): entry
        for entry in GradeEntry.objects.filter(item__in=items, student__in=students)
    }
    rows = []
    for student in students:
        cells = []
        category_rows = []
        weighted_total = Decimal("0")
        weight_sum = Decimal("0")
        for category in categories:
            cat_items = items_by_category.get(category.pk, [])
            if not cat_items:
                continue
            possible = sum((item.max_score for item in cat_items), Decimal("0"))
            earned = sum(
                ((entries.get((item.pk, student.pk)).score or Decimal("0"))
                 for item in cat_items), Decimal("0"))
            pct = ((earned / possible * 100).quantize(Decimal("0.01"))
                   if possible else None)
            if pct is not None and category.weight:
                weighted_total += pct * category.weight
                weight_sum += category.weight
            category_rows.append({"category": category, "percentage": pct})
            for item in cat_items:
                entry = entries.get((item.pk, student.pk))
                cells.append({"item": item, "entry": entry,
                              "score": entry.score if entry else None})
        overall = ((weighted_total / weight_sum).quantize(Decimal("0.01"))
                   if weight_sum else None)
        rows.append({"student": student, "cells": cells,
                     "categories": category_rows, "overall": overall})
    return {"categories": categories, "items": items, "rows": rows}


def submissions_for_grading(assignment):
    return Submission.objects.filter(assignment=assignment).select_related(
        "student__user").order_by("student__user__last_name",
                                  "student__user__first_name", "pk")
