from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Assignment, Question, Quiz, QuizAttempt, Submission
from .services import (
    ensure_grade_item,
    ensure_quiz_grade_item,
    sync_assignment_grade,
    sync_best_quiz_grade,
)


@receiver(post_save, sender=Assignment)
def assignment_created(sender, instance, **kwargs):
    ensure_grade_item(instance)


@receiver(post_save, sender=Quiz)
def quiz_created(sender, instance, **kwargs):
    ensure_quiz_grade_item(instance)


@receiver(post_save, sender=Question)
def question_saved(sender, instance, **kwargs):
    ensure_quiz_grade_item(instance.quiz)


@receiver(post_save, sender=Submission)
def submission_saved(sender, instance, **kwargs):
    if instance.mark is not None:
        sync_assignment_grade(instance)


@receiver(post_save, sender=QuizAttempt)
def quiz_attempt_saved(sender, instance, **kwargs):
    if instance.status == QuizAttempt.Status.GRADED and instance.score is not None:
        sync_best_quiz_grade(instance.quiz, instance.student)
