from django.urls import path

from core.registers import build_urls

from . import views
from .registry import MODULES

app_name = "health"
urlpatterns = [
    path("safe-alerts/", views.safe_alerts, name="safe_alerts"),
] + build_urls("health", MODULES, app_name)
