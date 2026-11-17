"""Admissions, offers and the student lifecycle.

An application is not a student yet. It is a public enquiry that moves through
review until an offer is accepted. Only the acceptance service creates the
student, login, invoice and welcome message, and it does so in one transaction.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from core.models import TenantModel
from core.uploads import (
    document_upload_validator,
    image_upload_validator,
    shrink_image_field,
)


def application_upload_to(instance, filename):
    token = getattr(instance.application, "tracking_token", "unknown")
    return f"admissions/{instance.application.institution_id}/{token}/{filename}"


class Application(TenantModel):
    """One public application to one institution."""

    class Status(models.TextChoices):
        APPLIED = "applied", "Applied"
        UNDER_REVIEW = "under_review", "Under review"
        INTERVIEW = "interview_scheduled", "Interview or test scheduled"
        ACCEPTED = "accepted", "Accepted"
        WAITLISTED = "waitlisted", "Waitlisted"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        ENROLLED = "enrolled", "Enrolled"

    class Gender(models.TextChoices):
        FEMALE = "female", "Female"
        MALE = "male", "Male"
        OTHER = "other", "Other"
        UNSPECIFIED = "", "Prefer not to say"

    reference = models.CharField(max_length=24, blank=True)
    tracking_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, choices=Gender.choices, blank=True)
    national_id = models.CharField(max_length=40, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)

    guardian_name = models.CharField(max_length=120, blank=True)
    guardian_relationship = models.CharField(max_length=60, blank=True)
    guardian_email = models.EmailField(blank=True)
    guardian_phone = models.CharField(max_length=30, blank=True)
    guardian_address = models.TextField(blank=True)

    programme = models.ForeignKey("academics.Programme", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="applications")
    programme_year = models.ForeignKey("academics.ProgrammeYear", null=True, blank=True,
                                       on_delete=models.SET_NULL,
                                       related_name="applications")
    cohort = models.ForeignKey("academics.Cohort", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="applications")
    school_class = models.ForeignKey("academics.SchoolClass", null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="applications")
    year_or_grade = models.CharField("Year / grade applied for", max_length=30, blank=True)

    previous_school = models.CharField(max_length=160, blank=True)
    previous_results = models.TextField(
        blank=True, help_text="One subject per line, for example Mathematics: B.")

    status = models.CharField(max_length=30, choices=Status.choices,
                              default=Status.APPLIED)
    eligible = models.BooleanField(null=True, blank=True)
    eligibility_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name="applications_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    interview_at = models.DateTimeField(null=True, blank=True)
    interview_location = models.CharField(max_length=160, blank=True)
    interview_note = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-submitted_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "reference"],
                                    name="uniq_application_reference"),
        ]
        indexes = [
            models.Index(fields=["institution", "status"], name="app_inst_status_idx"),
            models.Index(fields=["institution", "-submitted_at"], name="app_inst_submitted_idx"),
            models.Index(fields=["email"], name="app_email_idx"),
        ]

    def save(self, *a, **kw):
        adding = self._state.adding
        with transaction.atomic():
            if adding and not self.reference:
                from core.numbering import next_number

                self.reference = next_number(self.institution, "applicant")
            elif self.reference:
                self.reference = str(self.reference).strip().upper()
            super().save(*a, **kw)

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def full_name(self):
        return self.name

    @property
    def applied_for(self):
        if self.school_class_id:
            return str(self.school_class)
        if self.programme_id:
            return str(self.programme)
        return self.year_or_grade or "—"

    def __str__(self):
        return f"{self.reference} {self.name}"


class ApplicantDocument(TenantModel):
    """An uploaded identity document, result slip or birth certificate."""

    class Kind(models.TextChoices):
        ID = "id", "Identity document"
        RESULTS = "results", "Examination results"
        BIRTH_CERTIFICATE = "birth_certificate", "Birth certificate"
        OTHER = "other", "Other document"

    application = models.ForeignKey(Application, on_delete=models.CASCADE,
                                    related_name="documents")
    kind = models.CharField(max_length=30, choices=Kind.choices)
    file = models.FileField(
        upload_to=application_upload_to, validators=[document_upload_validator])
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "uploaded_at"]

    def __str__(self):
        return f"{self.get_kind_display()} for {self.application.reference}"


class ApplicantResult(TenantModel):
    """One subject result the applicant reported, used for eligibility."""

    application = models.ForeignKey(Application, on_delete=models.CASCADE,
                                    related_name="results")
    subject = models.CharField(max_length=120)
    grade = models.CharField(max_length=5, blank=True)
    mark = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["subject"]
        constraints = [
            models.UniqueConstraint(fields=["application", "subject"],
                                    name="uniq_applicant_result_subject"),
        ]

    def __str__(self):
        return f"{self.subject}: {self.grade or self.mark}"


class EntryRequirement(TenantModel):
    """The minimum subjects and grades needed for a programme."""

    programme = models.OneToOneField("academics.Programme", on_delete=models.CASCADE,
                                     related_name="entry_requirement")
    minimum_subjects = models.PositiveSmallIntegerField(
        default=0, help_text="How many passes are needed. 0 means no minimum.")
    minimum_grade = models.CharField(
        max_length=5, default="E",
        help_text="The lowest grade that counts as a pass (for example C or E).")
    required_subjects = models.CharField(
        max_length=400, blank=True,
        help_text="Comma-separated subjects that must be passed, for example Mathematics, English.")
    minimum_average = models.DecimalField(max_digits=5, decimal_places=2, null=True,
                                          blank=True,
                                          help_text="Optional average mark for numeric results.")
    notes = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["programme__name"]

    @property
    def required_subject_names(self):
        return [part.strip() for part in self.required_subjects.split(",") if part.strip()]

    def __str__(self):
        return f"Entry requirements for {self.programme}"


class Offer(TenantModel):
    """An offer letter waiting for the applicant's acceptance and deposit."""

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for acceptance"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"

    class DepositMethod(models.TextChoices):
        NONE = "", "No deposit"
        RECORDED = "recorded", "Recorded at the office"
        ONLINE = "online", "Paid online"

    application = models.OneToOneField(Application, on_delete=models.CASCADE,
                                       related_name="offer")
    offer_no = models.CharField(max_length=24, blank=True)
    acceptance_deadline = models.DateField()
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2,
                                         default=Decimal("0"))
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL,
                                  related_name="offers_issued")
    issued_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.PENDING)
    accepted_by_name = models.CharField(max_length=120, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    deposit_method = models.CharField(max_length=20, choices=DepositMethod.choices,
                                      blank=True)
    deposit_reference = models.CharField(max_length=80, blank=True)
    deposit_paid_at = models.DateTimeField(null=True, blank=True)
    student = models.OneToOneField("academics.Student", null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="admission_offer")

    class Meta:
        ordering = ["-issued_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "offer_no"],
                                    name="uniq_offer_number"),
        ]

    def save(self, *a, **kw):
        adding = self._state.adding
        if adding and not self.offer_no:
            self.offer_no = f"TMP{uuid.uuid4().hex[:12].upper()}"
        super().save(*a, **kw)
        if adding and self.offer_no.startswith("TMP"):
            stamp = (self.issued_at or timezone.now()).strftime("%y")
            self.offer_no = f"OFF{stamp}{self.pk:05d}"
            super().save(update_fields=["offer_no"])

    @property
    def is_expired(self):
        return self.status == self.Status.PENDING and \
            self.acceptance_deadline < timezone.localdate()

    @property
    def deposit_paid(self):
        return self.deposit_paid_at is not None

    def __str__(self):
        return f"{self.offer_no} for {self.application.name}"


