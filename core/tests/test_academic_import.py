"""Imports for the academic hierarchy sheets and the workbook template."""

import io
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from academics.models import (
    AcademicSchool,
    AcademicYear,
    ClassTeacher,
    GradeLevel,
    SchoolClass,
)
from core import importer
from core.models import AuditEntry, Department, Institution
from core.roles import ensure_roles
from core.tenancy import use_institution

User = get_user_model()


def workbook(sheets, name="academic-structure.xlsx"):
    """Build an XLSX where each key is an exact sheet name."""
    from openpyxl import Workbook

    book = Workbook()
    book.remove(book.active)
    for sheet_name, rows in sheets.items():
        sheet = book.create_sheet(sheet_name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return SimpleUploadedFile(
        name, buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class HierarchyImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Import College", slug="import-college", type="college")
        cls.roles = ensure_roles(cls.inst)
        cls.registrar = User.objects.create_user(
            "importreg", password="pw123456", role="registrar", institution=cls.inst)
        cls.registrar.primary_role = cls.roles["registrar"]
        cls.registrar.save(update_fields=["primary_role"])
        cls.no_permission = Institution.objects.create(
            name="No Permission College", slug="no-permission-college", type="college")
        cls.limited = User.objects.create_user(
            "importlimited", password="pw123456", role="registrar",
            institution=cls.no_permission)
        with use_institution(cls.inst):
            cls.teacher = User.objects.create_user(
                "teacher1", password="pw123456", role="lecturer", institution=cls.inst)
            AcademicSchool.objects.create(name="Keep Me", code="KEEP")
            cls.year = AcademicYear.objects.create(
                name="2026", start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31), is_current=True)

    def setUp(self):
        self.client.force_login(self.registrar)

    def _load(self, upload, kind):
        with use_institution(self.inst):
            rows = importer.read_rows(upload, upload.name, kind=kind)
            mapping = importer.guess_mapping(
                list(rows[0].keys()), importer.FIELDS_BY_KIND[kind])
            results = importer.validate(kind, rows[1:], mapping, self.inst)
            errors = [error for row in results for error in row["errors"]]
            self.assertEqual(errors, [], errors)
            return importer.run_import(kind, results, self.inst, self.registrar)

    def test_the_workbook_template_has_all_four_sheets(self):
        response = self.client.get(reverse("academic_setup_template"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"],
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        from openpyxl import load_workbook

        book = load_workbook(io.BytesIO(response.content))
        self.assertEqual(book.sheetnames, [
            "Schools or Faculties", "Departments", "Grade levels", "Classes",
            "Instructions"])
        self.assertIn("Code", [cell.value for cell in book["Schools or Faculties"][1]])
        self.assertIn("Class teacher", [cell.value for cell in book["Classes"][1]])

    def test_a_workbook_only_reads_the_sheet_for_the_kind(self):
        upload = workbook({
            "Students": [["Student Number"], ["WRONG"]],
            "Schools or Faculties": [["Code", "Name"], ["SCI", "Science Faculty"]],
            "Departments": [["Code", "Name"], ["WRONG", "Wrong Faculty"]],
        })
        with use_institution(self.inst):
            rows = importer.read_rows(upload, upload.name, kind="schools")
        self.assertEqual(rows[1]["Name"], "Science Faculty")

    def test_all_four_sheets_upsert_by_code_or_name_without_deleting(self):
        upload = workbook({
            "Schools or Faculties": [
                ["Code", "Name", "Description", "Sort order"],
                ["SCI", "Science Faculty", "Labs", "1"],
                ["ART", "Arts Faculty", "", "2"],
            ],
            "Departments": [
                ["Code", "Name", "School or faculty"],
                ["PHY", "Physics", "SCI"],
                ["ECO", "Economics", "ART"],
            ],
            "Grade levels": [
                ["Name", "Code", "Level type", "Order", "Active"],
                ["Grade 1", "G1", "primary", "1", "yes"],
                ["Grade 2", "G2", "primary", "2", "yes"],
            ],
            "Classes": [
                ["Name", "Grade level", "School or faculty", "Department",
                 "Streams", "Class teacher", "Capacity", "Room"],
                ["Grade 1 A", "G1", "SCI", "PHY", "A; B", "teacher1", "30", "R1"],
            ],
        })
        source = upload.read()
        for kind in ("schools", "departments", "grade_levels", "classes"):
            imported, errors = self._load(SimpleUploadedFile(
                "structure.xlsx", source,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ), kind)
            self.assertEqual(errors, [], errors)
            self.assertGreater(imported, 0, kind)

        with use_institution(self.inst):
            science = AcademicSchool.objects.get(code="SCI")
            arts = AcademicSchool.objects.get(code="ART")
            self.assertEqual(AcademicSchool.objects.count(), 3)  # Keep Me survives
            self.assertEqual(Department.objects.get(code="PHY").school, science)
            self.assertEqual(Department.objects.get(code="ECO").school, arts)
            level = GradeLevel.objects.get(code="G1")
            school_class = SchoolClass.objects.get(name="Grade 1 A")
            self.assertEqual(school_class.grade_level, level)
            self.assertEqual(school_class.school, science)
            self.assertEqual(school_class.department, Department.objects.get(code="PHY"))
            self.assertEqual(school_class.capacity, 30)
            self.assertEqual(school_class.room, "R1")
            self.assertEqual(set(school_class.streams.values_list("name", flat=True)),
                             {"A", "B"})
            self.assertTrue(ClassTeacher.objects.filter(
                school_class=school_class, teacher=self.teacher).exists())

        for action in ("academic.school.imported", "academic.department.imported",
                       "academic.grade_level.imported", "academic.class.imported"):
            self.assertTrue(
                AuditEntry.all_objects.filter(action=action, institution=self.inst).exists(),
                action)

    def test_upsert_updates_existing_rows_and_never_deletes_them(self):
        first = workbook({
            "Grade levels": [["Name", "Code", "Order"],
                             ["Grade 1", "G1", "1"], ["Grade 2", "G2", "2"]]})
        self._load(first, "grade_levels")
        second = workbook({
            "Grade levels": [["Name", "Code", "Order"],
                             ["Foundation Grade", "G1", "10"],
                             ["Grade 2", "", "22"]]})
        self._load(second, "grade_levels")

        with use_institution(self.inst):
            self.assertEqual(GradeLevel.objects.count(), 2)
            self.assertEqual(GradeLevel.objects.get(code="G1").name, "Foundation Grade")
            self.assertEqual(GradeLevel.objects.get(code="G1").order, 10)
            self.assertEqual(GradeLevel.objects.get(code="G2").order, 22)

    def test_structure_imports_need_the_manage_structure_permission(self):
        self.client.force_login(self.limited)
        response = self.client.get(reverse("academic_setup_template"))
        self.assertEqual(response.status_code, 403)

        upload = workbook({"Schools or Faculties": [["Code", "Name"], ["X", "X"]]})
        response = self.client.post(reverse("import_start"), {
            "kind": "schools", "file": upload, "access_mode": "none"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AcademicSchool.all_objects.filter(
            institution=self.no_permission).exists())
