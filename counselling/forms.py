from core.forms import form_for

from .models import CounsellingSession, Referral

CounsellingSessionForm = form_for(CounsellingSession, exclude=["counsellor"])
ReferralForm = form_for(Referral, exclude=["referred_by"])
