from django.conf import settings
from django.db import models, transaction

from core.tenancy import CurrentInstitutionId, current_institution_id, default_institution_id


class TenantQuerySet(models.QuerySet):
    def for_institution(self, institution):
        if institution is None:
            return self.none()
        pk = institution if isinstance(institution, int) else institution.pk
        return self.filter(institution_id=pk)

    def for_request(self, request):
        institution = getattr(request, "institution", None)
        if institution is None:
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated:
                institution = getattr(user, "institution", None)
        if institution is None:
            return self.none()
        return self.for_institution(institution)


class TenantManager(models.Manager):
    """Only ever returns rows belonging to the current institution."""

    def get_queryset(self):
        # The institution is read when the query runs, not when the queryset
        # is built, so a queryset captured at import time still reads the
        # institution of the request that uses it.
        return TenantQuerySet(self.model, using=self._db).filter(
            institution_id=CurrentInstitutionId()
        )

    def for_institution(self, institution):
        return TenantQuerySet(self.model, using=self._db).for_institution(institution)

    def for_request(self, request):
        return TenantQuerySet(self.model, using=self._db).for_request(request)


class TenantModel(models.Model):
    institution = models.ForeignKey(
        "core.Institution", on_delete=models.CASCADE, related_name="%(class)s_records"
    )

    # `objects` is the tenant-scoped manager and stays the default one.
    # `all_objects` reaches every institution and is for platform work only.
    objects = TenantManager()
    all_objects = models.Manager()  # noqa: DJ012

    class Meta:
        abstract = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.institution_id is None:
            self.institution_id = current_institution_id() or default_institution_id()

    def save(self, *args, **kwargs):
        if self.institution_id is None:
            self.institution_id = current_institution_id() or default_institution_id()
        if self.institution_id is None:
            raise ValueError(
                f"{self.__class__.__name__} needs an institution before it can be saved."
            )
        super().save(*args, **kwargs)


class Institution(models.Model):
    class Type(models.TextChoices):
        PRIMARY = "primary", "Primary school"
        SECONDARY = "secondary", "Secondary school"
        COLLEGE = "college", "College / university"
        VOCATIONAL = "vocational", "Vocational institute"

    class VatStatus(models.TextChoices):
        EXEMPT = "exempt", "Not registered (exempt)"
        REGISTERED = "registered", "VAT registered"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.COLLEGE)
    tagline = models.CharField(
        max_length=200,
        blank=True,
        help_text="A short line shown under the name on the public institution page.",
    )
    about = models.TextField(
        max_length=600,
        blank=True,
        help_text="A short description shown on the public institution page (600 characters).",
    )
    logo = models.ImageField(upload_to="institutions/logos/", blank=True)
    primary_color = models.CharField(max_length=20, blank=True)
    accent_color = models.CharField(max_length=20, blank=True)
    currency = models.CharField(max_length=10, default="$")
    # The reporting currency every other currency is converted into.
    base_currency = models.CharField(max_length=10, default="USD")
    # ZIMRA registration. A school that is not registered never charges VAT.
    vat_status = models.CharField(max_length=20, choices=VatStatus.choices,
                                  default=VatStatus.EXEMPT)
    vat_number = models.CharField(max_length=40, blank=True)
    bp_number = models.CharField(max_length=40, blank=True,
                                 help_text="ZIMRA Business Partner (BP) number.")
    vat_address = models.TextField(blank=True,
                                   help_text="The address shown on tax documents.")
    timezone = models.CharField(max_length=64, default="Africa/Harare")
    language = models.CharField(max_length=10, default="en")
    address = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    website = models.URLField(max_length=200, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    public_registration = models.BooleanField(
        default=False,
        help_text="Allow students and staff to register from the public site.",
    )
    public_page_enabled = models.BooleanField(
        default=True,
        help_text="Show this institution's public page at /i/<slug>/.",
    )
    show_register_button = models.BooleanField(
        default=True,
        help_text=(
            "Show a Register button on the public page when registration is open."
        ),
    )
    staff_positions = models.JSONField(
        default=list,
        blank=True,
        help_text="Staff positions this institution accepts for public registration.",
    )
    is_active = models.BooleanField(default=True)
    # Lifecycle. A suspended institution keeps its data but nobody can sign in.
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_reason = models.TextField(blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_reason = models.TextField(blank=True)
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the institution was deleted. The row is kept so the "
                  "audit trail and data processing records stay intact.",
    )
    # Consent to the data processing statement.
    data_processing_accepted_at = models.DateTimeField(null=True, blank=True)
    data_processing_version = models.CharField(max_length=20, blank=True)
    # The people who may approve health and counselling access requests.
    data_protection_officer = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dpo_institutions",
        help_text="Approves the most sensitive consent requests.",
    )
    principal = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="principal_institutions",
        help_text="Approves the most sensitive consent requests.",
    )
    access_grant_max_hours = models.PositiveSmallIntegerField(
        default=24,
        help_text="Longest consent grant this institution will allow (hard cap 168).",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def terminology(self):
        from core.terminology import terminology_for

        return terminology_for(self)

    def initials(self):
        """Up to two initials, used when the institution has no logo."""
        parts = [part for part in self.name.split() if part]
        if not parts:
            return "?"
        letters = "".join(part[0] for part in parts[:2])
        return letters.upper()

    def enabled_modules(self):
        from core.modules import enabled_modules

        return enabled_modules(self)

    def allowed_staff_positions(self):
        """The staff positions this institution accepts for registration."""
        from accounts.registration import allowed_staff_positions

        return allowed_staff_positions(self)


class InstitutionModule(models.Model):
    """One switch per module per institution."""

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="modules")
    code = models.CharField(max_length=40)
    enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = ("institution", "code")
        ordering = ["code"]

    def __str__(self):
        return f"{self.institution.slug}:{self.code}={'on' if self.enabled else 'off'}"


