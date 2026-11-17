"""Platform-level models: plans, applications, consent and audit.

None of these rows belongs to a single institution. A student record is owned
by a school and filtered by :class:`core.models.TenantManager`; a plan, an
institution application or an access grant belongs to the platform itself and
is read with ordinary managers.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone

from core.models import Institution
from core.uploads import document_upload_validator


def application_document_path(instance, filename):
    token = instance.application.applicant_token
    suffix = (filename.rsplit(".", 1)[-1] if "." in filename else "bin").lower()[:8]
    return f"platform-applications/{token}/{uuid.uuid4().hex}.{suffix}"


class Plan(models.Model):
    """A commercial plan a new institution can be placed on."""

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    annual_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=10, default="USD")
    trial_days = models.PositiveSmallIntegerField(default=14)
    max_students = models.PositiveIntegerField(null=True, blank=True)
    max_staff = models.PositiveIntegerField(null=True, blank=True)
    modules = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    ordering = models.PositiveSmallIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ordering", "name"]

    def __str__(self):
        return self.name


class Subscription(models.Model):
    """The plan an institution is on and how its billing period is doing."""

    class Status(models.TextChoices):
        TRIALING = "trialing", "On trial"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        CANCELLED = "cancelled", "Cancelled"
        SUSPENDED = "suspended", "Suspended"

    class Cycle(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        ANNUAL = "annual", "Annual"

    institution = models.ForeignKey(
        Institution, on_delete=models.CASCADE, related_name="subscriptions"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TRIALING)
    billing_cycle = models.CharField(max_length=20, choices=Cycle.choices, default=Cycle.MONTHLY)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    seats = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [models.Index(fields=["institution", "status"])]

    def __str__(self):
        return f"{self.institution.name} - {self.plan.name} ({self.get_status_display()})"

    @property
    def is_current(self):
        return self.status in (self.Status.TRIALING, self.Status.ACTIVE, self.Status.PAST_DUE)


class PlatformInvoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    institution = models.ForeignKey(
        Institution, on_delete=models.PROTECT, related_name="platform_invoices"
    )
    subscription = models.ForeignKey(
        Subscription, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="invoices",
    )
    number = models.CharField(max_length=40, unique=True)
    description = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=10, default="USD")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    issued_on = models.DateField(null=True, blank=True)
    due_on = models.DateField(null=True, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"{self.number} - {self.institution.name}"

    @property
    def balance(self):
        paid = self.payments.aggregate(total=models.Sum("amount"))["total"] or Decimal("0")
        return self.amount - paid


class PlatformPayment(models.Model):
    class Method(models.TextChoices):
        BANK = "bank", "Bank transfer"
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        MOBILE = "mobile", "Mobile money"
        OTHER = "other", "Other"

    invoice = models.ForeignKey(
        PlatformInvoice, on_delete=models.PROTECT, related_name="payments"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.BANK)
    reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_payments_recorded",
    )
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-paid_at", "-pk"]

    def __str__(self):
        return f"{self.amount} for {self.invoice.number}"


class InstitutionApplication(models.Model):
    """A public request from a school or college that wants EduSphere."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        UNDER_REVIEW = "under_review", "Under review"
        MORE_INFORMATION_NEEDED = "more_information_needed", "More information needed"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    reference = models.CharField(max_length=30, unique=True, db_index=True)
    applicant_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.SUBMITTED
    )

    institution_name = models.CharField(max_length=200)
    institution_type = models.CharField(max_length=20, choices=Institution.Type.choices)
    country = models.CharField(max_length=100, default="Zimbabwe")
    town = models.CharField(max_length=100)
    registration_number = models.CharField(max_length=100, blank=True)

    expected_students = models.PositiveIntegerField(default=0)
    expected_staff = models.PositiveIntegerField(default=0)

    contact_person = models.CharField(max_length=150)
    position = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40)
    email = models.EmailField()
    website = models.URLField(max_length=200, blank=True)

    preferred_plan = models.ForeignKey(
        Plan, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="applications",
    )
    modules = models.JSONField(default=list, blank=True)

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_applications_assigned",
    )
    internal_notes = models.TextField(blank=True)
    decision_reason = models.TextField(blank=True)
    more_information_message = models.TextField(blank=True)

    agreed_to_terms = models.BooleanField(default=False)
    consent_statement_version = models.CharField(max_length=20, blank=True)
    consent_accepted_at = models.DateTimeField(null=True, blank=True)

    submitted_ip = models.GenericIPAddressField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_applications_decided",
    )
    created_institution = models.OneToOneField(
        Institution, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="source_application",
    )

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [
            models.Index(fields=["status", "-created"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def __str__(self):
        return f"{self.reference} - {self.institution_name}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("platform_admin:application_detail", args=[self.pk])

    @property
    def is_open(self):
        return self.status in (
            self.Status.SUBMITTED,
            self.Status.UNDER_REVIEW,
            self.Status.MORE_INFORMATION_NEEDED,
        )

    @property
    def can_reply(self):
        return self.status == self.Status.MORE_INFORMATION_NEEDED


class ApplicationDocument(models.Model):
    class Kind(models.TextChoices):
        REGISTRATION_CERTIFICATE = "registration_certificate", "Registration certificate"
        AUTHORISATION_LETTER = "authorisation_letter", "Authorisation letter"
        OTHER = "other", "Other document"

    application = models.ForeignKey(
        InstitutionApplication, on_delete=models.CASCADE, related_name="documents"
    )
    kind = models.CharField(max_length=40, choices=Kind.choices)
    file = models.FileField(
        upload_to=application_document_path,
        validators=[document_upload_validator],
    )
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at", "pk"]

    def __str__(self):
        return f"{self.get_kind_display()} for {self.application.reference}"


class ApplicationEvent(models.Model):
    """The activity history of one application."""

    application = models.ForeignKey(
        InstitutionApplication, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_application_events",
    )
    action = models.CharField(max_length=80)
    note = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created", "pk"]

    def __str__(self):
        return f"{self.action} on {self.application.reference}"


class ApplicationMessage(models.Model):
    class AuthorKind(models.TextChoices):
        APPLICANT = "applicant", "Applicant"
        STAFF = "staff", "EduSphere"

    application = models.ForeignKey(
        InstitutionApplication, on_delete=models.CASCADE, related_name="messages"
    )
    author_kind = models.CharField(max_length=20, choices=AuthorKind.choices)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_application_messages",
    )
    body = models.TextField()
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created", "pk"]

    def __str__(self):
        return f"{self.author_kind} message on {self.application.reference}"


# ---------- consent and protected access ----------

ACCESS_CATEGORIES = [
    ("personal_data", "Student and staff personal data",
     "Names, contacts, national IDs and photographs."),
    ("health", "Health records",
     "Clinic visits, medication and health notes. Highest protection."),
    ("counselling", "Counselling notes",
     "Counselling sessions and notes. Highest protection."),
    ("finance", "Fees, payments and invoices",
     "Invoices, receipts, balances and the institution's finance reports."),
    ("results", "Results and transcripts",
     "Marks, report cards, transcripts and result exports."),
    ("hr", "Discipline, HR, payroll and contracts",
     "Discipline files, staff records, payroll and contracts."),
    ("messages", "Messages and uploaded documents",
     "Messages and documents uploaded inside the institution."),
    ("changes", "Make changes inside the institution",
     "Edit users, records or settings. Needs read-and-change access."),
]
ACCESS_CATEGORY_CODES = [code for code, _label, _help in ACCESS_CATEGORIES]
ACCESS_CATEGORY_LABELS = {code: label for code, label, _help in ACCESS_CATEGORIES}
HIGH_PROTECTION_CATEGORIES = {"health", "counselling"}


class AccessRequest(models.Model):
    """A platform admin asking an institution for consent to its data."""

    class Mode(models.TextChoices):
        READ = "read", "Read only"
        READ_WRITE = "read_write", "Read and change"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for the institution"
        APPROVED = "approved", "Approved"
        DENIED = "denied", "Denied"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"
        CANCELLED = "cancelled", "Cancelled"

    institution = models.ForeignKey(
        Institution, on_delete=models.CASCADE, related_name="platform_access_requests"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="platform_access_requests_made",
    )
    categories = models.JSONField(default=list, blank=True)
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.READ)
    include_export = models.BooleanField(default=False)
    reason = models.TextField()
    reference = models.CharField(max_length=120, blank=True)
    duration_hours = models.PositiveSmallIntegerField(default=24)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_access_requests_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)

    approved_categories = models.JSONField(default=list, blank=True)
    approved_mode = models.CharField(
        max_length=20, choices=Mode.choices, blank=True
    )
    approved_include_export = models.BooleanField(default=False)
    approved_duration_hours = models.PositiveSmallIntegerField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    is_emergency = models.BooleanField(default=False)
    emergency_justification = models.TextField(blank=True)
    second_approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_access_requests_seconded",
    )
    second_approved_at = models.DateTimeField(null=True, blank=True)
    flagged_for_review = models.BooleanField(default=False)

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["requested_by", "status"]),
        ]

    def __str__(self):
        return (
            f"{self.requested_by} -> {self.institution.name} "
            f"({self.get_status_display()})"
        )

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("platform_admin:access_request_detail", args=[self.pk])

    def clean(self):
        if self.reason and len(self.reason.strip()) < 20:
            raise ValidationError(
                {"reason": "Give a reason of at least 20 characters."}
            )
        unknown = set(self.categories or []) - set(ACCESS_CATEGORY_CODES)
        if unknown:
            raise ValidationError({"categories": "Unknown data category."})

    @property
    def needs_extra_approver(self):
        return bool(set(self.categories or []) & HIGH_PROTECTION_CATEGORIES)

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING


