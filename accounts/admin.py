from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User

@admin.register(User)
class AppUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("School role", {"fields": ("role", "phone")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("School role", {"fields": ("role", "phone")}),)
    list_display = ("username", "get_full_name", "role", "is_active")
    list_filter = ("role", "is_active")
