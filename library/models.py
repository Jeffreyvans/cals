"""Library catalogue, loans and fines."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import TenantModel


class Book(TenantModel):
    class Category(models.TextChoices):
        FICTION = "fiction", "Fiction"
        NON_FICTION = "non_fiction", "Non-fiction"
        REFERENCE = "reference", "Reference"
        TEXTBOOK = "textbook", "Textbook"
        JOURNAL = "journal", "Journal"
        OTHER = "other", "Other"

    title = models.CharField(max_length=200)
    author = models.CharField(max_length=150, blank=True)
    isbn = models.CharField(max_length=30, blank=True)
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.TEXTBOOK
    )
    shelf = models.CharField(max_length=40, blank=True)
    copies_total = models.PositiveIntegerField(default=1)
    copies_available = models.PositiveIntegerField(default=1)
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["title", "pk"]

    def clean(self):
        super().clean()
        if self.copies_available > self.copies_total:
            raise ValidationError(
                {"copies_available": "Available copies cannot exceed total copies."}
            )

    def __str__(self):
        return f"{self.title} ({self.author})" if self.author else self.title


class Loan(TenantModel):
    class Status(models.TextChoices):
        BORROWED = "borrowed", "On loan"
        RETURNED = "returned", "Returned"
        OVERDUE = "overdue", "Overdue"
        LOST = "lost", "Lost"

    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name="loans")
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="library_loans"
    )
    borrowed_on = models.DateField(default=timezone.localdate)
    due_on = models.DateField()
    returned_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BORROWED)
    fine = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    fine_invoice = models.ForeignKey(
        "finance.Invoice",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="library_loans",
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="library_loans_issued",
    )
    renewals = models.PositiveIntegerField(default=0)
    overdue_notified_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-borrowed_on", "-pk"]

    def clean(self):
        super().clean()
        if self.due_on and self.borrowed_on and self.due_on < self.borrowed_on:
            raise ValidationError({"due_on": "The due date cannot be before the loan date."})

    @property
    def is_overdue(self):
        return self.status in (self.Status.BORROWED, self.Status.OVERDUE) and (
            self.due_on < timezone.localdate()
        )

    def save(self, *a, **kw):
        adding = self._state.adding
        old_returned = None
        if not adding:
            old_returned = (
                Loan.all_objects.filter(pk=self.pk)
                .values_list("returned_on", flat=True)
                .first()
            )
        super().save(*a, **kw)
        if adding and self.book_id and not self.returned_on:
            Book.all_objects.filter(pk=self.book_id).update(
                copies_available=models.F("copies_available") - 1
            )
        if not adding and old_returned is None and self.returned_on:
            Book.all_objects.filter(pk=self.book_id).update(
                copies_available=models.F("copies_available") + 1
            )

    def __str__(self):
        return f"{self.book} - {self.student}"


class Reservation(TenantModel):
    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting"
        READY = "ready", "Ready to collect"
        COLLECTED = "collected", "Collected"
        CANCELLED = "cancelled", "Cancelled"

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="reservations")
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="library_reservations"
    )
    reserved_on = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.WAITING)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["reserved_on", "pk"]

    def __str__(self):
        return f"{self.book} for {self.student}"

