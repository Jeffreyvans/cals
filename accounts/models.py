import uuid

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models


class TenantUserQuerySet(models.QuerySet):
    def for_institution(self, institution):
        if institution is None:
            return self.none()
        pk = institution if isinstance(institution, int) else institution.pk
        return self.filter(institution_id=pk)


class TenantUserManager(UserManager):
    """Only returns users of the current institution."""

    use_in_migrations = False

    def get_queryset(self):
        from core.tenancy import CurrentInstitutionId

        return TenantUserQuerySet(self.model, using=self._db).filter(
            institution_id=CurrentInstitutionId()
        )

    def for_institution(self, institution):
        return TenantUserQuerySet(self.model, using=self._db).for_institution(institution)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        REGISTRAR = "registrar", "Registrar"
        BURSAR = "bursar", "Bursar / Accounts"
        LECTURER = "lecturer", "Lecturer / Teacher"
        TEACHER = "teacher", "Teacher"
        HOD = "hod", "Head of Department"
        DEAN = "dean", "Dean"
        ACCOUNTANT = "accountant", "Accountant"
        STUDENT = "student", "Student / Pupil"
        PARENT = "parent", "Parent / Guardian"
        STAFF = "staff", "Staff"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT)
    phone = models.CharField(max_length=30, blank=True)
    institution = models.ForeignKey(
        "core.Institution", null=False, blank=True, on_delete=models.CASCADE, related_name="users"
    )
    primary_role = models.ForeignKey(
        "core.Role", null=True, blank=True, on_delete=models.SET_NULL, related_name="primary_users"
    )
    extra_roles = models.ManyToManyField("core.Role", blank=True, related_name="extra_users")

    # Account lifecycle. A temporary password is only ever shown once, so the
    # account is flagged and the middleware sends the person to change it.
    must_change_password = models.BooleanField(default=False)
    failed_login_count = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicate_accounts",
        help_text="Set when this account is a flagged duplicate of another one.",
    )

    # `objects` stays unfiltered so that sign-in can find a user before the
    # institution is known. `tenant_objects` is the one to use for listings.
    objects = UserManager()
    tenant_objects = TenantUserManager()

    @property
    def is_staff_role(self):
        return self.role in ("admin", "registrar", "bursar", "hod", "dean", "accountant")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.institution_id is None:
            from core.tenancy import current_institution_id, default_institution_id

            self.institution_id = current_institution_id() or default_institution_id()

    def save(self, *args, **kwargs):
        if self.institution_id is None:
            from core.tenancy import current_institution_id, default_institution_id

            self.institution_id = current_institution_id() or default_institution_id()
        super().save(*args, **kwargs)

    def permission_codes(self):
        """Every permission code this user holds."""
        from core.access import permission_codes_for

        return permission_codes_for(self)

    def has_code(self, code):
        """True when the user holds this permission code."""
        from core.access import user_has_code

        return user_has_code(self, code)


class EmailVerification(models.Model):
    """A one-time link that proves a person can read their email address."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="email_verifications")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"Verification for {self.user} ({'verified' if self.verified_at else 'pending'})"

    @property
    def is_verified(self):
        return self.verified_at is not None


class UserInvitation(models.Model):
    """A one-time link that lets an invited person choose their password.

    The token is random and is the only secret in the link. It expires after
    72 hours and can be used once. The password itself is never stored.
    """

    LIFETIME_HOURS = 72

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="invitations")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    invited_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="invitations_sent")
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"Invitation for {self.user} ({'used' if self.used_at else 'open'})"

    @property
    def is_expired(self):
        from django.utils import timezone

        return self.expires_at <= timezone.now()

    @property
    def is_open(self):
        return self.used_at is None and not self.is_expired


class StaffRegistrationRequest(models.Model):
    """A staff sign-up waiting for an administrator to approve it.

    The login is created straight away but stays inactive until a person
    approves the request, so the visitor can never choose their own role.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    institution = models.ForeignKey(
        "core.Institution", on_delete=models.CASCADE,
        related_name="staff_registration_requests")
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="staff_registration")
    full_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    position = models.CharField(max_length=40)
    department = models.ForeignKey(
        "core.Department", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="staff_registration_requests")
    staff_no = models.CharField(max_length=30, blank=True)
    message = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING)
    approved_position = models.CharField(max_length=40, blank=True)
    rejection_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="staff_registrations_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [models.Index(fields=["institution", "status"])]

    def __str__(self):
        return f"{self.full_name} ({self.get_status_display()})"

    @property
    def position_label(self):
        from accounts.registration import position_label

        return position_label(self.approved_position or self.position)

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING
