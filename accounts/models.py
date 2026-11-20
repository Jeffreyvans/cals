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
        "core.Institution", null=True, blank=True, on_delete=models.CASCADE,
        related_name="users",
    )
    primary_role = models.ForeignKey(
        "core.Role", null=True, blank=True, on_delete=models.SET_NULL, related_name="primary_users"
    )
    extra_roles = models.ManyToManyField("core.Role", blank=True, related_name="extra_users")

    # A platform administrator belongs to the platform itself, not to a school.
    # The explicit flag makes the intent clear in the database and lets the
    # account exist without an institution.
    is_platform_admin = models.BooleanField(
        default=False,
        help_text="A main administrator of the platform itself.",
    )

    # Account lifecycle. A temporary password is only ever shown once, so the
    # account is flagged and the middleware sends the person to change it.
    must_change_password = models.BooleanField(default=False)
    # True while the current password was set from PLATFORM_ADMIN_PASSWORD.
    # The platform dashboard warns until the person chooses their own password.
    password_set_from_env = models.BooleanField(default=False)
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

    class Meta:
        indexes = [
            models.Index(fields=["institution", "role"], name="user_inst_role_idx"),
            models.Index(fields=["institution", "is_active"], name="user_inst_active_idx"),
            models.Index(fields=["email"], name="user_email_idx"),
            models.Index(fields=["institution", "email"], name="user_inst_email_idx"),
            models.Index(fields=["institution", "username"], name="user_inst_username_idx"),
        ]

    @property
    def is_staff_role(self):
        return self.role in ("admin", "registrar", "bursar", "hod", "dean", "accountant")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Platform administrators deliberately have no institution; every other
        # account is attached to the current (or default) institution.
        if self.institution_id is None and not self.is_platform_admin:
            from core.tenancy import current_institution_id, default_institution_id

            self.institution_id = current_institution_id() or default_institution_id()

    def save(self, *args, **kwargs):
        if self.institution_id is None and not self.is_platform_admin:
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


class SignupLink(models.Model):
    """A shareable link that opens self-registration for one institution.

    A ``UserInvitation`` belongs to a person who already has a row. A sign-up
    link is the opposite: an administrator hands it out and anyone who opens it
    may register. The link can be limited to students or staff, preset to one
    staff position, and can carry an expiry date and a maximum number of uses.
    It works even when the institution has public registration switched off,
    because the link itself is the invitation.
    """

    class Kind(models.TextChoices):
        STUDENT = "student", "Students"
        STAFF = "staff", "Staff"
        ANY = "any", "Students or staff"

    institution = models.ForeignKey(
        "core.Institution", on_delete=models.CASCADE, related_name="signup_links")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    label = models.CharField(
        max_length=120, blank=True,
        help_text="A name for your own reference, such as 'Term 1 intake'.")
    kind = models.CharField(
        max_length=20, choices=Kind.choices, default=Kind.ANY)
    position = models.CharField(
        max_length=40, blank=True,
        help_text="For a staff link: the position it is preset to, if any.")
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="signup_links_created")
    # Set when a platform administrator created the link during onboarding.
    # It is informational only: the link still belongs to the institution and
    # obeys exactly the same rules as one created by an institution admin.
    created_by_platform_admin = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Leave empty for no limit.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [models.Index(fields=["institution", "is_active"])]

    def __str__(self):
        return self.label or f"Sign-up link {self.token}"

    @property
    def is_expired(self):
        from django.utils import timezone

        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def use_count(self):
        return self.uses.count()

    @property
    def is_exhausted(self):
        return self.max_uses is not None and self.use_count >= self.max_uses

    @property
    def is_open(self):
        return self.is_active and not self.is_expired and not self.is_exhausted

    @property
    def position_label(self):
        from accounts.registration import position_label

        return position_label(self.position) if self.position else ""

    def path(self):
        """The public path a visitor opens, without the domain."""
        from django.urls import reverse

        return reverse("signup_link", args=[self.token])

    def url(self, request=None):
        """The absolute link to share, based on SITE_URL."""
        from core.notifications import absolute_url

        return absolute_url(self.path())


class SignupLinkUse(models.Model):
    """One registration that came through a sign-up link.

    Keeping a row per use gives the administrator a clear record of who
    arrived through a link and makes the maximum-uses limit exact.
    """

    link = models.ForeignKey(
        SignupLink, on_delete=models.CASCADE, related_name="uses")
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="signup_link_uses")
    email = models.EmailField(blank=True)
    kind = models.CharField(max_length=20, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"{self.kind or 'registration'} via {self.link}"


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


class ReservedUsername(models.Model):
    """A username that has ever been used and must never be assigned again.

    Usernames are compared case-insensitively. A reservation is kept even when
    the account that used the name is deleted, so a later person never inherits
    an old login name.
    """

    username = models.CharField(max_length=150, unique=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["username"]

    def __str__(self):
        return self.username

    def save(self, *args, **kwargs):
        self.username = str(self.username or "").strip().lower()[:150]
        super().save(*args, **kwargs)


def reserve_username_on_user_delete(sender, instance, **kwargs):
    """Keep a deleted account's username reserved forever."""
    if not getattr(instance, "username", ""):
        return
    from .usernames import reserve_username

    reserve_username(instance.username)


models.signals.post_delete.connect(reserve_username_on_user_delete, sender=User)
