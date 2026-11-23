"""The shared school/department/level/class filters on the working screens."""

from datetime import date, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.hierarchy import filter_hierarchy, has_hierarchy_filters
from academics.models import (
    AcademicSchool,
    AcademicYear,
    ClassTeacher,
    Enrollment,
    GradeLevel,
    Programme,
    SchoolClass,
    Student,
    Subject,
    Term,
    TimetableEntry,
)
from core.models import Department, Institution, StaffProfile
from core.roles import ensure_roles
from core.tenancy import use_institution
from finance.models import FeeStructure, Invoice, Payment

User = get_user_model()


class HierarchyFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Filter College", slug="filter-college", type="college")
        cls.other = Institution.objects.create(
            name="Other College", slug="other-college", type="college")
        roles = ensure_roles(cls.inst)

        def user(username, role, code):
            row = User.objects.create_user(
                username, password="pw123456", role=role, institution=cls.inst)
            row.primary_role = roles[code]
            row.save(update_fields=["primary_role"])
            return row

        cls.registrar = user("filterreg", "registrar", "registrar")
        cls.admin = user("filteradmin", "admin", "institution_admin")
        cls.bursar = user("filterbursar", "bursar", "bursar")
        cls.teacher_a = user("filterteachera", "lecturer", "lecturer")
        cls.teacher_b = user("filterteacherb", "lecturer", "lecturer")

        with use_institution(cls.inst):
            cls.year = AcademicYear.objects.create(
                name="2026", start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31), is_current=True)
            cls.term = Term.objects.create(
                academic_year=cls.year, name="Term 1",
                start_date=date(2026, 1, 15), end_date=date(2026, 4, 10), is_current=True)
            cls.level_a = GradeLevel.objects.create(
                name="Year 1", code="Y1", kind="primary", order=1)
            cls.level_b = GradeLevel.objects.create(
                name="Year 2", code="Y2", kind="primary", order=2)
            cls.school_a = AcademicSchool.objects.create(
                name="North Faculty", code="NOR")
            cls.school_b = AcademicSchool.objects.create(
                name="South Faculty", code="SOU")
            cls.dept_a = Department.objects.create(
                name="Physics", code="PHY", school=cls.school_a)
            cls.dept_b = Department.objects.create(
                name="Chemistry", code="CHE", school=cls.school_b)
            cls.class_a = SchoolClass.objects.create(
                name="Year 1 A", grade_level=cls.level_a,
                school=cls.school_a, department=cls.dept_a, level="primary")
            cls.class_b = SchoolClass.objects.create(
                name="Year 2 B", grade_level=cls.level_b,
                school=cls.school_b, department=cls.dept_b, level="primary")
            cls.programme = Programme.objects.create(
                name="General Studies", code="GEN", level="primary",
                school=cls.school_a, department=cls.dept_a)
            cls.programme_b = Programme.objects.create(
                name="South Studies", code="GSB", level="primary",
                school=cls.school_b, department=cls.dept_b)

            student_a = User.objects.create_user(
                "filterstudenta", password="pw123456", role="student", institution=cls.inst)
            student_b = User.objects.create_user(
                "filterstudentb", password="pw123456", role="student", institution=cls.inst)
            cls.student_a = Student.objects.create(
                user=student_a, student_no="SA001", programme=cls.programme,
                school=cls.school_a, department=cls.dept_a, school_class=cls.class_a)
            cls.student_b = Student.objects.create(
                user=student_b, student_no="SB001", programme=cls.programme,
                school=cls.school_b, department=cls.dept_b, school_class=cls.class_b)

            staff_a = StaffProfile.objects.create(
                user=cls.teacher_a, employee_no="EA001", job_title="Teacher")
            staff_a.departments.add(cls.dept_a)
            staff_b = StaffProfile.objects.create(
                user=cls.teacher_b, employee_no="EB001", job_title="Teacher")
            staff_b.departments.add(cls.dept_b)
            ClassTeacher.objects.create(
                school_class=cls.class_a, academic_year=cls.year, teacher=cls.teacher_a)
            ClassTeacher.objects.create(
                school_class=cls.class_b, academic_year=cls.year, teacher=cls.teacher_b)

            cls.subject = Subject.objects.create(
                code="MAT", name="Mathematics", level="primary", lecturer=cls.teacher_a)
            Enrollment.objects.create(
                student=cls.student_a, subject=cls.subject, term=cls.term)
            Enrollment.objects.create(
                student=cls.student_b, subject=cls.subject, term=cls.term)
            TimetableEntry.objects.create(
                academic_year=cls.year, term=cls.term, day=1,
                start_time=time(8, 0), end_time=time(8, 45),
                subject=cls.subject, school_class=cls.class_a, lecturer=cls.teacher_a)
            TimetableEntry.objects.create(
                academic_year=cls.year, term=cls.term, day=2,
                start_time=time(8, 0), end_time=time(8, 45),
                subject=cls.subject, school_class=cls.class_b, lecturer=cls.teacher_b)

            cls.fee_a = FeeStructure.objects.create(
                code="FEEA", name="North fees", programme=cls.programme,
                school_class=cls.class_a)
            cls.fee_b = FeeStructure.objects.create(
                code="FEEB", name="South fees", programme=cls.programme_b,
                school_class=cls.class_b)
            for student, amount in ((cls.student_a, Decimal("100.00")),
                                    (cls.student_b, Decimal("200.00"))):
                invoice = Invoice.objects.create(
                    student=student, description="Tuition", amount=amount,
                    due_date=date(2026, 3, 1), created_by=cls.bursar)
                Payment.objects.create(
                    invoice=invoice, amount=Decimal("10.00"), received_by=cls.bursar)

        with use_institution(cls.other):
            other_school = AcademicSchool.objects.create(
                name="Hidden Faculty", code="HID")
            other_programme = Programme.objects.create(
                name="Hidden", code="HID", level="primary", school=other_school)
            other_user = User.objects.create_user(
                "otherstudent", password="pw123456", role="student", institution=cls.other)
            cls.other_student = Student.objects.create(
                user=other_user, student_no="OS001", programme=other_programme,
                school=other_school)

    def test_filter_helper_only_uses_supplied_numeric_filters(self):
        from django.test import RequestFactory

        request = RequestFactory().get("/", {"school": self.school_a.pk, "level": "oops"})
        self.assertTrue(has_hierarchy_filters(request))
        with use_institution(self.inst):
            ids = set(filter_hierarchy(
                Student.objects.all(), request, school_field="school",
                level_field="school_class__grade_level").values_list("pk", flat=True))
        self.assertEqual(ids, {self.student_a.pk})

    def test_student_search_api_honours_each_filter(self):
        self.client.force_login(self.registrar)
        url = reverse("student_search_api")
        for key, value in (("school", self.school_a), ("department", self.dept_a),
                           ("level", self.level_a), ("school_class", self.class_a)):
            response = self.client.get(url, {key: value.pk})
            self.assertEqual([row["id"] for row in response.json()["results"]],
                             [self.student_a.pk], key)
        response = self.client.get(url, {"school": self.school_b.pk, "class": self.class_b.pk})
        self.assertEqual([row["id"] for row in response.json()["results"]],
                         [self.student_b.pk])

        response = self.client.get(url, {"school": self.school_b.pk})
        self.assertNotIn("OS001", str(response.json()))

    def test_staff_and_user_lists_honour_hierarchy_filters(self):
        self.client.force_login(self.admin)
        staff = self.client.get(reverse("staff_list"), {"school": self.school_a.pk})
        self.assertEqual([row.user_id for row in staff.context["rows"]],
                         [self.teacher_a.pk])
        staff = self.client.get(reverse("staff_list"), {"class": self.class_b.pk})
        self.assertEqual([row.user_id for row in staff.context["rows"]],
                         [self.teacher_b.pk])

        users = self.client.get(reverse("user_list"), {
            "school": self.school_a.pk, "type": "student"})
        self.assertEqual([row.pk for row in users.context["page"]],
                         [self.student_a.user_id])
        users = self.client.get(reverse("user_list"), {
            "level": self.level_b.pk, "type": "staff"})
        self.assertEqual([row.pk for row in users.context["page"]],
                         [self.teacher_b.pk])

    def test_timetable_and_marks_report_honour_hierarchy_filters(self):
        self.client.force_login(self.registrar)
        timetable = self.client.get(reverse("timetable"), {"school": self.school_a.pk})
        shown = [entry.school_class_id
                 for _day, rows in timetable.context["groups"] for entry in rows]
        self.assertEqual(shown, [self.class_a.pk])
        timetable = self.client.get(reverse("timetable"), {"department": self.dept_b.pk})
        shown = [entry.school_class_id
                 for _day, rows in timetable.context["groups"] for entry in rows]
        self.assertEqual(shown, [self.class_b.pk])

        url = reverse("marks_report", args=["subject", self.subject.pk])
        report = self.client.get(url, {"school": self.school_a.pk})
        self.assertEqual([row.student_id for row in report.context["rows"]],
                         [self.student_a.pk])
        report = self.client.get(url, {"school_class": self.class_b.pk})
        self.assertEqual([row.student_id for row in report.context["rows"]],
                         [self.student_b.pk])

    def test_dashboard_figures_follow_the_filters(self):
        self.client.force_login(self.registrar)
        plain = self.client.get(reverse("dashboard"))
        self.assertEqual(plain.context["students"], 2)
        filtered = self.client.get(reverse("dashboard"), {"school": self.school_a.pk})
        self.assertEqual(filtered.context["students"], 1)

    def test_fee_reports_and_structures_honour_hierarchy_filters(self):
        self.client.force_login(self.bursar)
        debtors = self.client.get(reverse("report_debtors"), {
            "show": "all", "school": self.school_a.pk})
        self.assertEqual([row.pk for row in debtors.context["rows"]],
                         [self.student_a.pk])
        collections = self.client.get(reverse("report_collections"), {
            "school": self.school_a.pk})
        self.assertEqual(collections.context["student_count"], 1)

        structures = self.client.get(reverse("fee_structures"), {
            "school": self.school_a.pk})
        self.assertEqual([row.pk for row in structures.context["structures"]],
                         [self.fee_a.pk])
