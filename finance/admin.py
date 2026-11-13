from django.contrib import admin

from core.admin import TenantAdminMixin

from .models import (
    DebitNote,
    DocumentSequence,
    ExchangeRate,
    Invoice,
    MoneyLog,
    Payment,
    TaxRate,
    Waiver,
)


class ImmutableDocumentAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Issued documents can be added and read, never changed or deleted.

    A correction is a new credit note, debit note or reversing entry, so the
    admin refuses both the change form and the delete action.
    """

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(ImmutableDocumentAdmin):
    list_display = ("number", "description", "student", "amount", "tax_amount",
                    "due_date", "created_by")
    search_fields = ("number", "description", "student__name",
                     "student__user__username")
    list_filter = ("tax_category",)
    readonly_fields = ("created", "created_by", "number", "tax_category",
                       "tax_rate", "tax_amount", "currency", "exchange_rate",
                       "base_amount")

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Payment)
class PaymentAdmin(ImmutableDocumentAdmin):
    list_display = ("receipt_no", "invoice", "amount", "method", "paid_on",
                    "received_by")
    search_fields = ("receipt_no", "invoice__description",
                     "invoice__student__name")
    readonly_fields = ("paid_on", "receipt_no", "currency", "exchange_rate",
                       "base_amount")


@admin.register(Waiver)
class WaiverAdmin(ImmutableDocumentAdmin):
    list_display = ("note_no", "invoice", "amount", "reason", "created",
                    "recorded_by")
    search_fields = ("note_no", "reason", "invoice__description")
    readonly_fields = ("created", "note_no")


@admin.register(DebitNote)
class DebitNoteAdmin(ImmutableDocumentAdmin):
    list_display = ("note_no", "invoice", "amount", "reason", "created",
                    "recorded_by")
    search_fields = ("note_no", "reason", "invoice__description")
    readonly_fields = ("created", "note_no")


@admin.register(TaxRate)
class TaxRateAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "category", "rate", "effective_from", "effective_to")
    list_filter = ("category",)


@admin.register(ExchangeRate)
class ExchangeRateAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("currency", "rate", "effective_from")
    list_filter = ("currency",)


@admin.register(DocumentSequence)
class DocumentSequenceAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Read-only: the numbers are advanced by the application, never by hand."""

    list_display = ("document_type", "financial_year", "last_number", "updated")
    list_filter = ("document_type",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MoneyLog)
class MoneyLogAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Append-only: the trail can be read but never touched."""
    list_display = ("created", "actor_name", "action", "description",
                    "student", "reference", "amount")
    list_filter = ("action",)
    search_fields = ("description", "reference", "student__name")
    ordering = ("-created", "-pk")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
