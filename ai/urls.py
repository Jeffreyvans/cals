"""Institution-facing AI control screens."""

from django.urls import path

from . import views

app_name = "ai"

urlpatterns = [
    path("", views.home, name="home"),
    path("consent/", views.consent, name="consent"),
    path("settings/", views.settings_view, name="settings"),
    path("usage/", views.usage, name="usage"),
]
