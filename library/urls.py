from django.urls import path

from core.registers import build_urls

from . import views
from .registry import MODULES

app_name = "library"
urlpatterns = [
    path("lookup/", views.lookup, name="lookup"),
    path("lookup/qr/", views.qr_lookup, name="qr_lookup"),
    path("books/<int:pk>/qr.png", views.book_qr_png, name="book_qr_png"),
    path("loans/<int:pk>/", views.loan_manage, name="loan_manage"),
    path("loans/<int:pk>/renew/", views.loan_renew, name="loan_renew"),
    path("loans/<int:pk>/fine/", views.loan_fine, name="loan_fine"),
] + build_urls("library", MODULES, app_name)
