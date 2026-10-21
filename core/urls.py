from django.urls import path

from . import views

urlpatterns = [
    path("settings/institution/", views.settings_view, name="institution_settings"),
    path("settings/roles/", views.role_list, name="role_list"),
    path("settings/roles/matrix/", views.role_matrix, name="role_matrix"),
    path("settings/roles/new/", views.role_create, name="role_create"),
    path("settings/roles/<int:pk>/", views.role_detail, name="role_detail"),
    path("settings/roles/<int:pk>/delete/", views.role_delete, name="role_delete"),
    path("notifications/", views.notifications, name="notifications"),
    path("audit/", views.audit_log, name="audit_trail"),
    path("audit/export/", views.audit_export, name="audit_trail_export"),
    path("audit/sensitive/", views.sensitive_log, name="sensitive_log"),
]