class Terminology(models.Model):
    """The words this institution uses: Class vs Programme, Pupil vs Student."""

    institution = models.OneToOneField(
        Institution, on_delete=models.CASCADE, related_name="terminology_record"
    )
    class_label = models.CharField(max_length=30, default="Class")
    grade_label = models.CharField(max_length=30, default="Grade")
    pupil_label = models.CharField(max_length=30, default="Student")
    teacher_label = models.CharField(max_length=30, default="Teacher")
    programme_label = models.CharField(max_length=30, default="Programme")

    def __str__(self):
        return f"Terminology for {self.institution}"


class Role(TenantModel):
    """A job role inside one institution, with its own permission set."""

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=80)
    code = models.SlugField(max_length=60)
    is_system = models.BooleanField(default=False)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = ("institution", "code")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.institution.name})"


class Permission(models.Model):
    """A permission code such as ``finance.receive_payment``."""

    code = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=200, blank=True)
    category = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["category", "code"]

    def __str__(self):
        return self.code


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="role_permissions")

    class Meta:
        unique_together = ("role", "permission")

    def __str__(self):
        return f"{self.role.code}:{self.permission.code}"


class Department(TenantModel):
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="child_departments"
    )
    head = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="departments_headed",
    )

    class Meta:
        unique_together = ("institution", "code")
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class StaffProfile(TenantModel):
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="staff")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_profile")
    employee_no = models.CharField(max_length=30, blank=True)
    departments = models.ManyToManyField(Department, blank=True, related_name="staff")
    job_title = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    date_joined = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["user__last_name", "user__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "employee_no"],
                condition=~models.Q(employee_no=""),
                name="uniq_staff_employee_no"),
        ]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - {self.job_title}"

    def save(self, *a, **kw):
        with transaction.atomic():
            super().save(*a, **kw)
            if not self.employee_no:
                from core.numbering import next_number

                # Callers that know the department before the row exists (the
                # user and import screens do) hand its code in, because a
                # many-to-many relation cannot be read before the first save.
                department_code = getattr(self, "_department_code", "")
                if not department_code:
                    department = self.departments.first()
                    department_code = getattr(department, "code", "")
                self.employee_no = next_number(
                    self.institution, "staff", department_code=department_code)
                super().save(update_fields=["employee_no"])
            elif self.employee_no:
                normalised = str(self.employee_no).strip().upper()
                if normalised != self.employee_no:
                    self.employee_no = normalised
                    super().save(update_fields=["employee_no"])


