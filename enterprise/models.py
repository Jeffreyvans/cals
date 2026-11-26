"""Enterprise records: groups of schools, sponsors, domains and white-label.

These models reuse the platform's existing rows wherever possible. An
organisation groups existing ``Institution`` rows without moving or deleting
them; a sponsor is the existing ``finance.Sponsor``; student-level access is
granted with the existing time-limited ``DataAccessGrant``; and platform
invoices are the existing ``platform_admin.PlatformInvoice`` rows.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from enterprise.settings_registry import SETTING_KEYS


def new_group_number():
    """A unique, human-readable group invoice number."""
    return "GRP-" + uuid.uuid4().hex[:12].upper()


class Organisation(models.Model):
    """One organisation with several campuses (institutions)."""

    class Type(models.TextChoices):
        CHURCH = "church", "Church"
        TRUST = "trust", "Trust"
        COMPANY = "company", "Company"
        GOVERNMENT = "government", "Government"
        RESELLER = "reseller", "Reseller"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.TRUST)
    logo = models.ImageField(upload_to="organisations/logos/", blank=True)
    primary_color = models.CharField(max_length=20, blank=True)
    accent_color = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    website = models.URLField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    def campuses(self):
        """The institutions currently attached to this organisation."""
        from core.models import Institution

        return Institution.objects.filter(
            group_membership__organisation=self, is_active=True
        ).order_by("group_membership__sort_order", "group_membership__campus_label", "name")

    def campus_ids(self):
        return list(self.campuses().values_list("pk", flat=True))


class OrganisationMembership(models.Model):
    """A campus's link to at most one organisation.

    Detaching is deleting this link only. Institution rows, students, money
    and documents are never touched.
    """

    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="memberships"
    )
    institution = models.OneToOneField(
        "core.Institution", on_delete=models.CASCADE, related_name="group_membership"
    )
    campus_label = models.CharField(max_length=120, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    attached_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="campuses_attached",
    )
    attached_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "campus_label"]

    def __str__(self):
        label = self.campus_label or self.institution.name
        return f"{self.organisation.name}: {label}"


class GroupMembership(models.Model):
    """A role in one organisation. It never grants student-level access."""

    class Role(models.TextChoices):
        ADMIN = "group_admin", "Group administrator"
        FINANCE = "group_finance", "Group finance officer"
        ACADEMIC = "group_academic", "Group academic officer"
        AUDITOR = "group_auditor", "Group auditor (read only)"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="group_memberships"
    )
    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="group_users"
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organisation"], name="uniq_group_membership"
            )
        ]
        ordering = ["organisation__name", "role"]

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"


class SharedSetting(models.Model):
    """A group default. Changing it never rewrites a campus record."""

    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="shared_settings"
    )
    key = models.CharField(max_length=40, choices=[(k, k) for k in SETTING_KEYS])
    value = models.JSONField(default=dict, blank=True)
    locked = models.BooleanField(
        default=False, help_text="Campuses cannot override a locked group default."
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="shared_settings_updated",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "key"], name="uniq_shared_setting"
            )
        ]
        ordering = ["key"]

    def __str__(self):
        return f"{self.organisation.name}: {self.key}"


class CampusSettingOverride(models.Model):
    """A campus's own value for one shared setting within one organisation."""

    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="campus_setting_overrides"
    )
    institution = models.ForeignKey(
        "core.Institution", on_delete=models.CASCADE, related_name="group_setting_overrides"
    )
    key = models.CharField(max_length=40, choices=[(k, k) for k in SETTING_KEYS])
    value = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="campus_setting_overrides_updated",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "institution", "key"],
                name="uniq_campus_setting_override",
            )
        ]
        ordering = ["key"]

    def __str__(self):
        return f"{self.institution.name}: {self.key}"


class GroupBilling(models.Model):
    """How the platform charges an organisation's campuses."""

    class Mode(models.TextChoices):
        SEPARATE = "separate", "Each campus billed separately"
        CONSOLIDATED = "consolidated", "One consolidated organisation invoice"

    organisation = models.OneToOneField(
        Organisation, on_delete=models.CASCADE, related_name="billing"
    )
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.SEPARATE)
    discount_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=None, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Override the plan's volume discount percentage (0-100).",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.organisation.name}: {self.get_mode_display()}"


class GroupInvoice(models.Model):
    """A platform invoice plus its per-campus line items."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PAID = "paid", "Paid"

    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="group_invoices"
    )
    platform_invoice = models.OneToOneField(
        "platform_admin.PlatformInvoice", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="group_invoice",
    )
    number = models.CharField(max_length=40, unique=True, default=new_group_number)
    period = models.CharField(max_length=40, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return self.number


class GroupInvoiceLine(models.Model):
    """One campus's share of a group invoice."""

    invoice = models.ForeignKey(
        GroupInvoice, on_delete=models.CASCADE, related_name="lines"
    )
    institution = models.ForeignKey(
        "core.Institution", on_delete=models.PROTECT, related_name="group_invoice_lines"
    )
    campus_label = models.CharField(max_length=120, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["campus_label", "pk"]

    def __str__(self):
        return f"{self.invoice.number}: {self.campus_label or self.institution.name}"

    @property
    def net(self):
        return self.amount - self.discount


class GroupAlert(models.Model):
    """A read-only dashboard alert derived from campus data."""

    class Kind(models.TextChoices):
        FEES = "fees", "Campus behind on fees"
        ATTENDANCE = "attendance", "Low attendance"
        SUBSCRIPTION = "subscription", "Subscription expiring"

    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="alerts"
    )
    institution = models.ForeignKey(
        "core.Institution", null=True, blank=True,
        on_delete=models.CASCADE, related_name="group_alerts",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    message = models.CharField(max_length=240)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return self.message


class GroupAccessRequestLink(models.Model):
    """Marks an existing access request as a group student-data request.

    The consent, grant, expiry, revocation and "Who accessed our data" page
    are the platform's existing ones. This row only records that the request
    came from a group role, so the request can be filtered in group screens.
    """

    access_request = models.OneToOneField(
        "platform_admin.AccessRequest", on_delete=models.CASCADE,
        related_name="group_link",
    )
    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="access_links"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="group_access_links"
    )
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Group access request {self.access_request_id}"
