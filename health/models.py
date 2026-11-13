"""Health and sick-bay records. Every view of these rows is logged."""

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TenantModel


class HealthProfile(TenantModel):
    student = models.OneToOneField(
        "academics.Student", on_delete=models.CASCADE, related_name="health_profile"
    )
    blood_group = models.CharField(max_length=10, blank=True)
    allergies = models.TextField(blank=True)
    conditions = models.TextField(blank=True)
    medication = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    share_with_teachers = models.BooleanField(default=False)
    teacher_note = models.TextField(blank=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["student__user__last_name", "student__user__first_name"]

    @property
    def has_allergy(self):
        return bool(self.allergies.strip())

    def __str__(self):
        return f"Health profile for {self.student}"


class ClinicVisit(TenantModel):
    class Outcome(models.TextChoices):
        TREATED = "treated", "Treated and returned to class"
        SENT_HOME = "sent_home", "Sent home"
        REFERRED = "referred", "Referred to hospital"
        OBSERVATION = "observation", "Kept for observation"

    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="clinic_visits"
    )
    visited_at = models.DateTimeField(default=timezone.now)
    complaint = models.TextField()
    temperature = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    diagnosis = models.TextField(blank=True)
    treatment = models.TextField(blank=True)
    outcome = models.CharField(
        max_length=20, choices=Outcome.choices, default=Outcome.TREATED
    )
    attended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="clinic_visits_attended",
    )
    follow_up_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-visited_at", "-pk"]

    def __str__(self):
        return f"{self.student} - {self.visited_at:%Y-%m-%d}"


class MedicationAdministration(TenantModel):
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="medications"
    )
    medication = models.CharField(max_length=150)
    dosage = models.CharField(max_length=80)
    given_at = models.DateTimeField(default=timezone.now)
    administered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="medications_administered",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-given_at", "-pk"]
        verbose_name_plural = "medication administrations"

    def __str__(self):
        return f"{self.medication} for {self.student}"


class Immunisation(TenantModel):
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="immunisations"
    )
    vaccine = models.CharField(max_length=120)
    dose = models.CharField(max_length=40, blank=True)
    given_on = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-given_on", "-pk"]

    def __str__(self):
        return f"{self.vaccine} - {self.student}"