class NumberFormat(TenantModel):
    """How one kind of number is built for one institution.

    The format is a set of tokens, not a template string: a fixed letter
    prefix, an optional two- or four-digit year, an optional programme or
    department code, a zero-padded sequence, one separator, and an optional
    final check letter. Keeping the pieces separate makes validation,
    live previews and a documented check-letter calculation possible.
    """

    class Kind(models.TextChoices):
        STUDENT = "student", "Student number"
        STAFF = "staff", "Staff number"
        APPLICANT = "applicant", "Applicant number"

    class Separator(models.TextChoices):
        NONE = "", "None"
        HYPHEN = "-", "Hyphen"
        SLASH = "/", "Slash"

    class CheckMode(models.TextChoices):
        NONE = "none", "No check letter"
        FIXED = "fixed", "Fixed letter"
        CALCULATED = "calculated", "Calculated check letter"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    prefix = models.CharField(max_length=12, blank=True)
    include_year = models.BooleanField(default=True)
    year_digits = models.PositiveSmallIntegerField(default=4)
    include_programme = models.BooleanField(
        default=False,
        help_text="Add the student's programme code to student numbers.")
    include_department = models.BooleanField(
        default=False,
        help_text="Add the staff member's department code to staff numbers.")
    padding = models.PositiveSmallIntegerField(default=4)
    separator = models.CharField(max_length=1, choices=Separator.choices,
                                 blank=True, default=Separator.HYPHEN)
    check_mode = models.CharField(max_length=12, choices=CheckMode.choices,
                                  default=CheckMode.NONE)
    check_letter = models.CharField(max_length=1, blank=True)
    reset_yearly = models.BooleanField(default=True)
    start_number = models.PositiveIntegerField(default=1)
    updated = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="number_formats_changed")

    class Meta:
        ordering = ["kind"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "kind"],
                                    name="uniq_number_format")
        ]

    def __str__(self):
        return f"{self.get_kind_display()} for {self.institution}"


class NumberFormatChange(TenantModel):
    """An append-only history of number-format changes."""

    format = models.ForeignKey(
        NumberFormat, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="changes")
    kind = models.CharField(max_length=20, choices=NumberFormat.Kind.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="number_format_changes")
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=200, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"{self.kind} format changed"


class NumberSequence(TenantModel):
    """The last number issued for one kind and one year bucket.

    A yearly format has one row per year, so a new year starts again without
    touching the old rows. A format that continues forever uses year ``0``.
    The number is advanced with an atomic database update inside a row lock,
    so two clerks admitting at the same moment never share a number. A number
    is never put back, so deleting a record cannot cause a reused number.
    """

    class Kind(models.TextChoices):
        STUDENT = "student", "Student number"
        STAFF = "staff", "Staff number"
        APPLICANT = "applicant", "Applicant number"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    year = models.PositiveIntegerField(default=0)
    last_number = models.PositiveIntegerField(default=0)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "year"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "kind", "year"],
                                    name="uniq_number_sequence")
        ]

    def __str__(self):
        return f"{self.kind} {self.year or 'all'}: {self.last_number}"


# ---------- bulk import ----------

