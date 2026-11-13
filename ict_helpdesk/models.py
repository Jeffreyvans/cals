"""ICT devices, support tickets, SLAs, account resets and software licences."""

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TenantModel


class Device(TenantModel):
    class Kind(models.TextChoices):
        LAPTOP = "laptop", "Laptop"
        DESKTOP = "desktop", "Desktop"
        TABLET = "tablet", "Tablet"
        PRINTER = "printer", "Printer"
        PROJECTOR = "projector", "Projector"
        NETWORK = "network", "Network device"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        IN_USE = "in_use", "In use"
        SPARE = "spare", "Spare"
        REPAIR = "repair", "Under repair"
        RETIRED = "retired", "Retired"
        LOST = "lost", "Lost"

    asset_tag = models.CharField(max_length=40)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.LAPTOP)
    model_name = models.CharField(max_length=120, blank=True)
    serial = models.CharField(max_length=80, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="devices_assigned",
    )
    location = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SPARE)
    purchased_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["asset_tag"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "asset_tag"], name="uniq_ict_asset_tag"
            )
        ]

    def __str__(self):
        return f"{self.asset_tag} {self.model_name}".strip()


class Ticket(TenantModel):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        WAITING = "waiting", "Waiting on user"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    title = models.CharField(max_length=150)
    description = models.TextField()
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ict_tickets_raised",
    )
    category = models.CharField(max_length=80, blank=True)
    priority = models.CharField(
        max_length=20, choices=Priority.choices, default=Priority.MEDIUM
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ict_tickets_assigned",
    )
    device = models.ForeignKey(
        Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets"
    )
    created = models.DateTimeField(auto_now_add=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"#{self.pk} {self.title}" if self.pk else self.title

    @property
    def response_due(self):
        policy = SLAPolicy.objects.filter(priority=self.priority, is_active=True).first()
        if policy is None:
            return None
        return self.created + timedelta(minutes=policy.response_minutes)

    @property
    def resolution_due(self):
        policy = SLAPolicy.objects.filter(priority=self.priority, is_active=True).first()
        if policy is None:
            return None
        return self.created + timedelta(minutes=policy.resolution_minutes)

    @property
    def is_response_overdue(self):
        due = self.response_due
        if due is None or self.first_response_at is not None:
            return False
        return timezone.now() > due

    @property
    def is_resolution_overdue(self):
        due = self.resolution_due
        if due is None or self.resolved_at is not None:
            return False
        return timezone.now() > due


class SLAPolicy(TenantModel):
    """Response and resolution targets for each ticket priority."""

    name = models.CharField(max_length=120)
    priority = models.CharField(
        max_length=20, choices=Ticket.Priority.choices, default=Ticket.Priority.MEDIUM
    )
    response_minutes = models.PositiveIntegerField(default=60)
    resolution_minutes = models.PositiveIntegerField(default=480)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["priority", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "priority"], name="uniq_sla_priority"
            )
        ]
        verbose_name = "SLA policy"
        verbose_name_plural = "SLA policies"

    def __str__(self):
        return f"{self.name} ({self.get_priority_display()})"


class AccountResetRequest(TenantModel):
    """A request to reset a user's password; the temporary password is never stored."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account_reset_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="account_reset_requests_raised",
    )
    ticket = models.ForeignKey(
        Ticket,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reset_requests",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reason = models.CharField(max_length=200, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="account_reset_requests_completed",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"Reset for {self.user} ({self.get_status_display()})"


class TicketComment(TenantModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ict_ticket_comments",
    )
    body = models.TextField()
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created", "pk"]

    def __str__(self):
        return f"Comment on {self.ticket}"


class SoftwareLicence(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=150)
    vendor = models.CharField(max_length=120, blank=True)
    seats = models.PositiveIntegerField(default=1)
    seats_used = models.PositiveIntegerField(default=0)
    key_reference = models.CharField(max_length=120, blank=True)
    starts_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "software licence"
        verbose_name_plural = "software licences"

    def __str__(self):
        return f"{self.name} ({self.seats} seats)"
