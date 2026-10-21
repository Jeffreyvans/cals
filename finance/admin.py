from django.contrib import admin

from core.admin import TenantAdminMixin

from .models import Invoice, MoneyLog, Payment, Waiver


@admin.register(Invoice)
class InvoiceAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("description", "student", "amount", "due_date", "created_by")
    search_fields = ("description", "student__name", "student__user__username")
    readonly_fields = ("created", "created_by")

    def save_model(self, request, obj, form, change):
        changed = [f for f in ("amount", "description") if f in form.changed_data]
        old = Invoice.objects.get(pk=obj.pk) if change and changed else None
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if old is None:
            return
        if {"amount", "description"} <= set(changed):
            detail = (f"Invoice edited: '{old.description}' {old.amount} -> "
                      f"'{obj.description}' {obj.amount}")
        elif "amount" in changed:
            detail = f"Invoice edited: amount {old.amount} -> {obj.amount}"
        else:
            detail = f"Invoice edited: renamed from '{old.description}'"
        MoneyLog.objects.create(action=MoneyLog.Action.ADJUST,
            amount=obj.amount, student=obj.student, invoice=obj,
            actor=request.user, description=detail[:200])

    def _log_removed(self, request, obj):
        extras = []
        n_pay = obj.payments.count()
        n_waive = obj.waivers.count()
        if n_pay:
            extras.append(f"{n_pay} payment{'s' if n_pay != 1 else ''}")
        if n_waive:
            extras.append(f"{n_waive} credit note{'s' if n_waive != 1 else ''}")
        tail = f" (incl. {', '.join(extras)})" if extras else ""
        MoneyLog.objects.create(action=MoneyLog.Action.REMOVE,
            amount=obj.amount, student=obj.student, invoice=obj,
            actor=request.user,
            description=f"Deleted invoice '{obj.description}'{tail}"[:200])

    def delete_model(self, request, obj):
        self._log_removed(request, obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self._log_removed(request, obj)
        super().delete_queryset(request, queryset)


@admin.register(Payment)
class PaymentAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("receipt_no", "invoice", "amount", "method", "paid_on",
                    "received_by")
    search_fields = ("receipt_no", "invoice__description",
                     "invoice__student__name")
    readonly_fields = ("paid_on", "receipt_no")

    def save_model(self, request, obj, form, change):
        changed = [f for f in ("amount", "invoice") if f in form.changed_data]
        old = Payment.objects.get(pk=obj.pk) if change and changed else None
        super().save_model(request, obj, form, change)
        if old is None:
            return
        if {"amount", "invoice"} <= set(changed):
            detail = (f"Payment edited: {old.amount} on '{old.invoice}' -> "
                      f"{obj.amount} on '{obj.invoice}'")
        elif "amount" in changed:
            detail = f"Payment edited: amount {old.amount} -> {obj.amount}"
        else:
            detail = f"Payment edited: moved from '{old.invoice}' to '{obj.invoice}'"
        MoneyLog.objects.create(action=MoneyLog.Action.ADJUST,
            amount=obj.amount, student=obj.invoice.student,
            invoice=obj.invoice, actor=request.user,
            reference=obj.receipt_no, description=detail[:200])

    def _log_removed(self, request, obj):
        MoneyLog.objects.create(action=MoneyLog.Action.REMOVE,
            amount=obj.amount, student=obj.invoice.student,
            invoice=obj.invoice, actor=request.user,
            reference=obj.receipt_no,
            description=f"Deleted payment against '{obj.invoice.description}'"[:200])

    def delete_model(self, request, obj):
        self._log_removed(request, obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self._log_removed(request, obj)
        super().delete_queryset(request, queryset)


@admin.register(Waiver)
class WaiverAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("note_no", "invoice", "amount", "reason", "created",
                    "recorded_by")
    search_fields = ("note_no", "reason", "invoice__description")
    readonly_fields = ("created", "note_no")

    def save_model(self, request, obj, form, change):
        changed = [f for f in ("amount", "reason") if f in form.changed_data]
        old = Waiver.objects.get(pk=obj.pk) if change and changed else None
        super().save_model(request, obj, form, change)
        if old is None:
            return
        if {"amount", "reason"} <= set(changed):
            detail = (f"Credit note edited: {old.amount} '{old.reason}' -> "
                      f"{obj.amount} '{obj.reason}'")
        elif "amount" in changed:
            detail = f"Credit note edited: amount {old.amount} -> {obj.amount}"
        else:
            detail = f"Credit note edited: reason changed from '{old.reason}'"
        MoneyLog.objects.create(action=MoneyLog.Action.ADJUST,
            amount=obj.amount, student=obj.invoice.student,
            invoice=obj.invoice, actor=request.user,
            reference=obj.note_no, description=detail[:200])

    def _log_removed(self, request, obj):
        MoneyLog.objects.create(action=MoneyLog.Action.REMOVE,
            amount=obj.amount, student=obj.invoice.student,
            invoice=obj.invoice, actor=request.user,
            reference=obj.note_no,
            description=f"Deleted credit note: {obj.reason}"[:200])

    def delete_model(self, request, obj):
        self._log_removed(request, obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self._log_removed(request, obj)
        super().delete_queryset(request, queryset)


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
