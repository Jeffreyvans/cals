"""Tests for the configurable school/faculty hierarchy.

These cover the built-in defaults, platform template versioning, the structure
screens and the shared labels/filters that reports and dashboards use.
"""

import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from academics.hierarchy import (
    LEVEL_SETS,
    TERM_SCHEMES,
    apply_template,
    breadcrumb_label,
    builtin_template,
    hierarchy_context,
    hierarchy_label,
    template_for,
    validate_template_data,
)
from academics.models import (
    AcademicSchool,
    AcademicYear,
    GradeLevel,
    Programme,
    SchoolClass,
    Stream,
    Student,
)
from core.models import AuditEntry, Department, Institution
from core.roles import ensure_roles
from core.tenancy import use_institution
from platform_admin.models import AcademicTemplate

User = get_user_model()


class DefaultTemplateTests(TestCase):
    """Each institution type starts with a sensible, editable shape."""

    def test_institution_types_have_levels_and_terms(self):
        self.assertEqual(LEVEL_SETS["primary"]["levels"][0], "ECD A")
        self.assertEqual(LEVEL_SETS["secondary"]["levels"][0], "Form 1")
        self.assertEqual(LEVEL_SETS["secondary_grade_8_13"]["levels"],
                         [f"Grade {n}" for n in range(8, 14)])
        self.assertEqual(LEVEL_SETS["college_long"]["levels"][-1], "Year 6")
        self.assertEqual(TERM_SCHEMES["terms"], ["Term 1", "Term 2", "Term 3"])
        self.assertEqual(TERM_SCHEMES["semesters"], ["Semester 1", "Semester 2"])

    def test_builtins_match_the_institution_type(self):
        primary = builtin_template("primary")
        university = builtin_template("university")
        vocational = builtin_template("vocational")

        self.assertEqual(primary["term_scheme"], "terms")
        self.assertEqual(primary["schools"], [])
        self.assertIn("Languages", [row["name"] for row in primary["departments"]])
        self.assertEqual(university["term_scheme"], "semesters")
        self.assertTrue(university["schools"])
        self.assertIn("School of Law", [row["name"] for row in university["schools"]])
        self.assertEqual(vocational["schools"][0]["departments"][0]["name"], "ICT")

    def test_alternative_level_sets_can_replace_the_defaults(self):
        data = builtin_template("secondary", level_set="secondary_grade_8_13",
                                term_scheme="semesters")
        self.assertEqual([row["name"] for row in data["levels"]],
                         [f"Grade {n}" for n in range(8, 14)])
        self.assertEqual(data["terms"], ["Semester 1", "Semester 2"])

    def test_a_template_body_is_checked_before_it_is_published(self):
        data = builtin_template("college")
        data["levels"][0]["name"] = "  Foundation Year  "
        clean = validate_template_data(data)
        self.assertEqual(clean["levels"][0]["name"], "Foundation Year")
        with self.assertRaises(ValueError):
            validate_template_data({**data, "terms": "Term 1"})
        with self.assertRaises(ValueError):
            validate_template_data({**data, "levels": [{"name": "Year 1", "code": "Y1"},
                                                       {"name": "Year 2", "code": "Y1"}]})


class TemplateVersioningTests(TestCase):
    """Templates are copied once; later versions never rewrite live rows."""

    @classmethod
    def setUpTestData(cls):
        cls.institution = Institution.objects.create(
            name="Riverside University", slug="riverside-u", type="university")
        cls.other = Institution.objects.create(
            name="Lakeside University", slug="lakeside-u", type="university")

    def test_publishing_a_version_only_changes_future_institutions(self):
        original = template_for("university")
        apply_template(self.institution, "university", create_terms=False)
        with use_institution(self.institution):
            school_count = AcademicSchool.objects.count()
            level_names = set(GradeLevel.objects.values_list("name", flat=True))
            self.assertEqual(
                self.institution.settings["academic_template"]["version"],
                original["version"])

        changed = json.loads(json.dumps(original))
        changed["levels"][0]["name"] = "Foundation Year"
        changed["schools"][0]["name"] = "Faculty of the Future"
        AcademicTemplate.publish("university", "University (edited)", changed)

        self.assertNotEqual(template_for("university")["levels"][0]["name"],
                            original["levels"][0]["name"])
        apply_template(self.other, "university", create_terms=False)
        with use_institution(self.other):
            self.assertIn("Foundation Year",
                          GradeLevel.objects.values_list("name", flat=True))
        with use_institution(self.institution):
            self.assertEqual(AcademicSchool.objects.count(), school_count)
            self.assertEqual(set(GradeLevel.objects.values_list("name", flat=True)),
                             level_names)

    def test_activating_an_old_version_rolls_back_new_institutions_only(self):
        first = AcademicTemplate.objects.filter(code="university", is_active=True).get()
        updated = dict(first.data)
        updated["levels"] = [{**updated["levels"][0], "name": "Year Zero"}]
        newer = AcademicTemplate.publish("university", "University v2", updated)
        self.assertTrue(newer.is_active)
        self.assertFalse(AcademicTemplate.objects.get(pk=first.pk).is_active)

        first.is_active = True
        first.save()
        self.assertEqual(template_for("university")["levels"][0]["name"],
                         first.data["levels"][0]["name"])
        self.assertFalse(AcademicTemplate.objects.get(pk=newer.pk).is_active)

    def test_template_application_is_audited(self):
        apply_template(self.institution, "university", create_terms=False)
        entry = AuditEntry.all_objects.filter(
            action="academic.template_applied", institution=self.institution).first()
        self.assertIsNotNone(entry)
        self.assertGreater(entry.after["created"]["schools"], 0)


class StructureViewBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Hillcrest College", slug="hillcrest-c", type="college")
        cls.other = Institution.objects.create(
            name="Valley College", slug="valley-c", type="college")
        cls.no_roles = Institution.objects.create(
            name="Unconfigured College", slug="unconfigured-c", type="college")
        cls.roles = ensure_roles(cls.inst)
        ensure_roles(cls.other)

        cls.registrar = User.objects.create_user(
            "hillreg", password="pw123456", role="registrar", institution=cls.inst)
        cls.registrar.primary_role = cls.roles["registrar"]
        cls.registrar.save(update_fields=["primary_role"])
        cls.admin = User.objects.create_user(
            "hilladmin", password="pw123456", role="admin", institution=cls.inst)
        cls.admin.primary_role = cls.roles["institution_admin"]
        cls.admin.save(update_fields=["primary_role"])
        cls.no_permission = User.objects.create_user(
            "nopermreg", password="pw123456", role="registrar", institution=cls.no_roles)

        with use_institution(cls.inst):
            cls.year = AcademicYear.objects.create(
                name="2026", start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31), is_current=True)
            cls.level1 = GradeLevel.objects.create(
                name="Year 1", code="Y1", kind="college", order=1)
            cls.level2 = GradeLevel.objects.create(
                name="Year 2", code="Y2", kind="college", order=2)
            cls.school = AcademicSchool.objects.create(name="Science", code="SCI")
            cls.department = Department.objects.create(
                name="Physics", code="PHY", school=cls.school)
            cls.school_class = SchoolClass.objects.create(
                name="Year 1 A", grade_level=cls.level1,
                school=cls.school, department=cls.department, room="R1", capacity=30)
            cls.programme = Programme.objects.create(
                name="BSc", code="BSC", level="degree",
                school=cls.school, department=cls.department)
            student_user = User.objects.create_user(
                "hillstudent", password="pw123456", role="student", institution=cls.inst)
            cls.student = Student.objects.create(
                user=student_user, programme=cls.programme, school_class=cls.school_class,
                school=cls.school, department=cls.department)
        with use_institution(cls.other):
            cls.other_school = AcademicSchool.objects.create(
                name="Valley Faculty", code="VAL")

    def setUp(self):
        self.client.force_login(self.registrar)

    def post(self, url_name, data, *args, follow=True):
        return self.client.post(reverse(url_name, args=args), data, follow=follow)


