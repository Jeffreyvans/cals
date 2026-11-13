from core.forms import form_for

from .models import (
    Consumable,
    ConsumableIssue,
    Equipment,
    EquipmentBooking,
    Lab,
    MaintenanceLog,
    PracticalAssessment,
    SafetyInduction,
)

LabForm = form_for(Lab)
EquipmentForm = form_for(Equipment)
EquipmentBookingForm = form_for(EquipmentBooking, exclude=["approved_by"])
MaintenanceLogForm = form_for(MaintenanceLog)
SafetyInductionForm = form_for(SafetyInduction, exclude=["inducted_by"])
ConsumableForm = form_for(Consumable)
ConsumableIssueForm = form_for(ConsumableIssue, exclude=["issued_by"])
PracticalAssessmentForm = form_for(PracticalAssessment, exclude=["assessor"])
