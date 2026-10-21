"""Admin helpers for tenant models.

The institution is never chosen by hand in the admin: it follows the signed-in
staff member, and the list of rows is narrowed to that institution.
"""

from django.contrib import admin


class TenantAdminMixin:
    """Narrow a tenant model to the current institution and stamp new rows."""

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if "institution" not in fields:
            fields.append("institution")
        return fields

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        user = request.user
        if user.is_superuser:
            return qs
        institution = getattr(request, "institution", None) or getattr(user, "institution", None)
        if institution is None:
            return qs.none()
        return qs.filter(institution=institution)

    def save_model(self, request, obj, form, change):
        institution = getattr(request, "institution", None)
        if institution is None:
            institution = getattr(request.user, "institution", None)
        if institution is not None:
            obj.institution = institution
        super().save_model(request, obj, form, change)


class TenantModelAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Ready-to-use admin for a tenant model."""


def tenant_qs(request, queryset):
    """Filter a queryset to the request's institution unless the user is root."""
    user = getattr(request, "user", None)
    if user is not None and user.is_superuser:
        return queryset
    institution = getattr(request, "institution", None)
    if institution is None and user is not None:
        institution = getattr(user, "institution", None)
    if institution is None:
        return queryset.none()
    return queryset.filter(institution=institution)
