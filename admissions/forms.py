"""Forms for the public application and the admissions office."""

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from academics.models import Programme, SchoolClass, SchoolSettings

from .models import (
    AlumniRecord,
    ApplicantDocument,
    ApplicantResult,
    Application,
    Certificate,
    EntryRequirement,
    LifecycleState,
    Offer,
    StudentCard,
)
from .services import parse_results_text


class PublicApplicationForm(forms.ModelForm):
    """The form anyone can fill in without an account."""

    results_text = forms.CharField(
        label="Results",
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="One subject per line, for example Mathematics: B.")
    identity_document = forms.FileField(required=False, label="Identity document")
    results_document = forms.FileField(required=False, label="Results slip")
    birth_certificate = forms.FileField(required=False, label="Birth certificate")
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Application
        fields = [
            "first_name", "last_name", "date_of_birth", "gender", "national_id",
            "email", "phone", "address",
            "guardian_name", "guardian_relationship", "guardian_email",
            "guardian_phone", "guardian_address",
            "programme", "school_class", "year_or_grade",
            "previous_school", "previous_results",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "address": forms.Textarea(attrs={"rows": 2}),
            "guardian_address": forms.Textarea(attrs={"rows": 2}),
            "previous_results": forms.HiddenInput,
        }

    def __init__(self, *args, institution=None, **kwargs):
        self.institution = institution
        super().__init__(*args, **kwargs)
        self.fields["programme"].queryset = Programme.objects.all()
        self.fields["school_class"].queryset = SchoolClass.objects.all()
        self.fields["programme"].required = False
        self.fields["school_class"].required = False
        self.order_fields([
            "first_name", "last_name", "date_of_birth", "gender", "national_id",
            "email", "phone", "address", "guardian_name", "guardian_relationship",
            "guardian_email", "guardian_phone", "guardian_address", "programme",
            "school_class", "year_or_grade", "previous_school", "results_text",
            "identity_document", "results_document", "birth_certificate",
        ])
        settings_row = SchoolSettings.get(institution) if institution else None
        if settings_row is not None and settings_row.is_school:
            self.fields["school_class"].label = "Class applied for"
            self.fields["year_or_grade"].label = "Year or grade applied for"
        else:
            self.fields["programme"].label = "Programme applied for"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):
            raise ValidationError("This application could not be accepted.")
        if not (cleaned.get("programme") or cleaned.get("school_class")
                or (cleaned.get("year_or_grade") or "").strip()):
            raise ValidationError("Choose the programme, class or year you are applying for.")
        if cleaned.get("programme") and cleaned.get("school_class"):
            raise ValidationError(
                "Choose either a programme or a class, not both.")
        return cleaned

    def save(self, commit=True):
        application = super().save(commit=False)
        if self.institution is not None:
            application.institution = self.institution
        application.previous_results = self.cleaned_data.get("results_text") or ""
        if commit:
            application.save()
            self._save_results(application)
            self._save_documents(application)
            from .services import check_eligibility

            check_eligibility(application)
        return application

    def _save_results(self, application):
        rows = parse_results_text(self.cleaned_data.get("results_text"))
        for row in rows:
            ApplicantResult.objects.get_or_create(
                institution=application.institution,
                application=application,
                subject=row["subject"],
                defaults={"grade": row["grade"], "mark": row["mark"]})

    def _save_documents(self, application):
        uploads = [
            ("identity_document", ApplicantDocument.Kind.ID),
            ("results_document", ApplicantDocument.Kind.RESULTS),
            ("birth_certificate", ApplicantDocument.Kind.BIRTH_CERTIFICATE),
        ]
        for field, kind in uploads:
            upload = self.cleaned_data.get(field)
            if upload:
                ApplicantDocument.objects.create(
                    institution=application.institution, application=application,
                    kind=kind, file=upload)


class ReviewForm(forms.Form):
    status = forms.ChoiceField(choices=[])
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [
            (Application.Status.UNDER_REVIEW, "Under review"),
            (Application.Status.ACCEPTED, "Accept"),
            (Application.Status.WAITLISTED, "Waitlist"),
            (Application.Status.REJECTED, "Reject"),
            (Application.Status.WITHDRAWN, "Withdraw"),
        ]


