from django.urls import path

from . import admin_views

urlpatterns = [
    path("", admin_views.api_keys, name="api_keys"),
    path("webhooks/", admin_views.webhooks, name="api_webhooks"),
    path("webhooks/<int:pk>/", admin_views.webhook_detail, name="api_webhook_detail"),
    path("webhooks/<int:pk>/delete/", admin_views.webhook_delete, name="api_webhook_delete"),
]
