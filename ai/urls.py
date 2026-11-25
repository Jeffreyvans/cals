"""Institution-facing AI control screens."""

from django.urls import path

from . import feature_views, views

app_name = "ai"

urlpatterns = [
    path("", views.home, name="home"),
    path("consent/", views.consent, name="consent"),
    path("settings/", views.settings_view, name="settings"),
    path("usage/", views.usage, name="usage"),
    path("assistant/", feature_views.help_assistant, name="help"),
    path("draft/", feature_views.draft_view, name="draft"),
    path("translation/", feature_views.translation_view, name="translation"),
    path("insights/", feature_views.insights_view, name="insights"),
    path("search/", feature_views.search_view, name="search"),
]
