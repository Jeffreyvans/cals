from core.forms import form_for

from .models import (
    AccountResetRequest,
    Device,
    SLAPolicy,
    SoftwareLicence,
    Ticket,
    TicketComment,
)

DeviceForm = form_for(Device)
TicketForm = form_for(Ticket, exclude=["created", "resolved_at", "first_response_at"])
TicketCommentForm = form_for(TicketComment, exclude=["created"])
SoftwareLicenceForm = form_for(SoftwareLicence)
SLAPolicyForm = form_for(SLAPolicy)
AccountResetRequestForm = form_for(
    AccountResetRequest, exclude=["completed_at", "completed_by", "created"]
)
