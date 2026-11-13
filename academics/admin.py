from django.contrib import admin

from core.admin import TenantAdminMixin, TenantModelAdmin

from . import models as m

for model in (m.Programme, m.Course, m.Enrollment, m.Activity, m.Attendance,
              m.Announcement, m.CalendarEvent):
    admin.site.register(model, TenantModelAdmin)


@admin.register(m.Student)
class StudentAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("student_no", "name", "programme", "status")
    search_fields = ("student_no", "user__first_name", "user__last_name")
