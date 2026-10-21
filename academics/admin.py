from django.contrib import admin
from . import models as m
for model in (m.Programme, m.Course, m.Enrollment, m.Activity, m.Attendance, m.Announcement):
    admin.site.register(model)
@admin.register(m.Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_no", "name", "programme", "status")
    search_fields = ("student_no", "user__first_name", "user__last_name")
