from core.modules import module_label

from .forms import (
    ClinicVisitForm,
    HealthProfileForm,
    ImmunisationForm,
    MedicationAdministrationForm,
)
from .models import ClinicVisit, HealthProfile, Immunisation, MedicationAdministration

MODULES = {
    "health": {
        "title": module_label("health"),
        "description": "Sick bay, allergies, medication and immunisations.",
        "permissions": ("health.view_records", "health.manage"),
        "sensitive": True,
        "links": [("Teacher-safe alerts", "health:safe_alerts")],
        "kinds": [
            {
                "slug": "profiles",
                "title": "Health profiles",
                "description": "Allergies and conditions. Restricted to health staff.",
                "model": HealthProfile,
                "form": HealthProfileForm,
                "sensitive": True,
                "search": ["student__student_no", "student__user__last_name", "allergies"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("Blood group", lambda o: o.blood_group or "-"),
                    ("Allergies", lambda o: o.allergies or "-"),
                    ("Updated", lambda o: o.updated),
                ],
            },
            {
                "slug": "visits",
                "title": "Clinic visits",
                "description": "Sick-bay visits and outcomes.",
                "model": ClinicVisit,
                "form": ClinicVisitForm,
                "sensitive": True,
                "search": ["student__student_no", "complaint", "diagnosis"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("When", lambda o: o.visited_at),
                    ("Complaint", lambda o: o.complaint),
                    ("Outcome", lambda o: o.get_outcome_display()),
                ],
            },
            {
                "slug": "medication",
                "title": "Medication given",
                "description": "Medication administered at school.",
                "model": MedicationAdministration,
                "form": MedicationAdministrationForm,
                "sensitive": True,
                "search": ["student__student_no", "medication"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("Medication", lambda o: o.medication),
                    ("Dosage", lambda o: o.dosage),
                    ("Given", lambda o: o.given_at),
                ],
            },
            {
                "slug": "immunisations",
                "title": "Immunisations",
                "description": "Vaccination history.",
                "model": Immunisation,
                "form": ImmunisationForm,
                "sensitive": True,
                "search": ["student__student_no", "vaccine"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("Vaccine", lambda o: o.vaccine),
                    ("Dose", lambda o: o.dose or "-"),
                    ("Given", lambda o: o.given_on),
                ],
            },
        ],
    }
}
