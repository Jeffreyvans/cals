from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from core.admin import TenantAdminMixin

from .admin_forms import StrongAdminPasswordChangeForm, StrongUserCreationForm
from .models import User


@admin.register(User)
class AppUserAdmin(TenantAdminMixin, UserAdmin):
    add_form = StrongUserCreationForm
    change_password_form = StrongAdminPasswordChangeForm
    fieldsets = UserAdmin.fieldsets + (
        ("School role", {"fields": ("role", "phone", "institution", "primary_role", "extra_roles")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("School role", {"fields": ("role", "phone", "institution")}),
    )
    list_display = ("username", "get_full_name", "role", "institution", "is_active")
    list_filter = ("role", "is_active", "institution")
    filter_horizontal = ("extra_roles",)