class InterviewForm(forms.Form):
    interview_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"},
                                   format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"])
    location = forms.CharField(max_length=160, required=False)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class OfferForm(forms.Form):
    acceptance_deadline = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}))
    deposit_amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0"), initial=Decimal("0"))


class AcceptOfferForm(forms.Form):
    accepted_by_name = forms.CharField(
        max_length=120, label="Full name of the person accepting")
    deposit_method = forms.ChoiceField(
        choices=Offer.DepositMethod.choices, required=False,
        label="How is the deposit paid?")
    deposit_reference = forms.CharField(
        max_length=80, required=False, label="Payment reference")
    agree = forms.BooleanField(
        label="I accept the place and confirm the details are correct.")

    def __init__(self, *args, offer=None, **kwargs):
        self.offer = offer
        super().__init__(*args, **kwargs)
        if offer is not None and offer.deposit_amount <= 0:
            self.fields["deposit_method"].required = False
        elif offer is not None:
            self.fields["deposit_method"].required = True
        self.fields["deposit_method"].choices = [
            (Offer.DepositMethod.RECORDED, "Recorded at the office"),
            (Offer.DepositMethod.ONLINE, "Paid online"),
        ]

    def clean(self):
        cleaned = super().clean()
        if self.offer is not None and self.offer.deposit_amount > 0:
            if not cleaned.get("deposit_method"):
                self.add_error("deposit_method", "Choose how the deposit is paid.")
        return cleaned


class EntryRequirementForm(forms.ModelForm):
    class Meta:
        model = EntryRequirement
        fields = ["programme", "minimum_subjects", "minimum_grade",
                  "required_subjects", "minimum_average", "notes"]


class TransitionForm(forms.Form):
    to_state = forms.ChoiceField(choices=[])
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
    effective_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, student=None, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = [
            (LifecycleState.DEFERRED, "Defer"),
            (LifecycleState.SUSPENDED, "Suspend"),
            (LifecycleState.WITHDRAWN, "Withdraw"),
            (LifecycleState.ENROLLED, "Return to enrolled"),
        ]
        if student is not None and student.status == "graduated":
            allowed = [(LifecycleState.ALUMNI, "Move to alumni")]
        self.fields["to_state"].choices = allowed


class TransferForm(forms.Form):
    direction = forms.ChoiceField(choices=[
        ("out", "Transfer out to another institution"),
        ("in", "Transfer in from another institution"),
    ])
    other_institution = forms.CharField(max_length=200)
    reference = forms.CharField(max_length=80, required=False)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
    effective_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}))


class ClearanceForm(forms.Form):
    fees_cleared = forms.BooleanField(required=False)
    refresh_fees = forms.BooleanField(
        required=False, label="Check the fee balance automatically")
    library_books_returned = forms.BooleanField(required=False)
    hostel_keys_returned = forms.BooleanField(required=False)
    exams_passed = forms.BooleanField(required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, clearance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if clearance is not None:
            self.initial.update({
                "fees_cleared": clearance.fees_cleared,
                "library_books_returned": clearance.library_books_returned,
                "hostel_keys_returned": clearance.hostel_keys_returned,
                "exams_passed": clearance.exams_passed,
                "notes": clearance.notes,
            })


class CertificateForm(forms.ModelForm):
    class Meta:
        model = Certificate
        fields = ["kind", "title", "award", "graduation_date"]
        widgets = {"graduation_date": forms.DateInput(attrs={"type": "date"})}


class StudentCardForm(forms.ModelForm):
    class Meta:
        model = StudentCard
        fields = ["valid_until", "is_active", "photo"]
        widgets = {"valid_until": forms.DateInput(attrs={"type": "date"})}


class AlumniForm(forms.ModelForm):
    class Meta:
        model = AlumniRecord
        fields = ["contact_email", "contact_phone", "address", "occupation",
                  "employer", "graduation_year", "notes", "share_updates"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}),
                   "notes": forms.Textarea(attrs={"rows": 2})}


class DocumentUploadForm(forms.Form):
    kind = forms.ChoiceField(choices=ApplicantDocument.Kind.choices)
    file = forms.FileField()
