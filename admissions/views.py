"""Admissions screens: a public front door and the office workflow."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from academics.models import Programme, Student
from core import jobs, tasks
from core.access import module_required, permission_required_code
from core.email_types import STUDENT_APPLICATION_RECEIVED
from core.models import Institution
from core.notifications import queue_message, send_message
from core.tenancy import current_institution_id, use_institution

from .documents import (
    build_certificate,
    build_id_card,
    build_id_cards,
    build_offer_letter,
)
from .forms import (
    AcceptOfferForm,
    AlumniForm,
    CertificateForm,
    ClearanceForm,
    DocumentUploadForm,
    EntryRequirementForm,
    InterviewForm,
    OfferForm,
    PublicApplicationForm,
    ReviewForm,
    StudentCardForm,
    TransferForm,
    TransitionForm,
)
from .models import (
    AlumniRecord,
    ApplicantDocument,
    Application,
    Certificate,
    EntryRequirement,
    Offer,
    StudentCard,
)
from .services import (
    AdmissionError,
    accept_offer,
    application_rate_limited,
    check_eligibility,
    clearance_for,
    graduate_student,
    issue_certificate,
    issue_id_card,
    make_offer,
    record_application_attempt,
    review_application,
    schedule_interview,
    transfer_in,
    transfer_out,
    transition_student,
    update_clearance,
)

STAFF_PERMS = ("admissions.manage", "admissions.review", "admissions.decide")


def _staff(view):
    return login_required(module_required("admissions")(
        permission_required_code(*STAFF_PERMS)(view)))


def _with_institution(request, institution):
    request.institution = institution


def _public_response(request, institution, template, context=None, status=200):
    """Render a public page in the institution that owns the token."""
    request.institution = institution
    with use_institution(institution):
        return render(request, template, context or {}, status=status)


def _message(request, form, prefix=""):
    error = " ".join(" ".join(values) for values in form.errors.values())
    messages.error(request, f"{prefix}{error}".strip())


def _tracking_url(request, application):
    from core.notifications import absolute_url

    return absolute_url(reverse(
        "admissions:track_application", args=[application.tracking_token]))


# ---------- public ----------


@module_required("admissions")
def application_start(request):
    institution = request.institution
    if institution is None:
        institution = Institution.objects.filter(
            pk=current_institution_id()).first()
    if institution is None:
        raise Http404("No institution is available for applications.")
    if request.method == "POST" and application_rate_limited(request, institution):
        return render(request, "admissions/apply.html", {
            "form": PublicApplicationForm(institution=institution),
            "rate_limited": True,
        }, status=429)
    if request.method == "POST":
        form = PublicApplicationForm(request.POST, request.FILES,
                                      institution=institution)
        if form.is_valid():
            application = form.save()
            record_application_attempt(request, institution)
            link = _tracking_url(request, application)
            message = queue_message(
                "email", application.email,
                (f"Thank you for applying to {institution.name}.\n\n"
                 f"Your reference is {application.reference}.\n"
                 f"Track your application here: {link}\n"),
                subject=f"Application {application.reference} received",
                institution=institution, related_object=application,
                email_type=STUDENT_APPLICATION_RECEIVED)
            send_message(message)
            return redirect("admissions:application_success",
                            token=application.tracking_token)
    else:
        form = PublicApplicationForm(institution=institution)
    return render(request, "admissions/apply.html", {"form": form})


@module_required("admissions")
def application_success(request, token):
    application = get_object_or_404(
        Application.all_objects, tracking_token=token)
    return _public_response(
        request, application.institution,
        "admissions/application_success.html", {
            "application": application,
            "tracking_url": _tracking_url(request, application),
        })


@module_required("admissions")
def track_application(request, token):
    application = get_object_or_404(
        Application.all_objects.select_related(
            "institution", "programme", "school_class", "programme_year", "cohort"),
        tracking_token=token)
    _with_institution(request, application.institution)
    with use_institution(application.institution):
        offer = Offer.objects.filter(application=application).first()
        context = {"application": application, "offer": offer}
    return _public_response(
        request, application.institution, "admissions/track.html", context)


@module_required("admissions")
def offer_accept(request, token):
    offer = get_object_or_404(
        Offer.all_objects.select_related(
            "application__institution", "application__programme",
            "application__school_class"),
        application__tracking_token=token)
    application = offer.application
    _with_institution(request, application.institution)
    if offer.status == Offer.Status.ACCEPTED and offer.student_id:
        return redirect("admissions:track_application", token=token)
    form = AcceptOfferForm(request.POST or None, offer=offer)
    accepted_student = None
    if request.method == "POST" and form.is_valid():
        try:
            accepted_student = accept_offer(
                offer,
                accepted_by_name=form.cleaned_data["accepted_by_name"],
                deposit_method=form.cleaned_data["deposit_method"],
                deposit_reference=form.cleaned_data["deposit_reference"],
                request=request)
        except (AdmissionError, ValidationError) as exc:
            _message(request, form, "The offer could not be accepted: ")
            messages.error(request, " ".join(exc.messages) if hasattr(exc, "messages")
                           else str(exc))
        else:
            messages.success(request, "Your place is confirmed. Welcome!")
    return _public_response(
        request, application.institution, "admissions/offer_accept.html", {
            "offer": offer,
            "application": application,
            "form": form,
            "student": accepted_student,
        })


@module_required("admissions")
def offer_letter(request, token):
    offer = get_object_or_404(
        Offer.all_objects.select_related(
            "application__institution", "application__programme",
            "application__school_class"),
        application__tracking_token=token)
    with use_institution(offer.application.institution):
        pdf = build_offer_letter(offer, request=request)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="offer-{offer.offer_no}.pdf"')
    return response


@module_required("admissions")
def verify_certificate(request, token):
    certificate = get_object_or_404(
        Certificate.all_objects.select_related(
            "student__institution", "student__programme", "student__user"),
        token=token)
    return _public_response(
        request, certificate.student.institution,
        "admissions/verify_certificate.html", {"certificate": certificate})


@module_required("admissions")
def verify_card(request, token):
    card = get_object_or_404(
        StudentCard.all_objects.select_related(
            "student__institution", "student__programme", "student__user"),
        token=token)
    return _public_response(
        request, card.student.institution, "admissions/verify_card.html",
        {"card": card})


# ---------- applications office ----------


@_staff
def application_list(request):
    applications = Application.objects.select_related(
        "programme", "school_class").order_by("-submitted_at", "-pk")
    status = request.GET.get("status", "")
    query = request.GET.get("q", "").strip()
    if status:
        applications = applications.filter(status=status)
    if query:
        applications = applications.filter(
            Q(first_name__icontains=query) | Q(last_name__icontains=query)
            | Q(email__icontains=query) | Q(reference__icontains=query)
            | Q(phone__icontains=query))
    from urllib.parse import urlencode

    from django.core.paginator import Paginator

    page = Paginator(applications, 25).get_page(request.GET.get("page"))
    filters = {key: value for key, value in (("q", query), ("status", status)) if value}
    return render(request, "admissions/application_list.html", {
        "applications": page.object_list,
        "page_obj": page,
        "extra_query": ("&" + urlencode(filters)) if filters else "",
        "status": status,
        "query": query,
        "statuses": Application.Status.choices,
    })


@_staff
def application_detail(request, pk):
    application = get_object_or_404(
        Application.objects.select_related(
            "programme", "programme_year", "cohort", "school_class",
            "reviewed_by", "institution"),
        pk=pk)
    offer = Offer.objects.filter(application=application).first()
    return render(request, "admissions/application_detail.html", {
        "application": application,
        "offer": offer,
        "documents": application.documents.all(),
        "results": application.results.all(),
        "review_form": ReviewForm(initial={"status": application.status,
                                           "note": application.decision_note}),
        "interview_form": InterviewForm(initial={
            "interview_at": application.interview_at,
            "location": application.interview_location,
            "note": application.interview_note,
        }),
        "offer_form": OfferForm(),
        "upload_form": DocumentUploadForm(),
    })


@_staff
def application_review(request, pk):
    application = get_object_or_404(Application, pk=pk)
    if request.method != "POST":
        return redirect("admissions:application_detail", pk=pk)
    form = ReviewForm(request.POST)
    if form.is_valid():
        try:
            review_application(application, form.cleaned_data["status"],
                               actor=request.user, note=form.cleaned_data["note"],
                               request=request)
        except AdmissionError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "The application was updated.")
    else:
        _message(request, form, "The application was not updated: ")
    return redirect("admissions:application_detail", pk=pk)


@_staff
def application_interview(request, pk):
    application = get_object_or_404(Application, pk=pk)
    if request.method != "POST":
        return redirect("admissions:application_detail", pk=pk)
    form = InterviewForm(request.POST)
    if form.is_valid():
        try:
            schedule_interview(
                application, form.cleaned_data["interview_at"],
                location=form.cleaned_data["location"],
                note=form.cleaned_data["note"], actor=request.user, request=request)
        except AdmissionError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "The interview was scheduled.")
    else:
        _message(request, form, "The interview was not scheduled: ")
    return redirect("admissions:application_detail", pk=pk)


@_staff
def application_offer(request, pk):
    application = get_object_or_404(Application, pk=pk)
    if request.method != "POST":
        return redirect("admissions:application_detail", pk=pk)
    form = OfferForm(request.POST)
    if form.is_valid():
        try:
            make_offer(application, form.cleaned_data["acceptance_deadline"],
                       form.cleaned_data["deposit_amount"],
                       actor=request.user, request=request)
        except AdmissionError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "The offer is ready. Send the applicant the tracking link.")
    else:
        _message(request, form, "The offer was not created: ")
    return redirect("admissions:application_detail", pk=pk)


@_staff
def application_eligibility(request, pk):
    application = get_object_or_404(Application, pk=pk)
    eligible, note = check_eligibility(application)
    messages.info(request, note or "No entry requirement is set.")
    return redirect("admissions:application_detail", pk=pk)


@_staff
def application_document(request, pk, document_pk):
    application = get_object_or_404(Application, pk=pk)
    document = get_object_or_404(
        ApplicantDocument, pk=document_pk, application=application)
    try:
        handle = document.file.open("rb")
    except (OSError, ValueError):
        raise Http404("The file could not be opened.") from None
    return FileResponse(handle, as_attachment=True,
                        filename=document.file.name.rsplit("/", 1)[-1])


@_staff
def application_upload(request, pk):
    application = get_object_or_404(Application, pk=pk)
    if request.method != "POST":
        return redirect("admissions:application_detail", pk=pk)
    form = DocumentUploadForm(request.POST, request.FILES)
    if form.is_valid():
        ApplicantDocument.objects.create(
            institution=application.institution, application=application,
            kind=form.cleaned_data["kind"], file=form.cleaned_data["file"])
        messages.success(request, "The document was uploaded.")
    else:
        _message(request, form, "The document was not uploaded: ")
    return redirect("admissions:application_detail", pk=pk)


# ---------- offers ----------


@_staff
def offer_list(request):
    offers = Offer.objects.select_related(
        "application", "application__programme", "student").order_by(
            "-issued_at", "-pk")
    status = request.GET.get("status", "")
    if status:
        offers = offers.filter(status=status)
    return render(request, "admissions/offer_list.html", {
        "offers": offers, "status": status, "statuses": Offer.Status.choices,
    })


@_staff
def offer_detail(request, pk):
    offer = get_object_or_404(
        Offer.objects.select_related(
            "application", "application__programme", "student", "issued_by"),
        pk=pk)
    return render(request, "admissions/offer_detail.html", {"offer": offer})


@_staff
def offer_pdf(request, pk):
    offer = get_object_or_404(
        Offer.objects.select_related(
            "application__institution", "application__programme",
            "application__school_class"),
        pk=pk)
    pdf = build_offer_letter(offer, request=request)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="offer-{offer.offer_no}.pdf"')
    return response


# ---------- entry requirements ----------


@_staff
def entry_requirements(request):
    requirements = {
        row.programme_id: row
        for row in EntryRequirement.objects.select_related("programme")
    }
    programmes = [
        (programme, requirements.get(programme.pk))
        for programme in Programme.objects.all()
    ]
    return render(request, "admissions/entry_requirements.html", {
        "programmes": programmes,
    })


@_staff
def entry_requirement_edit(request, programme_pk):
    programme = get_object_or_404(Programme, pk=programme_pk)
    requirement = EntryRequirement.objects.filter(programme=programme).first()
    form = EntryRequirementForm(
        request.POST or None, instance=requirement,
        initial={"programme": programme})
    form.fields["programme"].disabled = True
    if request.method == "POST" and form.is_valid():
        row = form.save(commit=False)
        row.institution = request.institution
        row.programme = programme
        row.save()
        messages.success(request, f"Entry requirements saved for {programme}.")
        return redirect("admissions:entry_requirements")
    return render(request, "admissions/form.html", {
        "title": f"Entry requirements — {programme}",
        "form": form,
        "back": "admissions:entry_requirements",
    })


# ---------- lifecycle ----------


@_staff
def lifecycle_list(request):
    students = Student.objects.select_related(
        "user", "programme", "school_class").order_by(
            "user__last_name", "user__first_name")
    status = request.GET.get("status", "")
    query = request.GET.get("q", "").strip()
    if status:
        students = students.filter(status=status)
    if query:
        from core.numbering import matching_pks

        numbers = matching_pks(students, "student_no", query)
        students = students.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(student_no__icontains=query)
            | Q(pk__in=numbers)
            | Q(programme__name__icontains=query))
    return render(request, "admissions/lifecycle_list.html", {
        "students": students, "status": status, "query": query,
        "statuses": Student.Status.choices,
    })


@_staff
def lifecycle_detail(request, pk):
    student = get_object_or_404(
        Student.objects.select_related("user", "programme", "school_class"), pk=pk)
    return render(request, "admissions/lifecycle_detail.html", {
        "student": student,
        "transitions": student.lifecycle_transitions.all()[:50],
        "transfers": student.transfers.all(),
        "transition_form": TransitionForm(student=student),
        "transfer_form": TransferForm(),
        "clearance": getattr(student, "graduation_clearance", None),
        "certificates": student.certificates.all(),
        "card": getattr(student, "id_card", None),
    })


@_staff
def lifecycle_transition(request, pk):
    student = get_object_or_404(Student, pk=pk)
    if request.method != "POST":
        return redirect("admissions:lifecycle_detail", pk=pk)
    form = TransitionForm(request.POST, student=student)
    if form.is_valid():
        try:
            transition_student(
                student, form.cleaned_data["to_state"],
                form.cleaned_data["reason"], actor=request.user,
                effective_date=form.cleaned_data["effective_date"],
                request=request)
        except AdmissionError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "The student's status was updated.")
    else:
        _message(request, form, "The status was not changed: ")
    return redirect("admissions:lifecycle_detail", pk=pk)


@_staff
def student_transfer(request, pk):
    student = get_object_or_404(Student, pk=pk)
    if request.method != "POST":
        return redirect("admissions:lifecycle_detail", pk=pk)
    form = TransferForm(request.POST)
    if form.is_valid():
        service = (transfer_out if form.cleaned_data["direction"] == "out"
                   else transfer_in)
        try:
            service(student, form.cleaned_data["other_institution"],
                    reference=form.cleaned_data["reference"],
                    reason=form.cleaned_data["reason"], actor=request.user,
                    effective_date=form.cleaned_data["effective_date"],
                    request=request)
        except AdmissionError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "The transfer was recorded.")
    else:
        _message(request, form, "The transfer was not recorded: ")
    return redirect("admissions:lifecycle_detail", pk=pk)


# ---------- graduation and certificates ----------


@_staff
def graduation_list(request):
    students = Student.objects.filter(status__in=[
        Student.Status.ACTIVE, Student.Status.ENROLLED, Student.Status.DEFERRED,
        Student.Status.SUSPENDED, Student.Status.GRADUATED,
    ]).select_related("user", "programme").order_by(
        "user__last_name", "user__first_name")
    query = request.GET.get("q", "").strip()
    if query:
        from core.numbering import matching_pks

        numbers = matching_pks(students, "student_no", query)
        students = students.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(student_no__icontains=query)
            | Q(pk__in=numbers))
    return render(request, "admissions/graduation_list.html", {
        "students": students, "query": query,
    })


@_staff
def graduation_detail(request, pk):
    student = get_object_or_404(
        Student.objects.select_related("user", "programme"), pk=pk)
    clearance = clearance_for(student)
    return render(request, "admissions/graduation_detail.html", {
        "student": student,
        "clearance": clearance,
        "clearance_form": ClearanceForm(clearance=clearance),
        "certificate_form": CertificateForm(initial={
            "title": "Certificate of Award",
            "graduation_date": timezone.localdate(),
            "award": student.programme.name,
        }),
        "certificates": student.certificates.all(),
    })


@_staff
def graduation_clearance(request, pk):
    student = get_object_or_404(Student, pk=pk)
    if request.method != "POST":
        return redirect("admissions:graduation_detail", pk=pk)
    form = ClearanceForm(request.POST)
    if form.is_valid():
        update_clearance(student, form.cleaned_data, actor=request.user,
                         request=request)
        messages.success(request, "The clearance checklist was saved.")
    else:
        _message(request, form, "The checklist was not saved: ")
    return redirect("admissions:graduation_detail", pk=pk)


@_staff
def graduation_graduate(request, pk):
    student = get_object_or_404(Student, pk=pk)
    if request.method != "POST":
        return redirect("admissions:graduation_detail", pk=pk)
    try:
        graduate_student(student, actor=request.user, request=request)
    except AdmissionError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "The student has graduated.")
    return redirect("admissions:graduation_detail", pk=pk)


@_staff
def certificate_new(request, pk):
    student = get_object_or_404(Student, pk=pk)
    if request.method != "POST":
        return redirect("admissions:graduation_detail", pk=pk)
    form = CertificateForm(request.POST)
    if form.is_valid():
        try:
            certificate = issue_certificate(
                student, form.cleaned_data["award"],
                title=form.cleaned_data["title"], kind=form.cleaned_data["kind"],
                graduation_date=form.cleaned_data["graduation_date"],
                actor=request.user, request=request)
        except AdmissionError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"Certificate {certificate.serial_no} issued.")
    else:
        _message(request, form, "The certificate was not issued: ")
    return redirect("admissions:graduation_detail", pk=pk)


@_staff
def certificate_list(request):
    certificates = Certificate.objects.select_related(
        "student__user", "student__programme").order_by("-issued_at", "-pk")
    query = request.GET.get("q", "").strip()
    if query:
        from core.numbering import matching_pks

        numbers = matching_pks(certificates, "student__student_no", query)
        certificates = certificates.filter(
            Q(serial_no__icontains=query)
            | Q(student__student_no__icontains=query)
            | Q(pk__in=numbers)
            | Q(student__user__first_name__icontains=query)
            | Q(student__user__last_name__icontains=query)
            | Q(award__icontains=query))
    return render(request, "admissions/certificate_list.html", {
        "certificates": certificates, "query": query,
    })


@_staff
def certificate_detail(request, pk):
    certificate = get_object_or_404(
        Certificate.objects.select_related(
            "student__user", "student__programme", "issued_by"), pk=pk)
    return render(request, "admissions/certificate_detail.html", {
        "certificate": certificate,
    })


@_staff
def certificate_pdf(request, pk):
    certificate = get_object_or_404(
        Certificate.objects.select_related(
            "student__institution", "student__programme", "student__user"), pk=pk)
    pdf = build_certificate(certificate, request=request)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="certificate-{certificate.serial_no}.pdf"')
    return response


# ---------- identity cards ----------


@_staff
def id_cards(request):
    students = Student.objects.select_related(
        "user", "programme").order_by("user__last_name", "user__first_name")
    cards = {
        card.student_id: card
        for card in StudentCard.objects.select_related("student")
    }
    from datetime import timedelta

    rows = [(student, cards.get(student.pk)) for student in students]
    return render(request, "admissions/id_cards.html", {
        "rows": rows,
        "suggested_valid_until": timezone.localdate() + timedelta(days=365),
    })


@_staff
def id_card_issue(request, pk):
    student = get_object_or_404(Student, pk=pk)
    card = StudentCard.objects.filter(student=student).first()
    if request.method != "POST":
        return redirect("admissions:id_cards")
    form = StudentCardForm(request.POST, request.FILES, instance=card)
    if form.is_valid():
        try:
            row = issue_id_card(
                student, form.cleaned_data["valid_until"],
                photo=form.cleaned_data.get("photo"), actor=request.user,
                request=request)
        except AdmissionError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            row.is_active = form.cleaned_data["is_active"]
            row.save(update_fields=["is_active"])
            messages.success(request, f"Card {row.card_no} is ready to print.")
    else:
        _message(request, form, "The card was not created: ")
    return redirect("admissions:id_cards")


@_staff
def id_card_pdf(request, pk):
    card = get_object_or_404(
        StudentCard.objects.select_related(
            "student__institution", "student__programme", "student__user"), pk=pk)
    pdf = build_id_card(card, request=request)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="card-{card.card_no}.pdf"')
    return response


@_staff
def id_card_bulk(request):
    if request.method != "POST":
        return redirect("admissions:id_cards")
    student_ids = request.POST.getlist("student")
    cards = StudentCard.objects.filter(
        student_id__in=student_ids).select_related(
            "student__institution", "student__programme", "student__user")
    if not cards:
        messages.error(request, "Choose at least one student with a card.")
        return redirect("admissions:id_cards")
    card_ids = list(cards.values_list("pk", flat=True))
    if jobs.jobs_available():
        job = jobs.create_job(
            "pdf", request.institution, request.user,
            label="Student identity cards", total=len(card_ids),
        )
        jobs.enqueue(tasks.build_id_cards_job, job, card_ids)
        messages.success(
            request,
            "The cards are being built in the background. "
            "This page shows the progress and the download link.")
        return redirect("job_detail", pk=job.pk)
    pdf = build_id_cards(cards, request=request)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="student-cards.pdf"'
    return response


# ---------- alumni ----------


@_staff
def alumni_list(request):
    alumni = AlumniRecord.objects.select_related(
        "student__user", "student__programme").order_by(
            "student__user__last_name", "student__user__first_name")
    query = request.GET.get("q", "").strip()
    if query:
        from core.numbering import matching_pks

        numbers = matching_pks(alumni, "student__student_no", query)
        alumni = alumni.filter(
            Q(student__user__first_name__icontains=query)
            | Q(student__user__last_name__icontains=query)
            | Q(student__student_no__icontains=query)
            | Q(pk__in=numbers)
            | Q(contact_email__icontains=query)
            | Q(employer__icontains=query))
    return render(request, "admissions/alumni_list.html", {
        "alumni": alumni, "query": query,
    })


@login_required
def my_id_card_pdf(request):
    """The signed-in student downloads their own identity card PDF."""
    student = get_object_or_404(
        Student.objects.select_related(
            "user", "institution", "programme", "school_class", "stream"),
        user=request.user)
    card = StudentCard.objects.filter(institution=student.institution, student=student).first()
    if card is None:
        messages.error(request, "No identity card has been issued for you yet.")
        return redirect("student_portal")
    pdf = build_id_card(card, request=request)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="card-{card.card_no}.pdf"'
    return response


@_staff
def alumni_detail(request, pk):
    record = get_object_or_404(
        AlumniRecord.objects.select_related(
            "student__user", "student__programme"), pk=pk)
    form = AlumniForm(request.POST or None, instance=record)
    if request.method == "POST" and form.is_valid():
        row = form.save(commit=False)
        row.updated_by = request.user
        row.save()
        messages.success(request, "The alumni record was saved.")
        return redirect("admissions:alumni_detail", pk=pk)
    return render(request, "admissions/alumni_detail.html", {
        "record": record, "form": form,
    })