class LifecycleState(models.TextChoices):
    APPLICANT = "applicant", "Applicant"
    ENROLLED = "enrolled", "Enrolled"
    DEFERRED = "deferred", "Deferred"
    SUSPENDED = "suspended", "Suspended"
    TRANSFERRED_OUT = "transferred_out", "Transferred out"
    WITHDRAWN = "withdrawn", "Withdrawn"
    GRADUATED = "graduated", "Graduated"
    ALUMNI = "alumni", "Alumni"


class LifecycleTransition(TenantModel):
    """One move from one lifecycle state to another, with its written reason."""

    student = models.ForeignKey("academics.Student", on_delete=models.CASCADE,
                                related_name="lifecycle_transitions")
    from_state = models.CharField(max_length=30, choices=LifecycleState.choices)
    to_state = models.CharField(max_length=30, choices=LifecycleState.choices)
    reason = models.TextField()
    effective_date = models.DateField(default=timezone.localdate)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name="lifecycle_transitions")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def clean(self):
        super().clean()
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValidationError({"reason": "Give a reason for this change."})

    def save(self, *a, **kw):
        self.full_clean()
        super().save(*a, **kw)

    def __str__(self):
        return f"{self.student} {self.from_state} -> {self.to_state}"


class TransferRecord(TenantModel):
    """A student moving in from, or out to, another institution."""

    class Direction(models.TextChoices):
        IN = "in", "Transfer in"
        OUT = "out", "Transfer out"

    student = models.ForeignKey("academics.Student", on_delete=models.CASCADE,
                                related_name="transfers")
    direction = models.CharField(max_length=10, choices=Direction.choices)
    other_institution = models.CharField(max_length=200)
    reference = models.CharField(max_length=80, blank=True)
    reason = models.TextField()
    effective_date = models.DateField(default=timezone.localdate)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="transfers_recorded")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_date", "-pk"]

    def clean(self):
        super().clean()
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValidationError({"reason": "Give a reason for the transfer."})

    def save(self, *a, **kw):
        self.full_clean()
        super().save(*a, **kw)

    def __str__(self):
        return f"{self.get_direction_display()} {self.student} ({self.other_institution})"


