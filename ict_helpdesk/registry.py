from core.modules import module_label

from .forms import (
    AccountResetRequestForm,
    DeviceForm,
    SLAPolicyForm,
    SoftwareLicenceForm,
    TicketCommentForm,
    TicketForm,
)
from .models import (
    AccountResetRequest,
    Device,
    SLAPolicy,
    SoftwareLicence,
    Ticket,
    TicketComment,
)

MODULES = {
    "ict_helpdesk": {
        "title": module_label("ict_helpdesk"),
        "description": "Devices, support tickets and software licences.",
        "permissions": ("ict.manage",),
        "kinds": [
            {
                "slug": "devices",
                "title": "Devices",
                "description": "Device inventory.",
                "model": Device,
                "form": DeviceForm,
                "search": ["asset_tag", "model_name", "serial", "location"],
                "columns": [
                    ("Asset tag", lambda o: o.asset_tag),
                    ("Kind", lambda o: o.get_kind_display()),
                    ("Model", lambda o: o.model_name or "-"),
                    ("Assigned to", lambda o: o.assigned_to or "-"),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "tickets",
                "title": "Tickets",
                "description": "ICT support requests.",
                "model": Ticket,
                "form": TicketForm,
                "search": ["title", "description", "category", "requester__username"],
                "columns": [
                    ("Ticket", lambda o: o.title),
                    ("Requester", lambda o: o.requester or "-"),
                    ("Priority", lambda o: o.get_priority_display()),
                    ("Status", lambda o: o.get_status_display()),
                    ("Assigned to", lambda o: o.assigned_to or "-"),
                ],
            },
            {
                "slug": "comments",
                "title": "Ticket comments",
                "description": "Updates on tickets.",
                "model": TicketComment,
                "form": TicketCommentForm,
                "search": ["ticket__title", "body"],
                "columns": [
                    ("Ticket", lambda o: o.ticket),
                    ("Author", lambda o: o.author or "-"),
                    ("Comment", lambda o: o.body),
                    ("Created", lambda o: o.created),
                ],
            },
            {
                "slug": "licences",
                "title": "Software licences",
                "description": "Licence seats and expiry dates.",
                "model": SoftwareLicence,
                "form": SoftwareLicenceForm,
                "search": ["name", "vendor", "key_reference"],
                "columns": [
                    ("Licence", lambda o: o.name),
                    ("Vendor", lambda o: o.vendor or "-"),
                    ("Seats", lambda o: f"{o.seats_used}/{o.seats}"),
                    ("Expires", lambda o: o.expires_on or "-"),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "sla-policies",
                "title": "SLA policies",
                "description": "Response and resolution targets per priority.",
                "model": SLAPolicy,
                "form": SLAPolicyForm,
                "search": ["name", "priority"],
                "columns": [
                    ("Policy", lambda o: o.name),
                    ("Priority", lambda o: o.get_priority_display()),
                    ("Response (min)", lambda o: o.response_minutes),
                    ("Resolution (min)", lambda o: o.resolution_minutes),
                    ("Active", lambda o: "Yes" if o.is_active else "No"),
                ],
            },
            {
                "slug": "account-resets",
                "title": "Account resets",
                "description": "Password reset requests and their outcome.",
                "model": AccountResetRequest,
                "form": AccountResetRequestForm,
                "row_action": ("ict_helpdesk:reset_manage", "Manage"),
                "search": ["user__username", "reason", "ticket__title"],
                "columns": [
                    ("User", lambda o: o.user),
                    ("Requested by", lambda o: o.requested_by or "-"),
                    ("Reason", lambda o: o.reason or "-"),
                    ("Status", lambda o: o.get_status_display()),
                    ("Created", lambda o: o.created),
                ],
            },
        ],
    }
}
