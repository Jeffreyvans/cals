from core.forms import form_for

from .models import (
    EthicsApplication,
    Milestone,
    Publication,
    ResearchProject,
    SupervisionMeeting,
    Thesis,
)

ResearchProjectForm = form_for(ResearchProject)
MilestoneForm = form_for(Milestone)
ThesisForm = form_for(Thesis)
SupervisionMeetingForm = form_for(SupervisionMeeting, exclude=["attended_by"])
EthicsApplicationForm = form_for(EthicsApplication)
PublicationForm = form_for(Publication)
