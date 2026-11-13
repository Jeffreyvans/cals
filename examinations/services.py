"""Examination timetable checks, seating, moderation and board exports.

Papers may be moved or added before every candidate is known, so a paper is
checked twice: when it is saved (date window, venue and invigilator) and on the
clash screen (which also compares the candidates of overlapping papers).
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from core.audit import log_action

from .models import Exam, ExamCandidate, ExamPaper

BOARD_EXPORTS = {
    "zimsec": "ZIMSEC",
    "hexco": "HEXCO",
}


def paper_window(paper):
    """The start and end instants of a paper, from its date and duration."""
    start = datetime.combine(paper.date, paper.start_time)
    minutes = paper.duration_minutes if paper.duration_minutes is not None else 120
    return start, start + timedelta(minutes=minutes)


def overlapping_papers(paper, queryset=None):
    """Papers on the same day whose time overlaps this paper's window."""
    if not (paper.date and paper.start_time):
        return []
    start, end = paper_window(paper)
    papers = queryset if queryset is not None else ExamPaper.objects.all()
    papers = papers.filter(date=paper.date)
    if paper.pk:
        papers = papers.exclude(pk=paper.pk)
    overlaps = []
    for other in papers.select_related("exam", "subject", "course", "invigilator"):
        other_start, other_end = paper_window(other)
        if other_start < end and start < other_end:
            overlaps.append(other)
    return overlaps


def schedule_issues(paper):
    """Date-window, venue and invigilator problems for one paper."""
    issues = []
    if paper.exam_id and paper.date:
        if paper.date < paper.exam.starts_on or paper.date > paper.exam.ends_on:
            issues.append(
                {
                    "kind": "Date",
                    "key": ("date", paper.pk or id(paper)),
                    "message": (
                        f"{paper} is on {paper.date}, outside the sitting "
                        f"({paper.exam.starts_on} to {paper.exam.ends_on})."
                    ),
                }
            )
    for other in overlapping_papers(paper):
        low, high = sorted((paper.pk or id(paper), other.pk or id(other)))
        if (
            paper.venue
            and other.venue
            and paper.venue.strip().lower() == other.venue.strip().lower()
        ):
            issues.append(
                {
                    "kind": "Venue",
                    "key": ("venue", low, high),
                    "message": (
                        f"Venue {paper.venue} is also booked for {other} "
                        f"at {other.start_time:%H:%M} on {other.date}."
                    ),
                }
            )
        if paper.invigilator_id and paper.invigilator_id == other.invigilator_id:
            name = paper.invigilator.get_full_name() or paper.invigilator.username
            issues.append(
                {
                    "kind": "Invigilator",
                    "key": ("invigilator", low, high),
                    "message": (
                        f"{name} is already invigilating {other} "
                        f"at {other.start_time:%H:%M} on {other.date}."
                    ),
                }
            )
    return issues


def student_paper_clashes(paper, student, exclude_pk=None):
    """Other candidates of this student that overlap the given paper."""
    candidates = ExamCandidate.objects.filter(
        paper__in=overlapping_papers(paper), student=student
    ).select_related("paper", "paper__exam")
    if exclude_pk:
        candidates = candidates.exclude(pk=exclude_pk)
    return list(candidates)


def candidate_issues(paper):
    """Students entered for two papers that run at the same time."""
    issues = []
    if not paper.pk:
        return issues
    for candidate in paper.candidates.select_related("student__user"):
        for other in student_paper_clashes(paper, candidate.student):
            low, high = sorted((paper.pk, other.paper_id))
            issues.append(
                {
                    "kind": "Candidate",
                    "key": ("candidate", low, high, candidate.student_id),
                    "message": (
                        f"{candidate.student} is also sitting {other.paper} "
                        f"at {other.paper.start_time:%H:%M} on {other.paper.date}."
                    ),
                }
            )
    return issues


def paper_issues(paper):
    return schedule_issues(paper) + candidate_issues(paper)


