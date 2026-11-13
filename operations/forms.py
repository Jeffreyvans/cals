"""Model forms for the administration registers.

The forms are generated from the models so a register only needs a small
entry in :mod:`operations.registry`. The shared base class scopes user
pickers to the current institution and gives dates browser-friendly inputs.
"""

from django import forms
from django.contrib.auth import get_user_model

from .models import (
    ActionItem,
    Appraisal,
    Asset,
    Campaign,
    CleaningSchedule,
    ComplianceAudit,
    DataRequest,
    DisciplinaryCase,
    Enquiry,
    GatePass,
    GoodsReceivedNote,
    GovernanceLevy,
    Incident,
    JobApplication,
    LeaveRequest,
    MaintenancePlan,
    Meeting,
    Newsletter,
    OpenDay,
    PayrollRule,
    PayrollRun,
    Payslip,
    Policy,
    PolicyAcknowledgement,
    PurchaseOrder,
    PurchaseOrderLine,
    Quotation,
    QuotationLine,
    Requisition,
    RequisitionLine,
    Resolution,
    Risk,
    SalaryStructure,
    StaffContract,
    StaffDocument,
    StockItem,
    StockMovement,
    StudentSignOut,
    SupplierInvoice,
    TrainingRecord,
    UtilityReading,
    Vacancy,
    Visitor,
    WorkOrder,
)


class TenantModelForm(forms.ModelForm):
    """Base form: scope user pickers and use date/time inputs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.tenancy import current_institution_id

        user_model = get_user_model()
        institution_id = current_institution_id()
        for field in self.fields.values():
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
            elif isinstance(field, forms.DateTimeField):
                field.widget = forms.DateTimeInput(
                    attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
                )
            elif isinstance(field, forms.TimeField):
                field.widget = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
            related = getattr(field, "queryset", None)
            if related is not None and field.__class__.__name__ == "ModelChoiceField":
                model = related.model
                if model is user_model and institution_id is not None:
                    field.queryset = model.objects.filter(institution_id=institution_id)


def _form(model, exclude=()):
    """Build a model form, leaving out fields the screens set themselves."""

    base_exclude = [
        "institution",
        "created",
        "updated",
        "created_by",
        "uploaded_by",
        "requested_by",
        "reported_by",
        "issued_by",
        "received_by",
    ]
    exclude = list(dict.fromkeys([*base_exclude, *exclude]))
    meta = type("Meta", (), {"model": model, "fields": "__all__", "exclude": exclude})
    return type(
        f"{model.__name__}Form",
        (TenantModelForm,),
        {"Meta": meta, "__module__": __name__},
    )


StaffContractForm = _form(StaffContract)
StaffDocumentForm = _form(StaffDocument)
LeaveRequestForm = _form(
    LeaveRequest, exclude=["status", "decided_by", "decided_at", "decision_note"]
)
AppraisalForm = _form(Appraisal)
TrainingRecordForm = _form(TrainingRecord)
DisciplinaryCaseForm = _form(DisciplinaryCase)
VacancyForm = _form(Vacancy)
JobApplicationForm = _form(JobApplication)

SalaryStructureForm = _form(SalaryStructure)
PayrollRuleForm = _form(PayrollRule)
PayrollRunForm = _form(
    PayrollRun, exclude=["status", "approved_by", "approved_at", "posted_journal"]
)
PayslipForm = _form(Payslip)

RequisitionForm = _form(Requisition)
RequisitionLineForm = _form(RequisitionLine)
PurchaseOrderForm = _form(PurchaseOrder, exclude=["number", "status", "approved_by"])
PurchaseOrderLineForm = _form(PurchaseOrderLine)
QuotationForm = _form(Quotation, exclude=["number", "status", "awarded_by", "awarded_at"])
QuotationLineForm = _form(QuotationLine)
SupplierInvoiceForm = _form(
    SupplierInvoice,
    exclude=[
        "status",
        "variance",
        "match_note",
        "expense",
        "matched_by",
        "matched_at",
    ],
)
GoodsReceivedNoteForm = _form(GoodsReceivedNote)
StockItemForm = _form(StockItem)
StockMovementForm = _form(StockMovement)
AssetForm = _form(Asset)

WorkOrderForm = _form(WorkOrder)
CleaningScheduleForm = _form(CleaningSchedule)
UtilityReadingForm = _form(UtilityReading)
MaintenancePlanForm = _form(MaintenancePlan)

VisitorForm = _form(Visitor)
GatePassForm = _form(GatePass)
StudentSignOutForm = _form(StudentSignOut)
IncidentForm = _form(Incident)

PolicyForm = _form(Policy)
PolicyAcknowledgementForm = _form(PolicyAcknowledgement)
ComplianceAuditForm = _form(ComplianceAudit)
RiskForm = _form(Risk)
DataRequestForm = _form(DataRequest)

EnquiryForm = _form(Enquiry)
CampaignForm = _form(Campaign)
OpenDayForm = _form(OpenDay)
NewsletterForm = _form(Newsletter, exclude=["status", "sent_at"])

MeetingForm = _form(Meeting)
ResolutionForm = _form(Resolution)
ActionItemForm = _form(ActionItem)
GovernanceLevyForm = _form(GovernanceLevy)


class LeaveDecisionForm(forms.Form):
    decision = forms.ChoiceField(choices=[("approve", "Approve"), ("reject", "Reject")])
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class StockMovementEntryForm(forms.Form):
    item = forms.ModelChoiceField(queryset=StockItem.objects.none())
    kind = forms.ChoiceField(choices=StockMovement.Kind.choices)
    quantity = forms.DecimalField(min_value=0.01, decimal_places=2)
    note = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = StockItem.objects.all()
