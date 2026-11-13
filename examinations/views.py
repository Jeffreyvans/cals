import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.access import user_has_any_code
from core.audit import log_action, log_permission_denied
from core.modules import is_enabled

from .models import Exam, ExamCandidate, ExamPaper
from .services import (
    BOARD_EXPORTS,
    BOARD_HEADERS,
    allocate_seats,
    board_rows,
    candidate_rows,
    clash_report,
    moderate_paper,
    release_paper,
    release_results,
    reopen_paper,
    submit_paper,
)


def _guard(request, permissions=("exams.manage", "exams.invigilate")):
    if not is_enabled("exams", request.institution):
        raise Http404("This module is not switched on for your institution.")
    codes = (permissions,) if isinstance(permissions, str) else tuple(permissions)
    if not user_has_any_code(request.user, codes):
        log_permission_denied(request, codes)
        raise PermissionDenied(
            "Your role does not allow this action. Ask an administrator for access."
        )


@login_required
def clash_check(request):
    _guard(request)
    exam_id = request.GET.get("exam") or ""
    exam = None
    if exam_id:
        exam = get_object_or_404(Exam.objects.all(), pk=exam_id)
    issues = clash_report(exam)
    return render(
        request,
        "examinations/clash_check.html",
        {
            "exam": exam,
            "exams": Exam.objects.all(),
            "issues": issues,
            "hub_url": reverse("examinations:hub"),
            "export_url": reverse("examinations:candidate_export")
            + (f"?exam={exam.pk}" if exam else ""),
        },
    )


@login_required
def exam_manage(request, pk):
    _guard(request)
    exam = get_object_or_404(Exam.objects.select_related("term"), pk=pk)
    candidates = ExamCandidate.objects.filter(paper__exam=exam)
    papers = list(
        ExamPaper.objects.filter(exam=exam)
        .select_related("subject", "course")
        .order_by("date", "start_time", "pk")
    )
    for paper in papers:
        paper.candidate_count = paper.candidates.count()
        paper.marked_count = paper.candidates.filter(mark__isnull=False).count()
    return render(
        request,
        "examinations/exam_manage.html",
        {
            "exam": exam,
            "issues": clash_report(exam),
            "papers": papers,
            "candidate_count": candidates.count(),
            "published_count": candidates.filter(published=True).count(),
            "can_manage": user_has_any_code(request.user, ("exams.manage",)),
            "release_url": reverse("examinations:exam_release", args=[exam.pk]),
            "export_url": reverse("examinations:candidate_export") + f"?exam={exam.pk}",
            "zimsec_url": reverse("examinations:zimsec_export") + f"?exam={exam.pk}",
            "hexco_url": reverse("examinations:hexco_export") + f"?exam={exam.pk}",
            "list_url": reverse("examinations:list", args=["sittings"]),
            "clash_url": reverse("examinations:clash_check") + f"?exam={exam.pk}",
        },
    )


@login_required
def exam_release(request, pk):
    _guard(request, "exams.manage")
    exam = get_object_or_404(Exam.objects.all(), pk=pk)
    if request.method == "POST":
        try:
            updated = release_results(exam, actor=request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            log_action(
                request.user,
                "examinations.exam.released",
                instance=exam,
                after={"candidates_published": updated},
                request=request,
            )
            messages.success(
                request,
                f"Results released for {exam.name} ({updated} candidate"
                f"{'s' if updated != 1 else ''}).",
            )
    return redirect("examinations:exam_manage", pk=exam.pk)


PAPER_ACTIONS = {
    "submit": (submit_paper, "submitted for moderation"),
    "moderate": (moderate_paper, "moderated"),
    "release": (release_paper, "released"),
    "reopen": (reopen_paper, "reopened for editing"),
}


@login_required
def paper_action(request, pk, action):
    _guard(request, "exams.manage")
    if action not in PAPER_ACTIONS:
        raise Http404("There is no such moderation action.")
    paper = get_object_or_404(ExamPaper.objects.all(), pk=pk)
    if request.method == "POST":
        handler, label = PAPER_ACTIONS[action]
        note = request.POST.get("note", "")
        try:
            if action in ("moderate", "reopen"):
                handler(paper, actor=request.user, note=note, request=request)
            else:
                handler(paper, actor=request.user, request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"{paper} {label}.")
    return redirect("examinations:exam_manage", pk=paper.exam_id)


@login_required
def seating_plan(request, pk):
    _guard(request)
    paper = get_object_or_404(
        ExamPaper.objects.select_related("exam", "subject", "course"), pk=pk
    )
    can_manage = user_has_any_code(request.user, ("exams.manage",))
    if request.method == "POST" and can_manage:
        count = allocate_seats(paper)
        log_action(
            request.user,
            "examinations.paper.seats_allocated",
            instance=paper,
            after={"candidates": count},
            request=request,
        )
        messages.success(request, f"{count} seat number{'s' if count != 1 else ''} allocated.")
        return redirect("examinations:seating_plan", pk=paper.pk)
    candidates = paper.candidates.select_related("student__user").order_by(
        "seat_no", "candidate_no"
    )
    return render(
        request,
        "examinations/seating_plan.html",
        {
            "paper": paper,
            "candidates": candidates,
            "can_manage": can_manage,
            "list_url": reverse("examinations:list", args=["papers"]),
        },
    )


@login_required
def candidate_export(request):
    _guard(request, "exams.manage")
    exam_id = request.GET.get("exam") or ""
    exam = None
    if exam_id:
        exam = get_object_or_404(Exam.objects.all(), pk=exam_id)
    response = HttpResponse(content_type="text/csv")
    label = exam.name.lower().replace(" ", "-") if exam else "all"
    response["Content-Disposition"] = f'attachment; filename="exam-candidates-{label}.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "Exam",
            "Date",
            "Session",
            "Subject / course",
            "Candidate no",
            "Seat no",
            "Student no",
            "Student",
            "Mark",
            "Grade",
            "Published",
        ]
    )
    for row in candidate_rows(exam):
        writer.writerow(row)
    return response


@login_required
def board_export(request, board):
    _guard(request, "exams.manage")
    if board not in BOARD_EXPORTS:
        raise Http404("There is no such examination board.")
    exam_id = request.GET.get("exam") or ""
    exam = None
    if exam_id:
        exam = get_object_or_404(Exam.objects.all(), pk=exam_id)
    response = HttpResponse(content_type="text/csv")
    label = exam.name.lower().replace(" ", "-") if exam else "all"
    response["Content-Disposition"] = (
        f'attachment; filename="{board}-results-{label}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(BOARD_HEADERS[board])
    for row in board_rows(exam, board):
        writer.writerow(row)
    return response
