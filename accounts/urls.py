"""Sign in, registration, email verification and password reset routes."""

from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import user_views, views
from .password_reset import EduSpherePasswordResetView

urlpatterns = [
    path("", views.EduSphereLandingView.as_view(), name="landing"),
    path("login/", views.EduSphereLoginView.as_view(), name="login"),
    path(
        "i/<slug:slug>/login/",
        views.InstitutionLoginView.as_view(),
        name="institution_login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password-reset/", EduSpherePasswordResetView.as_view(
        template_name="registration/password_reset_form.html",
        success_url=reverse_lazy("password_reset_done"),
    ), name="password_reset"),
    path("password-reset/done/", views.password_reset_done, name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="registration/password_reset_confirm.html",
        success_url=reverse_lazy("password_reset_complete"),
    ), name="password_reset_confirm"),
    path("reset/done/", views.password_reset_complete, name="password_reset_complete"),
    path("register/", views.register_choice, name="register_choice"),
    path("join/<uuid:token>/", views.signup_link, name="signup_link"),
    path("register/student/", views.register_student_view, name="register_student"),
    path("register/staff/", views.register_staff_view, name="register_staff"),
    path("register/staff/sent/", views.register_staff_sent, name="register_staff_sent"),
    path("register/contact/<int:pk>/", views.register_contact, name="register_contact"),
    path(
        "verify-email/<uuid:token>/",
        views.verify_email,
        name="register_verify_email",
    ),
    path("privacy/", views.privacy_notice, name="privacy_notice"),
    path(
        "settings/registration-requests/",
        views.registration_requests,
        name="registration_requests",
    ),
    path(
        "settings/registration-requests/<int:pk>/",
        views.registration_request_detail,
        name="registration_request_detail",
    ),
    path(
        "settings/signup-links/",
        user_views.signup_link_list,
        name="signup_link_list",
    ),
    path(
        "settings/signup-links/<int:pk>/action/",
        user_views.signup_link_action,
        name="signup_link_action",
    ),
    # The administrator Users area. Everything here is tenant-scoped.
    path("users/", user_views.user_list, name="user_list"),
    path("users/add/staff/", user_views.user_add_staff, name="user_add_staff"),
    path("users/add/student/", user_views.user_add_student, name="user_add_student"),
    path("users/created/", user_views.user_created, name="user_created"),
    path("users/link-parent/", user_views.user_link_parent, name="user_link_parent"),
    path("users/duplicates/", user_views.user_duplicates, name="user_duplicates"),
    path("users/<int:pk>/", user_views.user_detail, name="user_detail"),
    path("users/<int:pk>/edit/", user_views.user_edit, name="user_edit"),
    path("users/<int:pk>/action/", user_views.user_action, name="user_action"),
    path(
        "invitation/<uuid:token>/",
        user_views.accept_invitation_view,
        name="accept_invitation",
    ),
    path(
        "password-change/",
        user_views.ForcePasswordChangeView.as_view(),
        name="password_change",
    ),
]
