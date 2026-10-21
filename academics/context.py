from django.conf import settings

from .models import AcademicYear, SchoolSettings, Term


def institution(request):
    """Values every template needs: the institution, the mode and the period."""
    s = SchoolSettings.get()
    inst = getattr(request, "institution", None)
    unread = 0
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        from core.notifications import unread_count

        unread = unread_count(user)
    from core.modules import enabled_modules

    return {
        "INSTITUTION": inst.name if inst is not None else settings.INSTITUTION_NAME,
        "INSTITUTION_OBJECT": inst,
        "CUR": inst.currency if inst is not None else settings.CURRENCY,
        "CURRENCY": inst.currency if inst is not None else settings.CURRENCY,
        "MODE": s.mode,
        "IS_SCHOOL": s.is_school,
        "CURRENT_YEAR": AcademicYear.objects.filter(is_current=True).first(),
        "CURRENT_TERM": Term.objects.filter(is_current=True).first(),
        "UNREAD_COUNT": unread,
        "ENABLED_MODULES": enabled_modules(inst),
    }
