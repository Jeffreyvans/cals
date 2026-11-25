"""Shared setup for the AI control and privacy tests."""

from django.contrib.auth import get_user_model

from ai.features import ALL_FEATURES, CONSENT_VERSION, Feature, disclosure_for
from ai.models import AIConsent, AIFeatureSetting, AIPlatformSetting
from core.models import Institution
from core.roles import ensure_roles
from core.tenancy import use_institution

User = get_user_model()


def open_platform():
    setting = AIPlatformSetting.load()
    setting.enabled = True
    setting.save()
    return setting


def switch_on(institution, feature=Feature.HELP, **limits):
    defaults = {
        "enabled": True,
        "institution_daily_limit": 100,
        "institution_monthly_limit": 1000,
        "user_daily_limit": 20,
        "user_monthly_limit": 200,
    }
    defaults.update(limits)
    setting, _ = AIFeatureSetting.all_objects.update_or_create(
        institution=institution, feature=feature, defaults=defaults
    )
    return setting


def accept(user, institution, feature=ALL_FEATURES):
    disclosure = disclosure_for(feature)
    return AIConsent.all_objects.create(
        institution=institution,
        accepted_by=user,
        feature=feature,
        version=CONSENT_VERSION,
        shared_categories=disclosure["shared"],
        recorded_categories=["User id, feature, tokens, cost estimate, time and summary"],
        purpose=disclosure["purpose"],
    )


def make_school(name, slug):
    school = Institution.objects.create(name=name, slug=slug, type="secondary")
    with use_institution(school):
        ensure_roles(school)
        admin = User.objects.create_user(
            f"ai-{slug}-admin", password="pw123456", role="admin",
            is_staff=True, is_superuser=True, institution=school,
        )
    return school, admin
