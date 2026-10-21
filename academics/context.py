from django.conf import settings
from .models import AcademicYear, SchoolSettings, Term


def institution(request):
    s = SchoolSettings.get()
    return {
        "INSTITUTION": settings.INSTITUTION_NAME,
        "CUR": settings.CURRENCY,
        "MODE": s.mode,
        "IS_SCHOOL": s.is_school,
        "CURRENT_YEAR": AcademicYear.objects.filter(is_current=True).first(),
        "CURRENT_TERM": Term.objects.filter(is_current=True).first(),
    }
