from django.urls import path

from core.registers import build_urls

from . import views
from .registry import MODULES

app_name = "ict_helpdesk"
urlpatterns = [
    path("account-resets/<int:pk>/", views.reset_manage, name="reset_manage"),
] + build_urls("ict_helpdesk", MODULES, app_name)
