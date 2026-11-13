from django.urls import path

from core.registers import build_urls

from . import views
from .registry import MODULES

app_name = "examinations"
urlpatterns = [
    path("clash-check/", views.clash_check, name="clash_check"),
    path("candidates/export.csv", views.candidate_export, name="candidate_export"),
    path(
        "exports/zimsec.csv",
        views.board_export,
        {"board": "zimsec"},
        name="zimsec_export",
    ),
    path(
        "exports/hexco.csv",
        views.board_export,
        {"board": "hexco"},
        name="hexco_export",
    ),
    path("exams/<int:pk>/", views.exam_manage, name="exam_manage"),
    path("exams/<int:pk>/release/", views.exam_release, name="exam_release"),
    path("papers/<int:pk>/seating/", views.seating_plan, name="seating_plan"),
    path(
        "papers/<int:pk>/<slug:action>/",
        views.paper_action,
        name="paper_action",
    ),
] + build_urls("examinations", MODULES, app_name)