class DataAccessGrant(models.Model):
    """A time-limited, revocable consent record scoped to exact categories."""

    access_request = models.OneToOneField(
        AccessRequest, on_delete=models.CASCADE, related_name="grant"
    )
    institution = models.ForeignKey(
        Institution, on_delete=models.CASCADE, related_name="platform_data_grants"
    )
    platform_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="platform_data_grants",
    )
    categories = models.JSONField(default=list, blank=True)
    mode = models.CharField(max_length=20, choices=AccessRequest.Mode.choices)
    include_export = models.BooleanField(default=False)
    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_data_grants_revoked",
    )
    revoked_reason = models.TextField(blank=True)
    # Set when the expiry sweep closes the grant. The time can be earlier than
    # ``expires_at`` when a scheduled sweep runs, and keeping it separate from
    # ``revoked_at`` preserves the difference between expiry and revocation.
    expired_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [models.Index(fields=["institution", "platform_admin"])]

    def __str__(self):
        return (
            f"Grant #{self.pk} to {self.platform_admin} for "
            f"{self.institution.name} until {self.expires_at:%Y-%m-%d %H:%M}"
        )

    @property
    def is_revoked(self):
        return self.revoked_at is not None

    @property
    def is_expired(self):
        return self.expired_at is not None or self.expires_at <= timezone.now()

    @property
    def is_active(self):
        return not self.is_revoked and not self.is_expired

    @property
    def category_labels(self):
        return [
            ACCESS_CATEGORY_LABELS.get(code, code) for code in (self.categories or [])
        ]

    @property
    def mode_label(self):
        return "read-and-change" if self.mode == AccessRequest.Mode.READ_WRITE else "read-only"

    @property
    def banner(self):
        labels = ", ".join(self.category_labels) or "no categories"
        return (
            f"Access granted until {timezone.localtime(self.expires_at):%H:%M} "
            f"for {labels}, {self.mode_label}"
        )


# ---------- platform audit ----------

class PlatformAuditQuerySet(models.QuerySet):
    """An append-only queryset: no bulk edits and no deletion."""

    def update(self, *args, **kwargs):
        raise PermissionDenied("The platform audit log cannot be edited.")

    def delete(self, *args, **kwargs):
        raise PermissionDenied("The platform audit log cannot be deleted.")


class PlatformAuditLog(models.Model):
    """A tamper-evident record of every platform decision and data view.

    Each row carries the hash of the row before it, so removing or changing a
    row in the middle breaks the chain. Platform administrators have no screen
    that edits or deletes these rows and the model refuses both operations.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_audit_entries",
    )
    action = models.CharField(max_length=80, db_index=True)
    institution = models.ForeignKey(
        Institution, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="platform_audit_entries",
    )
    grant = models.ForeignKey(
        DataAccessGrant, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    record_type = models.CharField(max_length=80, blank=True)
    record_id = models.CharField(max_length=80, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    prev_hash = models.CharField(max_length=64, blank=True)
    entry_hash = models.CharField(max_length=64, unique=True, editable=False)
    created = models.DateTimeField(default=timezone.now, db_index=True, editable=False)

    objects = PlatformAuditQuerySet.as_manager()

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [
            models.Index(fields=["institution", "-created"]),
            models.Index(fields=["actor", "-created"]),
        ]

    def __str__(self):
        return f"{self.created:%Y-%m-%d %H:%M} {self.action}"

    def save(self, *args, **kwargs):
        if self.pk and PlatformAuditLog.objects.filter(pk=self.pk).exists():
            raise PermissionDenied("The platform audit log cannot be edited.")
        with transaction.atomic():
            previous = (
                PlatformAuditLog.objects.order_by("-pk")
                .values_list("entry_hash", flat=True)
                .first()
            )
            self.prev_hash = previous or "GENESIS"
            self.entry_hash = hashlib.sha256(
                f"{self.prev_hash}:{self._payload()}".encode()
            ).hexdigest()
            return super().save(*args, **kwargs)

    def _payload(self):
        return json.dumps(
            {
                "actor_id": self.actor_id,
                "action": self.action,
                "institution_id": self.institution_id,
                "grant_id": self.grant_id,
                "record_type": self.record_type,
                "record_id": self.record_id,
                "ip_address": self.ip_address,
                "detail": self.detail or {},
                "before": self.before,
                "after": self.after,
                "created": self.created.isoformat() if self.created else "",
            },
            sort_keys=True,
            default=str,
        )

    def delete(self, *args, **kwargs):
        raise PermissionDenied("The platform audit log cannot be deleted.")

    @classmethod
    def verify_chain(cls):
        """Return True when every hash still matches the row before it."""
        previous = "GENESIS"
        for entry in cls.objects.order_by("pk").iterator():
            expected = hashlib.sha256(
                f"{previous}:{entry._payload()}".encode()
            ).hexdigest()
            if entry.prev_hash != previous or entry.entry_hash != expected:
                return False
            previous = entry.entry_hash
        return True


# ---------- platform staff, inbox, settings, consent ----------

class PlatformRole(models.Model):
    """A configurable subset of platform permissions, e.g. Support or Billing."""

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=200, blank=True)
    permissions = models.JSONField(default=list, blank=True)
    is_system = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def has_permission(self, code):
        return "*" in (self.permissions or []) or code in (self.permissions or [])


class PlatformStaff(models.Model):
    """A person who works for EduSphere, not for one of its institutions."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="platform_staff",
    )
    role = models.ForeignKey(
        PlatformRole, null=True, blank=True, on_delete=models.PROTECT,
        related_name="staff",
    )
    is_main_admin = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    two_factor_enabled = models.BooleanField(default=False)
    two_factor_secret = models.CharField(max_length=64, blank=True)
    last_reauth_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user__first_name", "user__last_name"]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} (platform)"

    def has_permission(self, code):
        from .permissions import has_code

        return has_code(self, code)

    @property
    def can_see_protected_data(self):
        return False