def clash_report(exam=None):
    """Every distinct clash, optionally limited to one sitting."""
    papers = ExamPaper.objects.all()
    if exam is not None:
        papers = papers.filter(exam=exam)
    seen = set()
    issues = []
    for paper in papers.select_related("exam", "subject", "course", "invigilator"):
        for issue in paper_issues(paper):
            if issue["key"] in seen:
                continue
            seen.add(issue["key"])
            issue["paper"] = paper
            issues.append(issue)
    issues.sort(key=lambda issue: (issue["paper"].date, issue["paper"].start_time))
    return issues


def allocate_seats(paper):
    """Number the candidates of one paper from 1, in candidate-number order."""
    candidates = list(
        paper.candidates.select_related("student__user").order_by(
            "candidate_no",
            "student__user__last_name",
            "student__user__first_name",
            "pk",
        )
    )
    for number, candidate in enumerate(candidates, start=1):
        candidate.seat_no = str(number)
    ExamCandidate.objects.bulk_update(candidates, ["seat_no"])
    return len(candidates)


def submit_paper(paper, actor=None, request=None):
    """Send a paper's entered marks for moderation."""
    if paper.mark_status != ExamPaper.MarkStatus.ENTERING:
        raise ValidationError("Only a paper whose marks are being entered can be submitted.")
    candidates = paper.candidates.all()
    if not candidates.exists():
        raise ValidationError("Enter at least one candidate before submitting the paper.")
    missing = candidates.filter(mark__isnull=True).count()
    if missing:
        raise ValidationError(
            f"{missing} candidate{'s' if missing != 1 else ''} still have no mark."
        )
    before = {"status": paper.mark_status}
    paper.mark_status = ExamPaper.MarkStatus.SUBMITTED
    paper.submitted_by = actor
    paper.submitted_at = timezone.now()
    paper.moderated_by = paper.moderated_at = None
    paper.released_by = paper.released_at = None
    paper.moderation_note = ""
    paper.save()
    log_action(
        actor,
        "examinations.paper.submitted",
        paper,
        before=before,
        after={"status": paper.mark_status},
        request=request,
    )
    return paper


def moderate_paper(paper, actor=None, note="", request=None):
    """Lock a submitted paper's marks after moderation."""
    if paper.mark_status != ExamPaper.MarkStatus.SUBMITTED:
        raise ValidationError("Only a submitted paper can be moderated.")
    before = {"status": paper.mark_status}
    paper.mark_status = ExamPaper.MarkStatus.MODERATED
    paper.moderated_by = actor
    paper.moderated_at = timezone.now()
    paper.moderation_note = (note or "").strip()[:500]
    paper.save()
    log_action(
        actor,
        "examinations.paper.moderated",
        paper,
        before=before,
        after={"status": paper.mark_status},
        request=request,
    )
    return paper


def release_paper(paper, actor=None, request=None):
    """Publish a moderated paper's results to the candidates."""
    if paper.mark_status != ExamPaper.MarkStatus.MODERATED:
        raise ValidationError("Only a moderated paper can be released.")
    before = {"status": paper.mark_status}
    paper.mark_status = ExamPaper.MarkStatus.RELEASED
    paper.released_by = actor
    paper.released_at = timezone.now()
    paper.save()
    ExamCandidate.objects.filter(paper=paper, published=False).update(published=True)
    log_action(
        actor,
        "examinations.paper.released",
        paper,
        before=before,
        after={"status": paper.mark_status},
        request=request,
    )
    _mark_exam_published_if_done(paper.exam)
    return paper


def reopen_paper(paper, actor=None, note="", request=None):
    """Unlock a submitted, moderated or released paper for corrections."""
    if paper.mark_status == ExamPaper.MarkStatus.ENTERING:
        raise ValidationError("This paper's marks are already open for editing.")
    before = {"status": paper.mark_status}
    paper.mark_status = ExamPaper.MarkStatus.ENTERING
    paper.submitted_by = paper.submitted_at = None
    paper.moderated_by = paper.moderated_at = None
    paper.released_by = paper.released_at = None
    paper.moderation_note = (note or "").strip()[:500]
    paper.save()
    ExamCandidate.objects.filter(paper=paper).update(published=False)
    log_action(
        actor,
        "examinations.paper.reopened",
        paper,
        before=before,
        after={"status": paper.mark_status},
        request=request,
    )
    return paper