class GraduationClearance(TenantModel):
    """The checklist a student must pass before the registrar may graduate them."""

    student = models.OneToOneField("academics.Student", on_delete=models.CASCADE,
                                   related_name="graduation_clearance")
    fees_cleared = models.BooleanField(default=False)
    library_books_returned = models.BooleanField(default=False)
    hostel_keys_returned = models.BooleanField(default=False)
    exams_passed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="clearances_updated")
    updated_at = models.DateTimeField(auto_now=True)
    cleared_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="clearances_cleared")
    cleared_at = models.DateTimeField(null=True, blank=True)
    graduated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name="students_graduated")
    graduated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "graduation clearance"
        verbose_name_plural = "graduation clearances"

    @property
    def items(self):
        return [
            ("fees_cleared", "Fees cleared", self.fees_cleared),
            ("library_books_returned", "Library books returned",
             self.library_books_returned),
            ("hostel_keys_returned", "Hostel keys returned", self.hostel_keys_returned),
            ("exams_passed", "Examinations passed", self.exams_passed),
        ]

    @property
    def is_clear(self):
        return all(value for _key, _label, value in self.items)

    @property
    def outstanding(self):
        return [label for _key, label, value in self.items if not value]

    def __str__(self):
        return f"Clearance for {self.student}"


class Certificate(TenantModel):
    """Award certificate with a serial number and public QR verification."""

    class Kind(models.TextChoices):
        ACADEMIC = "academic", "Academic award"
        SHORT_COURSE = "short_course", "Short course certificate"
        TRANSCRIPT = "transcript", "Certificate transcript"

    student = models.ForeignKey("academics.Student", on_delete=models.CASCADE,
                                related_name="certificates")
    kind = models.CharField(max_length=20, choices=Kind.choices,
                            default=Kind.ACADEMIC)
    title = models.CharField(max_length=160, default="Certificate of Award")
    award = models.CharField(max_length=160)
    serial_no = models.CharField(max_length=24, blank=True)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    graduation_date = models.DateField(default=timezone.localdate)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL,
                                  related_name="certificates_issued")
    issued_at = models.DateTimeField(auto_now_add=True)
    is_revoked = models.BooleanField(default=False)
    revoked_reason = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-issued_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "serial_no"],
                                    name="uniq_certificate_serial"),
        ]

    def save(self, *a, **kw):
        adding = self._state.adding
        if adding and not self.serial_no:
            self.serial_no = f"TMP{uuid.uuid4().hex[:12].upper()}"
        super().save(*a, **kw)
        if adding and self.serial_no.startswith("TMP"):
            stamp = (self.issued_at or timezone.now()).strftime("%y")
            self.serial_no = f"CERT{stamp}{self.pk:06d}"
            super().save(update_fields=["serial_no"])

    def __str__(self):
        return f"{self.serial_no} {self.award} for {self.student}"


class StudentCard(TenantModel):
    """A printable student identity card with a photo, QR and validity date."""

    student = models.OneToOneField("academics.Student", on_delete=models.CASCADE,
                                   related_name="id_card")
    card_no = models.CharField(max_length=24, blank=True)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    photo = models.ImageField(
        upload_to="students/cards/", blank=True,
        validators=[image_upload_validator],
        help_text="PNG, JPG or WebP up to 5 MB.",
    )
    issued_on = models.DateField(default=timezone.localdate)
    valid_until = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["student__user__last_name", "student__user__first_name"]

    def save(self, *a, **kw):
        adding = self._state.adding
        shrink_image_field(self, "photo")
        if adding and not self.card_no:
            self.card_no = f"TMP{uuid.uuid4().hex[:12].upper()}"
        super().save(*a, **kw)
        if adding and self.card_no.startswith("TMP"):
            stamp = (self.issued_on or timezone.localdate()).strftime("%y")
            self.card_no = f"ID{stamp}{self.pk:06d}"
            super().save(update_fields=["card_no"])

    @property
    def is_valid(self):
        return self.is_active and self.valid_until >= timezone.localdate()

    def __str__(self):
        return f"{self.card_no} for {self.student}"


class AlumniRecord(TenantModel):
    """The contact file the institution keeps after a student graduates."""

    student = models.OneToOneField("academics.Student", on_delete=models.CASCADE,
                                   related_name="alumni_record")
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    occupation = models.CharField(max_length=120, blank=True)
    employer = models.CharField(max_length=160, blank=True)
    graduation_year = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    share_updates = models.BooleanField(
        default=True, help_text="The alumnus agrees to receive occasional updates.")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="alumni_updated")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["student__user__last_name", "student__user__first_name"]

    def __str__(self):
        return f"Alumni record for {self.student}"