class PlatformRequest(models.Model):
    """A platform-level decision that is not an institution application."""

    class Kind(models.TextChoices):
        PLAN_CHANGE = "plan_change", "Plan change"
        MODULE = "module", "Module request"
        EXTENSION = "extension", "Extension request"
        DATA_EXPORT = "data_export", "Data export"
        DELETION = "deletion", "Deletion request"
        STAFF_REGISTRATION = "staff_registration", "Escalated staff registration"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CLOSED = "closed", "Closed"

    institution = models.ForeignKey(
        Institution, null=True, blank=True, on_delete=models.CASCADE,
        related_name="platform_requests",
    )
    kind = models.CharField(max_length=30, choices=Kind.choices)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_requests_made",
    )
    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True)
    reference = models.CharField(max_length=120, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_requests_assigned",
    )
    decision_reason = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [models.Index(fields=["status", "-created"])]

    def __str__(self):
        return self.title


class PlatformAnnouncement(models.Model):
    class Audience(models.TextChoices):
        EVERYONE = "everyone", "Everyone"
        INSTITUTIONS = "institutions", "Institution administrators"
        STAFF = "staff", "Platform staff"

    title = models.CharField(max_length=200)
    body = models.TextField()
    audience = models.CharField(
        max_length=20, choices=Audience.choices, default=Audience.INSTITUTIONS
    )
    is_active = models.BooleanField(default=True)
    published_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_announcements",
    )
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-published_at", "-pk"]

    def __str__(self):
        return self.title


