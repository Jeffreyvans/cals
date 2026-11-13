from django.contrib import admin

from core.admin import TenantAdminMixin

from .models import FiscalDay, FiscalDevice, FiscalSubmission


@admin.register(FiscalDevice)
class FiscalDeviceAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("device_id", "device_serial", "is_active", "last_fiscal_day_no")
    search_fields = ("device_id", "device_serial")


@admin.register(FiscalDay)
class FiscalDayAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("day", "fiscal_day_no", "status", "opened_at", "closed_at")
    list_filter = ("status",)


@admin.register(FiscalSubmission)
class FiscalSubmissionAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Append-only: what was sent to ZIMRA is never edited by hand."""

    list_display = ("document_number", "document_type", "status",
                    "verification_code", "attempts", "submitted_at")
    list_filter = ("document_type", "status")
    search_fields = ("document_number", "verification_code")
    readonly_fields = ("created", "updated", "submitted_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
