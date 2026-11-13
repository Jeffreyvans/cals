from django.urls import path

from core.registers import build_urls

from . import views
from .registry import MODULES

app_name = "boarding"
urlpatterns = [
    path("allocate/", views.allocate, name="allocate"),
] + build_urls("boarding", MODULES, app_name)