class ImportBatch(TenantModel):
    """One uploaded spreadsheet and what happened to it.

    The file is read into rows and kept on the batch, so the user can map the
    columns, look at the problems and come back to the same file later. The
    rows never leave the institution that uploaded them.
    """

    class Kind(models.TextChoices):
        STUDENTS = "students", "Students"
        STAFF = "staff", "Staff"
        COURSES = "courses", "Courses"
        SUBJECTS = "subjects", "Subjects"
        FEE_STRUCTURES = "fee_structures", "Fee structures"

    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        MAPPED = "mapped", "Columns mapped"
        IMPORTED = "imported", "Imported"
        FAILED = "failed", "Nothing imported"

    class AccessMode(models.TextChoices):
        INVITATION = "invitation", "Send invitation email"
        TEMPORARY_PASSWORD = "temporary_password", "Temporary password"

    institution = models.ForeignKey(
        Institution, on_delete=models.CASCADE, related_name="import_batches")
    kind = models.CharField(max_length=30, choices=Kind.choices)
    filename = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UPLOADED)
    mapping = models.JSONField(default=dict, blank=True)
    rows = models.JSONField(default=list, blank=True)
    imported_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="import_batches")
    access_mode = models.CharField(
        max_length=30, choices=AccessMode.choices, default=AccessMode.INVITATION)
    # The accounts written by this batch. The ids let an administrator undo a
    # batch that nobody has signed in to yet.
    created_user_ids = models.JSONField(default=list, blank=True)
    # One-time credentials for the results file. They are cleared as soon as
    # the file is downloaded, so a password is never kept after its first view.
    credentials = models.JSONField(default=list, blank=True)
    credentials_downloaded = models.BooleanField(default=False)
    undone_at = models.DateTimeField(null=True, blank=True)
    undone_count = models.PositiveIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    finished = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created", "-pk"]
        verbose_name_plural = "import batches"

    def __str__(self):
        return f"{self.get_kind_display()} from {self.filename}"

    @property
    def headers(self):
        """The column names of the uploaded file."""
        rows = self.rows or []
        return list(rows[0].keys()) if rows else []

    @property
    def data_rows(self):
        """The rows without the header row."""
        return list((self.rows or [])[1:])


# ---------- workflow ----------

class WorkflowTemplate(TenantModel):
    """An approval route: an ordered list of steps, each with an approver role."""

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="workflow_templates")
    name = models.CharField(max_length=120)
    code = models.SlugField(max_length=60)
    description = models.CharField(max_length=200, blank=True)
    applies_to = models.CharField(max_length=60, blank=True,
                                  help_text="What this workflow is for, for example 'leave' or 'refund'.")
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("institution", "code")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.institution.name})"


class WorkflowStep(models.Model):
    template = models.ForeignKey(WorkflowTemplate, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveSmallIntegerField(default=1)
    name = models.CharField(max_length=120, blank=True)
    approver_role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="workflow_steps")
    required_permission = models.CharField(max_length=80, blank=True,
                                           help_text="Permission code that also allows the step.")

    class Meta:
        ordering = ["order", "pk"]
        unique_together = ("template", "order")

    def __str__(self):
        return f"{self.template.code} step {self.order} ({self.approver_role.code})"


class ApprovalRequest(TenantModel):
    """One item waiting for approval."""

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        RETURNED = "returned", "Returned for changes"
        CANCELLED = "cancelled", "Cancelled"

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="approval_requests")
    template = models.ForeignKey(WorkflowTemplate, on_delete=models.PROTECT, related_name="requests")
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="approval_requests")
    subject_type = models.CharField(max_length=60)
    subject_id = models.CharField(max_length=40)
    subject_label = models.CharField(max_length=200, blank=True)
    reason = models.TextField(blank=True)
    current_step = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [models.Index(fields=["institution", "status"])]

    def __str__(self):
        return f"{self.subject_type} #{self.subject_id} - {self.get_status_display()}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("approval_detail", kwargs={"pk": self.pk})

    @property
    def is_open(self):
        return self.status == self.Status.PENDING

    def current_step_object(self):
        return self.template.steps.filter(order=self.current_step).first()


