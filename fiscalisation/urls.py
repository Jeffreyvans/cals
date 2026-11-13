from django.urls import path

from . import views

app_name = "fiscalisation"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("day/open/", views.open_day, name="open_day"),
    path("day/close/", views.close_day, name="close_day"),
    path("day/report/", views.day_report, name="day_report"),
    path("submissions/<int:pk>/retry/", views.retry, name="retry"),
    path("submissions/<int:pk>/qr.png", views.qr_image, name="qr"),
]