def _mark_exam_published_if_done(exam):
    if not exam.papers.exists():
        return
    if exam.papers.exclude(mark_status=ExamPaper.MarkStatus.RELEASED).exists():
        return
    if exam.status != Exam.Status.PUBLISHED:
        exam.status = Exam.Status.PUBLISHED
        exam.save(update_fields=["status"])


def release_results(exam, actor=None):
    """Publish a sitting once every paper has been moderated."""
    papers = ExamPaper.objects.filter(exam=exam)
    pending = papers.filter(
        mark_status__in=[
            ExamPaper.MarkStatus.ENTERING,
            ExamPaper.MarkStatus.SUBMITTED,
        ]
    )
    if pending.exists():
        raise ValidationError(
            "Moderate every paper before releasing the sitting's results."
        )
    updated = ExamCandidate.objects.filter(paper__exam=exam, published=False).update(
        published=True
    )
    papers.update(
        mark_status=ExamPaper.MarkStatus.RELEASED,
        released_by=actor,
        released_at=timezone.now(),
    )
    if exam.status != Exam.Status.PUBLISHED:
        exam.status = Exam.Status.PUBLISHED
        exam.save(update_fields=["status"])
    return updated


BOARD_HEADERS = {
    "zimsec": [
        "Centre",
        "Candidate no",
        "Student no",
        "Candidate name",
        "Subject code",
        "Subject",
        "Paper date",
        "Session",
        "Mark",
        "Grade",
    ],
    "hexco": [
        "Centre",
        "Student no",
        "Candidate no",
        "Candidate name",
        "Programme",
        "Course code",
        "Course",
        "Mark",
        "Grade",
        "Session",
    ],
}


def _paper_identity(paper):
    if paper.subject_id:
        return paper.subject.code, paper.subject.name, ""
    course = paper.course
    return course.code, course.title, course.programme.code


def board_rows(exam=None, board="zimsec") -> Iterable[list[str]]:
    """Rows for a ZIMSEC or HEXCO submission; only moderated results."""
    board = board if board in BOARD_EXPORTS else "zimsec"
    candidates = ExamCandidate.objects.select_related(
        "paper",
        "paper__institution",
        "paper__subject",
        "paper__course",
        "paper__course__programme",
        "student__user",
    ).filter(
        paper__mark_status__in=[
            ExamPaper.MarkStatus.MODERATED,
            ExamPaper.MarkStatus.RELEASED,
        ],
        mark__isnull=False,
    )
    if exam is not None:
        candidates = candidates.filter(paper__exam=exam)
    for candidate in candidates.order_by(
        "paper__date",
        "paper__start_time",
        "candidate_no",
        "student__user__last_name",
    ):
        paper = candidate.paper
        code, title, programme = _paper_identity(paper)
        centre = paper.institution.name
        name = candidate.student.user.get_full_name() or candidate.student.user.username
        if board == "hexco":
            yield [
                centre,
                candidate.student.student_no,
                candidate.candidate_no,
                name,
                programme,
                code,
                title,
                str(candidate.mark),
                candidate.grade,
                paper.get_session_display(),
            ]
        else:
            yield [
                centre,
                candidate.candidate_no,
                candidate.student.student_no,
                name,
                code,
                title,
                paper.date.isoformat(),
                paper.get_session_display(),
                str(candidate.mark),
                candidate.grade,
            ]


def candidate_rows(exam=None) -> Iterable[list[str]]:
    """Rows for the candidate CSV export, in timetable order."""
    candidates = ExamCandidate.objects.select_related(
        "paper", "paper__exam", "paper__subject", "paper__course", "student__user"
    )
    if exam is not None:
        candidates = candidates.filter(paper__exam=exam)
    for candidate in candidates.order_by(
        "paper__date",
        "paper__start_time",
        "candidate_no",
        "student__user__last_name",
    ):
        paper = candidate.paper
        yield [
            paper.exam.name,
            paper.date.isoformat(),
            paper.get_session_display(),
            str(paper.subject or paper.course),
            candidate.candidate_no,
            candidate.seat_no,
            candidate.student.student_no,
            candidate.student.user.get_full_name() or candidate.student.user.username,
            "" if candidate.mark is None else str(candidate.mark),
            candidate.grade,
            "yes" if candidate.published else "no",
        ]
