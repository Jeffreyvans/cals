"""The registers shown in each administration module.

A kind is one list/create/edit register. Keeping the definitions here means
the generic screens and templates do not need one view per table, while the
module feature flag and permission code still gate every page.
"""

from core.modules import module_label

from . import forms
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


def _text(value):
    return "" if value is None else str(value)


def _date(value):
    return value.strftime("%d %b %Y") if value else "-"


def _datetime(value):
    return value.strftime("%d %b %Y %H:%M") if value else "-"


def _money(value):
    return f"{value:,.2f}" if value is not None else "-"


def _yes(value):
    return "Yes" if value else "No"


def _choice(prefix):
    return lambda obj: getattr(obj, f"get_{prefix}_display", lambda: "")() or "-"


def _staff(value):
    return str(value) if value else "-"


def _user(value):
    if not value:
        return "-"
    return value.get_full_name() or value.username


MODULES = {
    "hr": {
        "title": "Human resources",
        "description": "Staff files, contracts, leave, appraisals and recruitment.",
        "permissions": ("hr.manage", "hr.approve_leave"),
        "kinds": [
            {
                "slug": "contracts",
                "title": "Staff contracts",
                "model": StaffContract,
                "form": forms.StaffContractForm,
                "search": ["reference", "staff__user__first_name", "staff__user__last_name"],
                "columns": [
                    ("Reference", lambda o: o.reference or "-"),
                    ("Staff", lambda o: _staff(o.staff)),
                    ("Type", _choice("kind")),
                    ("Starts", lambda o: _date(o.start_date)),
                    ("Ends", lambda o: _date(o.end_date)),
                    ("Salary", lambda o: _money(o.salary)),
                    ("Status", _choice("status")),
                ],
            },
            {
                "slug": "documents",
                "title": "Staff documents",
                "model": StaffDocument,
                "form": forms.StaffDocumentForm,
                "search": ["title", "staff__user__last_name"],
                "columns": [
                    ("Title", lambda o: o.title),
                    ("Staff", lambda o: _staff(o.staff)),
                    ("Kind", _choice("kind")),
                    ("Confidential", lambda o: _yes(o.confidential)),
                    ("Added", lambda o: _datetime(o.created)),
                ],
            },
            {
                "slug": "leave",
                "title": "Leave requests",
                "model": LeaveRequest,
                "form": forms.LeaveRequestForm,
                "search": ["staff__user__last_name", "reason"],
                "row_action": ("operations:leave_decision", "Decide"),
                "columns": [
                    ("Staff", lambda o: _staff(o.staff)),
                    ("Kind", _choice("kind")),
                    ("From", lambda o: _date(o.start_date)),
                    ("To", lambda o: _date(o.end_date)),
                    ("Days", lambda o: o.days),
                    ("Status", _choice("status")),
                ],
            },
            {
                "slug": "appraisals",
                "title": "Appraisals",
                "model": Appraisal,
                "form": forms.AppraisalForm,
                "search": ["staff__user__last_name", "period"],
                "columns": [
                    ("Staff", lambda o: _staff(o.staff)),
                    ("Period", lambda o: o.period),
                    ("Score", lambda o: _money(o.score)),
                    ("Reviewer", lambda o: _user(o.reviewer)),
                    ("Created", lambda o: _date(o.created)),
                ],
            },
            {
                "slug": "training",
                "title": "Training records",
                "model": TrainingRecord,
                "form": forms.TrainingRecordForm,
                "search": ["title", "provider", "staff__user__last_name"],
                "columns": [
                    ("Title", lambda o: o.title),
                    ("Staff", lambda o: _staff(o.staff)),
                    ("Provider", lambda o: o.provider or "-"),
                    ("Starts", lambda o: _date(o.start_date)),
                    ("Cost", lambda o: _money(o.cost)),
                    ("Completed", lambda o: _yes(o.completed)),
                ],
            },
            {
                "slug": "discipline",
                "title": "Disciplinary cases",
                "model": DisciplinaryCase,
                "form": forms.DisciplinaryCaseForm,
                "search": ["staff__user__last_name", "category", "summary"],
                "columns": [
                    ("Staff", lambda o: _staff(o.staff)),
                    ("Date", lambda o: _date(o.date)),
                    ("Category", lambda o: o.category),
                    ("Status", _choice("status")),
                    ("Confidential", lambda o: _yes(o.confidential)),
                ],
            },
            {
                "slug": "vacancies",
                "title": "Vacancies",
                "model": Vacancy,
                "form": forms.VacancyForm,
                "search": ["title", "description"],
                "columns": [
                    ("Title", lambda o: o.title),
                    ("Department", lambda o: _text(o.department) or "-"),
                    ("Closes", lambda o: _date(o.closes_on)),
                    ("Status", _choice("status")),
                ],
            },
            {
                "slug": "applications",
                "title": "Job applications",
                "model": JobApplication,
                "form": forms.JobApplicationForm,
                "search": ["name", "email", "vacancy__title"],
                "columns": [
                    ("Name", lambda o: o.name),
                    ("Vacancy", lambda o: _text(o.vacancy)),
                    ("Email", lambda o: o.email or "-"),
                    ("Phone", lambda o: o.phone or "-"),
                    ("Status", _choice("status")),
                    ("Applied", lambda o: _datetime(o.applied_at)),
                ],
            },
        ],
    },
    "payroll": {
        "title": "Payroll",
        "description": "Salary structures, tax and pension rules, monthly runs.",
        "permissions": ("payroll.manage", "payroll.approve"),
        "kinds": [
            {
                "slug": "structures",
                "title": "Salary structures",
                "model": SalaryStructure,
                "form": forms.SalaryStructureForm,
                "search": ["staff__user__first_name", "staff__user__last_name"],
                "columns": [
                    ("Staff", lambda o: _staff(o.staff)),
                    ("Basic", lambda o: _money(o.basic)),
                    ("Allowances", lambda o: _money(o.allowances)),
                    ("Gross", lambda o: _money(o.gross)),
                    ("Active", lambda o: _yes(o.is_active)),
                ],
            },
            {
                "slug": "rules",
                "title": "Tax and pension rules",
                "model": PayrollRule,
                "form": forms.PayrollRuleForm,
                "search": ["name"],
                "columns": [
                    ("Name", lambda o: o.name),
                    ("Kind", _choice("kind")),
                    ("Rate", lambda o: f"{o.rate}%"),
                    ("On gross", lambda o: _yes(o.applies_to_gross)),
                    ("Active", lambda o: _yes(o.is_active)),
                ],
            },
            {
                "slug": "runs",
                "title": "Payroll runs",
                "model": PayrollRun,
                "form": forms.PayrollRunForm,
                "search": ["name", "notes"],
                "detail": "operations:payroll_run_detail",
                "action_label": "Open",
                "columns": [
                    ("Run", lambda o: o.name),
                    ("Period", lambda o: f"{_date(o.start_date)} - {_date(o.end_date)}"),
                    ("Pay date", lambda o: _date(o.pay_date)),
                    ("Status", _choice("status")),
                    ("Gross", lambda o: _money(o.total_gross)),
                    ("Net", lambda o: _money(o.total_net)),
                ],
            },
            {
                "slug": "payslips",
                "title": "Payslips",
                "model": Payslip,
                "form": forms.PayslipForm,
                "search": ["staff__user__last_name", "run__name"],
                "row_action": ("operations:payroll_payslip_pdf", "PDF"),
                "columns": [
                    ("Run", lambda o: o.run.name),
                    ("Staff", lambda o: _staff(o.staff)),
                    ("Gross", lambda o: _money(o.gross)),
                    ("Tax", lambda o: _money(o.tax)),
                    ("Pension", lambda o: _money(o.pension)),
                    ("Net", lambda o: _money(o.net)),
                ],
            },
        ],
    },
    "procurement": {
        "title": "Procurement and stores",
        "description": "Requisitions, purchase orders, receipts, stock and assets.",
        "flag": "procurement_stores",
        "permissions": ("procurement.manage", "procurement.approve"),
        "kinds": [
            {
                "slug": "requisitions",
                "title": "Requisitions",
                "model": Requisition,
                "form": forms.RequisitionForm,
                "search": ["title", "justification"],
                "detail": "operations:requisition_detail",
                "action_label": "Compare quotes",
                "columns": [
                    ("Title", lambda o: o.title),
                    ("Department", lambda o: _text(o.department) or "-"),
                    ("Needed by", lambda o: _date(o.needed_by)),
                    ("Status", _choice("status")),
                    ("Estimated", lambda o: _money(o.estimated_total)),
                ],
            },
            {
                "slug": "requisition-lines",
                "title": "Requisition lines",
                "model": RequisitionLine,
                "form": forms.RequisitionLineForm,
                "search": ["description", "requisition__title"],
                "columns": [
                    ("Requisition", lambda o: _text(o.requisition)),
                    ("Description", lambda o: o.description),
                    ("Quantity", lambda o: o.quantity),
                    ("Unit cost", lambda o: _money(o.estimated_unit_cost)),
                    ("Total", lambda o: _money(o.total)),
                ],
            },
            {
                "slug": "quotations",
                "title": "Supplier quotations",
                "model": Quotation,
                "form": forms.QuotationForm,
                "search": ["number", "supplier__name", "requisition__title"],
                "columns": [
                    ("Number", lambda o: o.number or "-"),
                    ("Requisition", lambda o: _text(o.requisition)),
                    ("Supplier", lambda o: _text(o.supplier)),
                    ("Amount", lambda o: _money(o.amount)),
                    ("Valid until", lambda o: _date(o.valid_until)),
                    ("Status", _choice("status")),
                    ("Awarded by", lambda o: _user(o.awarded_by)),
                ],
            },
            {
                "slug": "quotation-lines",
                "title": "Quotation lines",
                "model": QuotationLine,
                "form": forms.QuotationLineForm,
                "search": ["description", "quotation__number", "quotation__supplier__name"],
                "columns": [
                    ("Quotation", lambda o: _text(o.quotation)),
                    ("Description", lambda o: o.description),
                    ("Quantity", lambda o: o.quantity),
                    ("Unit price", lambda o: _money(o.unit_price)),
                    ("Total", lambda o: _money(o.total)),
                ],
            },
            {
                "slug": "orders",
                "title": "Purchase orders",
                "model": PurchaseOrder,
                "form": forms.PurchaseOrderForm,
                "search": ["number", "supplier__name"],
                "columns": [
                    ("Number", lambda o: o.number or "-"),
                    ("Supplier", lambda o: _text(o.supplier)),
                    ("Ordered", lambda o: _date(o.order_date)),
                    ("Expected", lambda o: _date(o.expected_on)),
                    ("Status", _choice("status")),
                    ("Total", lambda o: _money(o.total)),
                ],
            },
            {
                "slug": "order-lines",
                "title": "Purchase order lines",
                "model": PurchaseOrderLine,
                "form": forms.PurchaseOrderLineForm,
                "search": ["description", "order__number"],
                "columns": [
                    ("Order", lambda o: _text(o.order)),
                    ("Description", lambda o: o.description),
                    ("Quantity", lambda o: o.quantity),
                    ("Unit price", lambda o: _money(o.unit_price)),
                    ("Total", lambda o: _money(o.total)),
                ],
            },
            {
                "slug": "receipts",
                "title": "Goods received",
                "model": GoodsReceivedNote,
                "form": forms.GoodsReceivedNoteForm,
                "search": ["reference", "order__number"],
                "columns": [
                    ("Order", lambda o: _text(o.order)),
                    ("Received on", lambda o: _date(o.received_on)),
                    ("Reference", lambda o: o.reference or "-"),
                    ("Received by", lambda o: _user(o.received_by)),
                ],
            },
            {
                "slug": "supplier-invoices",
                "title": "Supplier invoices",
                "model": SupplierInvoice,
                "form": forms.SupplierInvoiceForm,
                "search": ["number", "supplier__name", "description", "order__number"],
                "row_action": ("operations:supplier_invoice_match", "Match"),
                "columns": [
                    ("Number", lambda o: o.number),
                    ("Supplier", lambda o: _text(o.supplier)),
                    ("Order", lambda o: _text(o.order) or "-"),
                    ("Invoice date", lambda o: _date(o.invoice_date)),
                    ("Due", lambda o: _date(o.due_date)),
                    ("Amount", lambda o: _money(o.amount)),
                    ("Variance", lambda o: _money(o.variance)),
                    ("Status", _choice("status")),
                ],
            },
            {
                "slug": "stock",
                "title": "Stock items",
                "model": StockItem,
                "form": forms.StockItemForm,
                "search": ["code", "name", "category"],
                "columns": [
                    ("Code", lambda o: o.code),
                    ("Item", lambda o: o.name),
                    ("Quantity", lambda o: f"{o.quantity} {o.unit}"),
                    ("Reorder at", lambda o: o.reorder_level),
                    ("Low", lambda o: "Yes" if o.is_low else "No"),
                    ("Value", lambda o: _money(o.value)),
                ],
            },
            {
                "slug": "stock-movements",
                "title": "Stock movements",
                "model": StockMovement,
                "form": forms.StockMovementForm,
                "search": ["note", "item__name", "item__code"],
                "columns": [
                    ("Item", lambda o: _text(o.item)),
                    ("Kind", _choice("kind")),
                    ("Quantity", lambda o: o.quantity),
                    ("Note", lambda o: o.note or "-"),
                    ("When", lambda o: _datetime(o.created)),
                ],
            },
            {
                "slug": "assets",
                "title": "Assets",
                "model": Asset,
                "form": forms.AssetForm,
                "search": ["name", "tag", "category"],
                "columns": [
                    ("Asset", lambda o: o.name),
                    ("Tag", lambda o: o.tag or "-"),
                    ("Category", lambda o: o.category or "-"),
                    ("Assigned to", lambda o: _user(o.assigned_to)),
                    ("Cost", lambda o: _money(o.cost)),
                    ("Status", _choice("status")),
                ],
            },
        ],
    },
    "estates": {
        "title": "Estates and maintenance",
        "description": "Work orders, cleaning, utility readings and maintenance.",
        "permissions": ("estates.manage",),
        "kinds": [
            {
                "slug": "work-orders",
                "title": "Work orders",
                "model": WorkOrder,
                "form": forms.WorkOrderForm,
                "search": ["title", "location", "category"],
                "columns": [
                    ("Work order", lambda o: o.title),
                    ("Location", lambda o: o.location),
                    ("Priority", _choice("priority")),
                    ("Status", _choice("status")),
                    ("Assigned to", lambda o: _user(o.assigned_to)),
                    ("Opened", lambda o: _date(o.opened_on)),
                ],
            },
            {
                "slug": "cleaning",
                "title": "Cleaning schedules",
                "model": CleaningSchedule,
                "form": forms.CleaningScheduleForm,
                "search": ["area", "notes"],
                "columns": [
                    ("Area", lambda o: o.area),
                    ("Frequency", _choice("frequency")),
                    ("Assigned to", lambda o: _user(o.assigned_to)),
                    ("Last done", lambda o: _date(o.last_done)),
                    ("Next due", lambda o: _date(o.next_due)),
                ],
            },
            {
                "slug": "utilities",
                "title": "Utility readings",
                "model": UtilityReading,
                "form": forms.UtilityReadingForm,
                "search": ["meter_name", "notes"],
                "columns": [
                    ("Meter", lambda o: o.meter_name),
                    ("Kind", _choice("kind")),
                    ("Date", lambda o: _date(o.reading_date)),
                    ("Reading", lambda o: o.reading),
                    ("Cost", lambda o: _money(o.cost)),
                ],
            },
            {
                "slug": "maintenance",
                "title": "Preventive maintenance",
                "model": MaintenancePlan,
                "form": forms.MaintenancePlanForm,
                "search": ["title", "instructions"],
                "columns": [
                    ("Plan", lambda o: o.title),
                    ("Asset", lambda o: _text(o.asset) or "-"),
                    ("Every", lambda o: f"{o.frequency_days} days"),
                    ("Last done", lambda o: _date(o.last_done)),
                    ("Next due", lambda o: _date(o.next_due)),
                    ("Active", lambda o: _yes(o.is_active)),
                ],
            },
        ],
    },
    "security": {
        "title": "Security",
        "description": "Visitors, gate passes, student sign-out and incidents.",
        "permissions": ("security.manage",),
        "kinds": [
            {
                "slug": "visitors",
                "title": "Visitor log",
                "model": Visitor,
                "form": forms.VisitorForm,
                "search": ["full_name", "host", "purpose"],
                "columns": [
                    ("Visitor", lambda o: o.full_name),
                    ("Host", lambda o: o.host or "-"),
                    ("Purpose", lambda o: o.purpose or "-"),
                    ("In", lambda o: _datetime(o.time_in)),
                    ("Out", lambda o: _datetime(o.time_out)),
                    ("On site", lambda o: _yes(o.is_on_site)),
                ],
            },
            {
                "slug": "gate-passes",
                "title": "Gate passes",
                "model": GatePass,
                "form": forms.GatePassForm,
                "search": ["person_name", "item", "purpose"],
                "columns": [
                    ("Person", lambda o: o.person_name),
                    ("Item", lambda o: o.item),
                    ("Issued", lambda o: _datetime(o.issued_at)),
                    ("Due back", lambda o: _datetime(o.due_back)),
                    ("Status", _choice("status")),
                ],
            },
            {
                "slug": "sign-outs",
                "title": "Student sign-out",
                "model": StudentSignOut,
                "form": forms.StudentSignOutForm,
                "search": ["student__user__last_name", "reason"],
                "columns": [
                    ("Student", lambda o: _staff(o.student)),
                    ("Reason", lambda o: o.reason),
                    ("Out", lambda o: _datetime(o.out_at)),
                    ("Expected back", lambda o: _datetime(o.expected_back)),
                    ("Returned", lambda o: _datetime(o.returned_at)),
                ],
            },
            {
                "slug": "incidents",
                "title": "Incidents",
                "model": Incident,
                "form": forms.IncidentForm,
                "search": ["location", "category", "description"],
                "columns": [
                    ("Date", lambda o: _date(o.date)),
                    ("Location", lambda o: o.location),
                    ("Category", lambda o: o.category),
                    ("Severity", _choice("severity")),
                    ("Status", _choice("status")),
                ],
            },
        ],
    },
    "compliance": {
        "title": "Compliance and quality",
        "description": "Policies, acknowledgements, audits, risks and data requests.",
        "permissions": ("compliance.manage",),
        "kinds": [
            {
                "slug": "policies",
                "title": "Policy library",
                "model": Policy,
                "form": forms.PolicyForm,
                "search": ["title", "code", "category"],
                "row_action": ("operations:policy_acknowledge", "Acknowledge"),
                "columns": [
                    ("Code", lambda o: o.code),
                    ("Policy", lambda o: o.title),
                    ("Version", lambda o: o.version),
                    ("Effective", lambda o: _date(o.effective_date)),
                    ("Review", lambda o: _date(o.review_date)),
                    ("Active", lambda o: _yes(o.is_active)),
                ],
            },
            {
                "slug": "acknowledgements",
                "title": "Acknowledgements",
                "model": PolicyAcknowledgement,
                "form": forms.PolicyAcknowledgementForm,
                "search": ["policy__title", "user__last_name"],
                "columns": [
                    ("Policy", lambda o: _text(o.policy)),
                    ("User", lambda o: _user(o.user)),
                    ("When", lambda o: _datetime(o.acknowledged_at)),
                ],
            },
            {
                "slug": "audits",
                "title": "Compliance audits",
                "model": ComplianceAudit,
                "form": forms.ComplianceAuditForm,
                "search": ["title", "area", "findings"],
                "columns": [
                    ("Audit", lambda o: o.title),
                    ("Area", lambda o: o.area),
                    ("Date", lambda o: _date(o.audit_date)),
                    ("Score", lambda o: _money(o.score)),
                    ("Status", _choice("status")),
                ],
            },
            {
                "slug": "risks",
                "title": "Risk register",
                "model": Risk,
                "form": forms.RiskForm,
                "search": ["title", "category", "mitigation"],
                "columns": [
                    ("Risk", lambda o: o.title),
                    ("Likelihood", lambda o: o.likelihood),
                    ("Impact", lambda o: o.impact),
                    ("Score", lambda o: o.score),
                    ("Rating", lambda o: o.rating),
                    ("Status", _choice("status")),
                ],
            },
            {
                "slug": "data-requests",
                "title": "Data protection requests",
                "model": DataRequest,
                "form": forms.DataRequestForm,
                "search": ["requester_name", "email", "details"],
                "columns": [
                    ("Requester", lambda o: o.requester_name),
                    ("Kind", _choice("kind")),
                    ("Received", lambda o: _date(o.received_on)),
                    ("Due", lambda o: _date(o.due_on)),
                    ("Status", _choice("status")),
                ],
            },
        ],
    },
    "marketing": {
        "title": "Marketing and enquiries",
        "description": "Enquiries, campaigns, open days and newsletters.",
        "permissions": ("marketing.manage",),
        "kinds": [
            {
                "slug": "enquiries",
                "title": "Enquiries",
                "model": Enquiry,
                "form": forms.EnquiryForm,
                "search": ["name", "email", "phone", "interest"],
                "columns": [
                    ("Name", lambda o: o.name),
                    ("Source", lambda o: o.source or "-"),
                    ("Interest", lambda o: o.interest or "-"),
                    ("Status", _choice("status")),
                    ("Follow up", lambda o: _date(o.follow_up_on)),
                ],
            },
            {
                "slug": "campaigns",
                "title": "Campaigns",
                "model": Campaign,
                "form": forms.CampaignForm,
                "search": ["name", "description"],
                "columns": [
                    ("Campaign", lambda o: o.name),
                    ("Kind", _choice("kind")),
                    ("Starts", lambda o: _date(o.start_date)),
                    ("Ends", lambda o: _date(o.end_date)),
                    ("Budget", lambda o: _money(o.budget)),
                    ("Status", _choice("status")),
                ],
            },
            {
                "slug": "open-days",
                "title": "Open days",
                "model": OpenDay,
                "form": forms.OpenDayForm,
                "search": ["title", "venue"],
                "columns": [
                    ("Open day", lambda o: o.title),
                    ("Date", lambda o: _date(o.date)),
                    ("Venue", lambda o: o.venue or "-"),
                    ("Capacity", lambda o: o.capacity or "-"),
                ],
            },
            {
                "slug": "newsletters",
                "title": "Newsletters",
                "model": Newsletter,
                "form": forms.NewsletterForm,
                "search": ["title", "subject", "body"],
                "row_action": ("operations:newsletter_send", "Send"),
                "columns": [
                    ("Newsletter", lambda o: o.title),
                    ("Subject", lambda o: o.subject or "-"),
                    ("Status", _choice("status")),
                    ("Scheduled", lambda o: _datetime(o.scheduled_for)),
                    ("Sent", lambda o: _datetime(o.sent_at)),
                ],
            },
        ],
    },
    "governance": {
        "title": "Governance",
        "description": "Meetings, resolutions, action items and levies.",
        "permissions": ("governance.manage",),
        "kinds": [
            {
                "slug": "meetings",
                "title": "Meetings",
                "model": Meeting,
                "form": forms.MeetingForm,
                "search": ["title", "location", "agenda"],
                "columns": [
                    ("Meeting", lambda o: o.title),
                    ("Kind", _choice("kind")),
                    ("Date", lambda o: _date(o.date)),
                    ("Time", lambda o: o.time.strftime("%H:%M") if o.time else "-"),
                    ("Status", _choice("status")),
                ],
            },
            {
                "slug": "resolutions",
                "title": "Resolutions",
                "model": Resolution,
                "form": forms.ResolutionForm,
                "search": ["title", "body", "meeting__title"],
                "columns": [
                    ("Resolution", lambda o: o.title),
                    ("Meeting", lambda o: _text(o.meeting)),
                    ("Status", _choice("status")),
                    ("For", lambda o: o.votes_for),
                    ("Against", lambda o: o.votes_against),
                ],
            },
            {
                "slug": "actions",
                "title": "Action items",
                "model": ActionItem,
                "form": forms.ActionItemForm,
                "search": ["title", "meeting__title"],
                "columns": [
                    ("Action", lambda o: o.title),
                    ("Meeting", lambda o: _text(o.meeting)),
                    ("Owner", lambda o: _user(o.owner)),
                    ("Due", lambda o: _date(o.due_date)),
                    ("Status", _choice("status")),
                ],
            },
            {
                "slug": "levies",
                "title": "Levies",
                "model": GovernanceLevy,
                "form": forms.GovernanceLevyForm,
                "search": ["name", "description"],
                "columns": [
                    ("Levy", lambda o: o.name),
                    ("Amount", lambda o: _money(o.amount)),
                    ("Due", lambda o: _date(o.due_date)),
                    ("Active", lambda o: _yes(o.is_active)),
                ],
            },
        ],
    },
}


def get_module(code):
    info = MODULES.get(code)
    if info is None:
        return None
    return {**info, "code": code, "label": module_label(code)}


def get_kind(module_code, slug):
    module = get_module(module_code)
    if module is None:
        return None
    for kind in module["kinds"]:
        if kind["slug"] == slug:
            return {**kind, "module": module, "slug": slug}
    return None


def object_count(kind):
    return kind["model"].objects.count()
