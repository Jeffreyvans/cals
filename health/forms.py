from core.forms import form_for

from .models import ClinicVisit, HealthProfile, Immunisation, MedicationAdministration

HealthProfileForm = form_for(HealthProfile, exclude=["updated"])
ClinicVisitForm = form_for(ClinicVisit, exclude=["attended_by"])
MedicationAdministrationForm = form_for(MedicationAdministration, exclude=["administered_by"])
ImmunisationForm = form_for(Immunisation)