class PlatformSetting(models.Model):
    """Platform-wide settings, kept as typed JSON values."""

    key = models.SlugField(max_length=80, unique=True)
    value = models.JSONField(default=dict, blank=True)
    description = models.CharField(max_length=200, blank=True)
    updated = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="platform_settings_changed",
    )

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.key

    @classmethod
    def get(cls, key, default=None):
        row = cls.objects.filter(key=key).first()
        return row.value if row is not None else default

    @classmethod
    def set(cls, key, value, actor=None, description=""):
        row, _created = cls.objects.update_or_create(
            key=key,
            defaults={
                "value": value,
                "description": description,
                "updated_by": actor if getattr(actor, "is_authenticated", False) else None,
            },
        )
        return row


class DataConsentLog(models.Model):
    """Evidence that an institution accepted the data processing statement.

    The log is append-only in practice: it supports the record-keeping duty in
    Zimbabwe's Cyber and Data Protection Act, and an institution may download
    its own copy from the settings screen.
    """

    class Kind(models.TextChoices):
        DATA_PROCESSING = "data_processing", "Data processing statement"
        TERMS = "terms", "Terms of service"
        PRIVACY = "privacy", "Privacy notice"

    institution = models.ForeignKey(
        Institution, on_delete=models.CASCADE, related_name="data_consent_logs"
    )
    application = models.ForeignKey(
        InstitutionApplication, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="consent_logs",
    )
    kind = models.CharField(
        max_length=30, choices=Kind.choices, default=Kind.DATA_PROCESSING
    )
    version = models.CharField(max_length=20, default="1.0")
    statement_text = models.TextField()
    statement_hash = models.CharField(max_length=64, blank=True)
    accepted_by_name = models.CharField(max_length=200, blank=True)
    accepted_by_email = models.EmailField(blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="data_consents_recorded",
    )
    accepted_at = models.DateTimeField(default=timezone.now, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-accepted_at", "-pk"]
        indexes = [models.Index(fields=["institution", "kind"])]

    def __str__(self):
        return f"{self.get_kind_display()} v{self.version} for {self.institution.name}"

    def save(self, *args, **kwargs):
        if not self.statement_hash and self.statement_text:
            self.statement_hash = hashlib.sha256(
                self.statement_text.encode("utf-8")
            ).hexdigest()
        return super().save(*args, **kwargs)
