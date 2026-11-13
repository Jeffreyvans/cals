from core.forms import form_for

from .models import Equipment, EquipmentLoan, Fixture, HousePoint, Team, TeamMember

TeamForm = form_for(Team)
TeamMemberForm = form_for(TeamMember)
FixtureForm = form_for(Fixture)
HousePointForm = form_for(HousePoint, exclude=["awarded_by"])
EquipmentForm = form_for(Equipment)
EquipmentLoanForm = form_for(EquipmentLoan, exclude=["issued_by"])
