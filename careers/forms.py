from core.forms import form_for

from .models import (
    AttachmentGrade,
    AttachmentLog,
    CareerEvent,
    Employer,
    Placement,
    SupervisorReport,
)

EmployerForm = form_for(Employer)
PlacementForm = form_for(Placement)
AttachmentLogForm = form_for(AttachmentLog, exclude=["signed_by"])
CareerEventForm = form_for(CareerEvent, exclude=["organiser"])
SupervisorReportForm = form_for(SupervisorReport, exclude=["signed_by"])
AttachmentGradeForm = form_for(AttachmentGrade, exclude=["graded_by", "graded_on"])
