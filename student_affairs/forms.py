from core.forms import form_for

from .models import Club, ClubMembership, DisciplineCase, Event, EventRegistration

DisciplineCaseForm = form_for(DisciplineCase, exclude=["handled_by"])
ClubForm = form_for(Club)
ClubMembershipForm = form_for(ClubMembership)
EventForm = form_for(Event, exclude=["organiser"])
EventRegistrationForm = form_for(EventRegistration, exclude=["registered_on"])