class StructurePermissionTests(StructureViewBase):
    def test_a_registrar_with_the_permission_can_open_the_pages(self):
        for name in ("academic_structure", "school_list", "grades_classes"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_a_write_without_the_permission_is_refused_and_audited(self):
        self.client.force_login(self.no_permission)
        response = self.post("grades_classes", {
            "action": "rename_level", "level_pk": self.level1.pk, "name": "Hacked"})
        self.assertEqual(response.status_code, 403)
        entry = AuditEntry.all_objects.filter(
            action="permission.denied",
            object_id=reverse("grades_classes"),
            institution=self.no_roles).first()
        self.assertIsNotNone(entry)
        self.assertIn("academics.manage_structure", entry.after["codes"])

    def test_rows_from_another_institution_are_not_visible(self):
        response = self.client.get(reverse("school_list"))
        self.assertContains(response, "Science")
        self.assertNotContains(response, "Valley Faculty")


class StructureChangeTests(StructureViewBase):
    def test_rename_reorder_deactivate_and_promotion_target(self):
        self.post("grades_classes", {
            "action": "rename_level", "level_pk": self.level1.pk,
            "name": "Foundation Year", "code": "FY"})
        self.level1.refresh_from_db()
        self.assertEqual(self.level1.name, "Foundation Year")
        self.assertEqual(self.level1.code, "FY")
        with use_institution(self.inst):
            self.assertIn("Foundation Year", hierarchy_context(self.student)["level"].name)

        self.post("grades_classes", {
            "action": "move_level", "level_pk": self.level1.pk, "direction": "down"})
        self.level1.refresh_from_db()
        self.level2.refresh_from_db()
        self.assertGreater(self.level1.order, self.level2.order)

        self.post("grades_classes", {
            "action": "set_promotion", "level_pk": self.level1.pk,
            "promotion_to": self.level2.pk})
        self.level1.refresh_from_db()
        self.assertEqual(self.level1.next_level, self.level2)

        self.post("grades_classes", {
            "action": "deactivate_level", "level_pk": self.level1.pk})
        self.level1.refresh_from_db()
        self.assertFalse(self.level1.is_active)
        self.assertTrue(GradeLevel.all_objects.filter(pk=self.level1.pk).exists())

        for action in ("level.rename", "level.reorder", "level.promotion", "level.deactivate"):
            self.assertTrue(
                AuditEntry.all_objects.filter(action=action, institution=self.inst).exists(),
                action)

    def test_used_levels_and_classes_are_deactivated_not_deleted(self):
        response = self.post("grades_classes", {
            "action": "delete_level", "level_pk": self.level1.pk})
        self.assertContains(response, "Cannot delete")
        self.assertTrue(GradeLevel.all_objects.filter(pk=self.level1.pk).exists())

        response = self.post("grades_classes", {
            "action": "delete_class", "class_pk": self.school_class.pk})
        self.assertContains(response, "Cannot delete")
        self.assertTrue(SchoolClass.all_objects.filter(pk=self.school_class.pk).exists())

        self.post("grades_classes", {
            "action": "deactivate_class", "class_pk": self.school_class.pk})
        self.school_class.refresh_from_db()
        self.assertFalse(self.school_class.is_active)
        self.assertTrue(
            AuditEntry.all_objects.filter(action="class.deactivate",
                                          institution=self.inst).exists())

    def test_bulk_streams_are_created_once_and_audited(self):
        response = self.post("grades_classes", {
            "action": "bulk_streams", "class_ids": [self.school_class.pk],
            "stream_names": ["A, B", "A"], "new_streams": "Red\nBlue"})
        self.assertContains(response, "Added 4 streams")
        with use_institution(self.inst):
            self.assertEqual(
                set(Stream.objects.filter(school_class=self.school_class)
                    .values_list("name", flat=True)),
                {"A", "B", "Red", "Blue"})
        self.assertTrue(AuditEntry.all_objects.filter(
            action="stream.bulk_create", institution=self.inst).exists())

    def test_school_rename_propagates_to_reports_and_breadcrumbs(self):
        self.post("school_edit", {
            "name": "Faculty of Science", "code": "SCI", "sort_order": 1},
            self.school.pk)
        self.school.refresh_from_db()
        self.assertEqual(self.school.name, "Faculty of Science")
        with use_institution(self.inst):
            self.assertEqual(hierarchy_label(self.student),
                             "Faculty of Science, Department of Physics")

        request = RequestFactory().get("/academics/students/", {
            "school": self.school.pk, "department": self.department.pk})
        request.institution = self.inst
        self.assertEqual(breadcrumb_label(request),
                         "Faculty of Science / PHY - Physics")

        report = self.client.get(reverse("student_report", args=[self.student.pk]))
        self.assertEqual(report.status_code, 200)
        self.assertContains(report, "Faculty of Science, Department of Physics")

        self.post("school_edit", {
            "name": "Science", "code": "SCI", "sort_order": 1},
            self.school.pk)

    def test_school_with_dependants_cannot_be_deleted(self):
        response = self.post("school_delete", {}, self.school.pk)
        self.assertContains(response, "Cannot delete")
        self.assertTrue(AcademicSchool.all_objects.filter(pk=self.school.pk).exists())

    def test_a_school_can_be_created_and_the_change_is_audited(self):
        self.post("school_create", {
            "name": "Faculty of Arts", "code": "ART", "sort_order": 2})
        self.assertTrue(AcademicSchool.all_objects.filter(
            institution=self.inst, code="ART").exists())
        self.assertTrue(AuditEntry.all_objects.filter(
            action="school.create", institution=self.inst).exists())
