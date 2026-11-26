from django.urls import path

from enterprise import views

app_name = "enterprise"

urlpatterns = [
    # Platform administration of organisations.
    path("platform/groups/", views.organisation_list, name="organisation_list"),
    path("platform/groups/new/", views.organisation_create, name="organisation_create"),
    path("platform/groups/<int:pk>/", views.organisation_detail, name="organisation_detail"),
    # Group sign-in and dashboards.
    path("g/<slug:slug>/", views.group_login, name="group_login"),
    path("g/<slug:slug>/dashboard/", views.group_dashboard, name="group_dashboard"),
    path("g/<slug:slug>/switch/", views.group_switch_campus, name="group_switch_campus"),
    path("g/<slug:slug>/students/", views.group_students, name="group_students"),
    path("g/<slug:slug>/request-access/", views.group_request_access,
         name="group_request_access"),
    path("g/<slug:slug>/settings/", views.group_settings, name="group_settings"),
    path("g/<slug:slug>/settings/save/", views.group_setting_save, name="group_setting_save"),
    path("g/<slug:slug>/campus/<int:campus_id>/settings/",
         views.campus_settings, name="campus_settings"),
    path("g/<slug:slug>/campus/<int:campus_id>/settings/save/",
         views.campus_setting_save, name="campus_setting_save"),
    path("g/<slug:slug>/billing/", views.group_billing, name="group_billing"),
    path("g/<slug:slug>/export/", views.group_export, name="group_export"),
]