class ApprovalAction(models.Model):
    """Every decision somebody made on a request."""

    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        RETURNED = "returned", "Returned for changes"
        SUBMITTED = "submitted", "Submitted"
        CANCELLED = "cancelled", "Cancelled"

    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="actions")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="approval_actions")
    decision = models.CharField(max_length=20, choices=Decision.choices)
    comment = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created", "pk"]

    def __str__(self):
        return f"{self.get_decision_display()} by {self.actor}"


# ---------- notifications ----------

class MessageTemplate(TenantModel):
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="message_templates")
    code = models.CharField(max_length=60)
    channel = models.CharField(
        max_length=20,
        choices=[("email", "Email"), ("sms", "SMS"), ("whatsapp", "WhatsApp"), ("inapp", "In-app")],
        default="inapp",
    )
    subject = models.CharField(max_length=150, blank=True)
    body = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("institution", "code", "channel")
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} ({self.channel})"


class Notification(TenantModel):
    """A message waiting for one user in the notification bell."""

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="notifications")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=150)
    body = models.TextField(blank=True)
    link = models.CharField(max_length=200, blank=True)
    category = models.CharField(max_length=40, default="general")
    read_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [models.Index(fields=["user", "read_at"])]

    def __str__(self):
        return f"{self.title} for {self.user}"

    @property
    def is_read(self):
        return self.read_at is not None


class OutboundMessage(TenantModel):
    """A message sent (or queued to be sent) to a phone number or email."""

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="outbound_messages")
    channel = models.CharField(max_length=20, choices=Channel.choices)
    recipient = models.CharField(max_length=120)
    subject = models.CharField(max_length=150, blank=True)
    body = models.TextField()
    html_body = models.TextField(
        blank=True,
        help_text="Optional rich version of the body, sent alongside the plain text.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    provider = models.CharField(max_length=40, blank=True)
    provider_response = models.TextField(blank=True)
    provider_message_id = models.CharField(max_length=255, blank=True, db_index=True)
    retries = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    related_object_type = models.CharField(max_length=60, blank=True)
    related_object_id = models.CharField(max_length=40, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"{self.channel} to {self.recipient}: {self.status}"


class EmailSuppression(models.Model):
    """An address the platform must not email again (hard bounce/complaint).

    Suppression is platform-wide: a bad address is bad for every institution.
    """

    class Reason(models.TextChoices):
        BOUNCED = "bounced", "Hard bounce"
        COMPLAINED = "complained", "Complaint"

    email = models.EmailField(unique=True)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.email} ({self.reason})"


class EmailEvent(TenantModel):
    """A delivery event received from the Resend webhook."""

    class Kind(models.TextChoices):
        DELIVERED = "delivered", "Delivered"
        BOUNCED = "bounced", "Bounced"
        COMPLAINED = "complained", "Complained"

    message = models.ForeignKey(
        OutboundMessage, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="events",
    )
    recipient = models.EmailField()
    kind = models.CharField(max_length=20, choices=Kind.choices, db_index=True)
    provider_event_id = models.CharField(max_length=255, blank=True)
    detail = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"{self.kind} to {self.recipient}"


# ---------- audit ----------

class AuditEntry(TenantModel):
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="audit_entries")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="audit_entries")
    action = models.CharField(max_length=80, db_index=True)
    model_name = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [models.Index(fields=["institution", "-created"])]

    def __str__(self):
        return f"{self.created:%Y-%m-%d %H:%M} {self.action}"

    @property
    def actor_name(self):
        if not self.actor_id:
            return "system"
        return self.actor.get_full_name() or self.actor.username


class SensitiveViewLog(TenantModel):
    """A record of somebody opening a restricted record (health, counselling)."""

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="sensitive_views")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="sensitive_views")
    record_type = models.CharField(max_length=60, db_index=True)
    record_id = models.CharField(max_length=40, blank=True)
    view_name = models.CharField(max_length=120, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"{self.created:%Y-%m-%d %H:%M} viewed {self.record_type}"
