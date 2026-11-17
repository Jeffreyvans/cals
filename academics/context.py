from django.conf import settings

from core.cache import branding_for, modules_for, terminology_for

from .models import AcademicYear, SchoolSettings, Term


def institution(request):
    """Values every template needs: the institution, the mode and the period."""
    from core.cache import school_settings_for
    from core.tenancy import current_institution_id

    inst = getattr(request, "institution", None)
    institution_id = inst.pk if inst is not None else current_institution_id()
    mode = school_settings_for(institution_id) or SchoolSettings.Mode.COLLEGE
    is_school = mode == SchoolSettings.Mode.SCHOOL
    unread = 0
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        from core.notifications import unread_count

        unread = unread_count(user)

    return {
        "INSTITUTION": inst.name if inst is not None else settings.INSTITUTION_NAME,
        "INSTITUTION_OBJECT": inst,
        "CUR": inst.currency if inst is not None else settings.CURRENCY,
        "CURRENCY": inst.currency if inst is not None else settings.CURRENCY,
        "MODE": mode,
        "IS_SCHOOL": is_school,
        "CURRENT_YEAR": AcademicYear.objects.filter(is_current=True).first(),
        "CURRENT_TERM": Term.objects.filter(is_current=True).first(),
        "UNREAD_COUNT": unread,
        "ENABLED_MODULES": modules_for(inst),
        "TERMS": terminology_for(inst),
        "BRAND": branding_for(inst) or branding_for_empty(),
        "LOW_DATA": bool(getattr(request, "low_data", False)),
    }


def branding_for_empty():
    from core.branding import brand_variables

    return brand_variables(None)
