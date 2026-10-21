from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path, include
urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("finance/", include("finance.urls")),
    path("", include("academics.urls")),
]
