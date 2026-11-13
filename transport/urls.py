from django.urls import path

from core.registers import build_urls

from . import views
from .registry import MODULES

app_name = "transport"
urlpatterns = [
    path("pickup-list/", views.pickup_list, name="pickup_list"),
    path("pickup-list.csv", views.pickup_export, name="pickup_export"),
    path("fuel-report/", views.fuel_report, name="fuel_report"),
    path("fuel-report.csv", views.fuel_export, name="fuel_export"),
] + build_urls("transport", MODULES, app_name)

