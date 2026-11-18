"""Phase 1: the bulk import reads a file, checks it and writes the good rows."""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from academics.models import Course, Programme, SchoolClass, Student, Subject
from core import importer
from core.models import ImportBatch, Institution, StaffProfile
from core.roles import ensure_roles
from core.tenancy import use_institution
from finance.models import FeeStructure

User = get_user_model()


def csv_file(text, name="students.csv"):
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")


def xlsx_file(rows, name="students.xlsx"):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(),
                              content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


STUDENT_CSV = (
    "Student Number,First Name,Last Name,Programme,Class,Date of Birth,Guardian,Guardian Phone\n"
    "ST001,Tariro,Dube,DIT,,2007-03-04,Mr Dube,+263771000001\n"
    "ST002,Blessing,Sibanda,DIT,,2008-07-09,Mrs Sibanda,+263771000002\n"
)


class ReadingFileTests(TestCase):
    def test_a_csv_file_is_read_into_rows(self):
        rows = importer.read_rows(csv_file(STUDENT_CSV), "students.csv")
        self.assertEqual(list(rows[0].keys()),
                         ["Student Number", "First Name", "Last Name", "Programme",
                          "Class", "Date of Birth", "Guardian", "Guardian Phone"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["First Name"], "Tariro")

    def test_a_file_with_a_byte_order_mark_is_read(self):
        text = "\ufeffFirst Name,Last Name\nTariro,Dube\n"
        rows = importer.read_rows(csv_file(text), "students.csv")
        self.assertEqual(list(rows[0].keys()), ["First Name", "Last Name"])

    def test_an_excel_file_is_read_into_rows(self):
        upload = xlsx_file([["First Name", "Last Name"], ["Tariro", "Dube"]])
        rows = importer.read_rows(upload, "students.xlsx")
        self.assertEqual(list(rows[0].keys()), ["First Name", "Last Name"])
        self.assertEqual(rows[1]["Last Name"], "Dube")

    def test_a_file_of_the_wrong_kind_is_refused(self):
        upload = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        with self.assertRaises(importer.ImportError):
            importer.read_rows(upload, "notes.txt")

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(importer.ImportError):
            importer.read_rows(csv_file(""), "students.csv")

    def test_empty_lines_are_left_out(self):
        text = "First Name,Last Name\nTariro,Dube\n\nBlessing,Sibanda\n"
        rows = importer.read_rows(csv_file(text), "students.csv")
        self.assertEqual(len(rows), 3)


class MappingTests(TestCase):
    def test_the_columns_are_guessed_from_their_names(self):
        headers = ["Student Number", "First Name", "Last Name", "Programme"]
        mapping = importer.guess_mapping(headers, importer.STUDENT_FIELDS)
        self.assertEqual(mapping["student_no"], "Student Number")
        self.assertEqual(mapping["first_name"], "First Name")
        self.assertEqual(mapping["last_name"], "Last Name")
        self.assertEqual(mapping["programme"], "Programme")

    def test_a_column_is_only_used_once(self):
        headers = ["Name", "Title"]
        mapping = importer.guess_mapping(headers, importer.SUBJECT_FIELDS)
        self.assertEqual(mapping.get("name"), "Name")
        self.assertNotIn("Title", mapping.values())


class ImportFixture(TestCase):
    """A school with a programme, a class and the people who may import."""

    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha School", slug="alpha", type="secondary")
        ensure_roles(cls.inst)
        with use_institution(cls.inst):
            cls.programme = Programme.objects.create(
                code="DIT", name="Diploma in IT", level="diploma")
            cls.school_class = SchoolClass.objects.create(
                name="Form 2", level="secondary")
        cls.admin = User.objects.create_user(
            "boss", password="pw123456", role="admin", is_superuser=True,
            institution=cls.inst)
        cls.bursar = User.objects.create_user(
            "bur", password="pw123456", role="bursar", institution=cls.inst)
        cls.teacher = User.objects.create_user(
            "teach", password="pw123456", role="lecturer", institution=cls.inst)


class StudentImportTests(ImportFixture):
    def _check(self, rows, mapping=None):
        mapping = mapping or importer.guess_mapping(
            list(rows[0].keys()), importer.STUDENT_FIELDS)
        return importer.validate("students", rows[1:], mapping, self.inst)

    def test_good_rows_have_no_problems(self):
        rows = importer.read_rows(csv_file(STUDENT_CSV), "students.csv")
        results = self._check(rows)
        self.assertEqual(len(results), 2)
        for result in results:
            self.assertEqual(result["errors"], [])

    def test_a_row_without_a_first_name_is_reported(self):
        text = ("Student Number,First Name,Last Name\n"
                "ST001,,Dube\n")
        rows = importer.read_rows(csv_file(text), "students.csv")
        results = self._check(rows)
        self.assertIn("First name is needed.", results[0]["errors"])

    def test_an_unknown_programme_is_reported(self):
        text = ("Student Number,First Name,Last Name,Programme\n"
                "ST001,Tariro,Dube,NOTHERE\n")
        rows = importer.read_rows(csv_file(text), "students.csv")
        results = self._check(rows)
        self.assertIn("Unknown programme 'NOTHERE'.", results[0]["errors"])

    def test_an_unknown_class_is_reported(self):
        text = ("Student Number,First Name,Last Name,Class\n"
                "ST001,Tariro,Dube,Form 9\n")
        rows = importer.read_rows(csv_file(text), "students.csv")
        results = self._check(rows)
        self.assertIn("Unknown class 'Form 9'.", results[0]["errors"])

    def test_the_same_student_number_twice_is_reported(self):
        text = ("Student Number,First Name,Last Name\n"
                "ST001,Tariro,Dube\n"
                "ST001,Blessing,Sibanda\n")
        rows = importer.read_rows(csv_file(text), "students.csv")
        results = self._check(rows)
        self.assertIn("Student number 'ST001' is used twice in this file.",
                      results[1]["errors"])

    def test_a_username_that_is_taken_is_reported(self):
        User.objects.create_user("taken", password="pw123456", institution=self.inst)
        text = ("Student Number,First Name,Last Name,Username\n"
                "ST001,Tariro,Dube,taken\n")
        rows = importer.read_rows(csv_file(text), "students.csv")
        results = self._check(rows)
        self.assertIn("Username 'taken' is already taken.", results[0]["errors"])

    def test_a_bad_date_is_reported(self):
        text = ("Student Number,First Name,Last Name,Date of Birth\n"
                "ST001,Tariro,Dube,not a date\n")
        rows = importer.read_rows(csv_file(text), "students.csv")
        results = self._check(rows)
        self.assertTrue(any("is not a date" in problem for problem in results[0]["errors"]))

    def test_the_students_are_written(self):
        rows = importer.read_rows(csv_file(STUDENT_CSV), "students.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.STUDENT_FIELDS)
        results = importer.validate("students", rows[1:], mapping, self.inst)
        imported, errors = importer.run_import("students", results, self.inst, self.admin)
        self.assertEqual(imported, 2)
        self.assertEqual(errors, [])
        with use_institution(self.inst):
            self.assertEqual(Student.objects.count(), 2)
            student = Student.objects.get(student_no="ST001")
            self.assertEqual(student.user.first_name, "Tariro")
            self.assertEqual(student.user.last_name, "Dube")
            self.assertEqual(student.programme, self.programme)
            self.assertEqual(student.guardian_name, "Mr Dube")

    def test_a_student_login_must_be_reset_before_it_is_used(self):
        rows = importer.read_rows(csv_file(STUDENT_CSV), "students.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.STUDENT_FIELDS)
        results = importer.validate("students", rows[1:], mapping, self.inst)
        importer.run_import("students", results, self.inst, self.admin)
        with use_institution(self.inst):
            account = User.objects.get(username="ST001")
            self.assertFalse(account.has_usable_password())

    def test_a_username_is_made_from_the_student_number(self):
        rows = importer.read_rows(csv_file(STUDENT_CSV), "students.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.STUDENT_FIELDS)
        results = importer.validate("students", rows[1:], mapping, self.inst)
        importer.run_import("students", results, self.inst, self.admin)
        with use_institution(self.inst):
            self.assertTrue(User.objects.filter(username="ST001").exists())

    def test_a_row_without_a_number_gets_the_next_one(self):
        text = ("Student Number,First Name,Last Name,Programme\n"
                ",Tariro,Dube,DIT\n"
                ",Blessing,Sibanda,DIT\n")
        rows = importer.read_rows(csv_file(text), "students.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.STUDENT_FIELDS)
        results = importer.validate("students", rows[1:], mapping, self.inst)
        imported, errors = importer.run_import("students", results, self.inst, self.admin)
        self.assertEqual(imported, 2)
        self.assertEqual(errors, [])
        with use_institution(self.inst):
            numbers = list(Student.objects.order_by("pk").values_list(
                "student_no", flat=True))
        self.assertEqual(numbers[0], numbers[0].upper())
        self.assertNotEqual(numbers[0], numbers[1])

    def test_duplicate_numbers_are_found_whatever_the_case_or_separators(self):
        text = ("Student Number,First Name,Last Name\n"
                "ST-001,Tariro,Dube\n"
                "st 001,Blessing,Sibanda\n")
        rows = importer.read_rows(csv_file(text), "students.csv")
        results = self._check(rows)
        self.assertTrue(any("used twice" in problem
                            for problem in results[1]["errors"]))

    def test_a_country_number_is_kept_exactly(self):
        text = ("Student Number,First Name,Last Name,Programme\n"
                "abc-7,Tariro,Dube,DIT\n")
        rows = importer.read_rows(csv_file(text), "students.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.STUDENT_FIELDS)
        results = importer.validate("students", rows[1:], mapping, self.inst)
        importer.run_import("students", results, self.inst, self.admin)
        with use_institution(self.inst):
            self.assertTrue(Student.objects.filter(student_no="ABC-7").exists())

    def test_the_students_of_another_institution_are_not_touched(self):
        other = Institution.objects.create(name="Beta School", slug="beta", type="secondary")
        ensure_roles(other)
        with use_institution(other):
            Programme.objects.create(code="DIT", name="Diploma in IT", level="diploma")
        rows = importer.read_rows(csv_file(STUDENT_CSV), "students.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.STUDENT_FIELDS)
        results = importer.validate("students", rows[1:], mapping, self.inst)
        importer.run_import("students", results, self.inst, self.admin)
        with use_institution(other):
            self.assertEqual(Student.objects.count(), 0)


class StaffImportTests(ImportFixture):
    def test_staff_are_written_with_their_profile(self):
        text = ("Username,First Name,Last Name,Email,Employee No,Job Title,Phone\n"
                "j.dube,John,Dube,john@dube.test,E001,Head of Science,+263771000003\n")
        rows = importer.read_rows(csv_file(text), "staff.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.STAFF_FIELDS)
        results = importer.validate("staff", rows[1:], mapping, self.inst)
        self.assertEqual(results[0]["errors"], [])
        imported, errors = importer.run_import("staff", results, self.inst, self.admin)
        self.assertEqual(imported, 1)
        self.assertEqual(errors, [])
        with use_institution(self.inst):
            profile = StaffProfile.objects.get(user__username="j.dube")
            self.assertEqual(profile.employee_no, "E001")
            self.assertEqual(profile.job_title, "Head of Science")
            self.assertEqual(profile.phone, "+263771000003")

    def test_a_staff_row_without_a_username_is_reported(self):
        text = "First Name,Last Name\nJohn,Dube\n"
        rows = importer.read_rows(csv_file(text), "staff.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.STAFF_FIELDS)
        results = importer.validate("staff", rows[1:], mapping, self.inst)
        self.assertIn("Login username is missing.", results[0]["errors"])

    def test_a_staff_email_must_look_like_an_email(self):
        text = ("Username,First Name,Last Name,Email\n"
                "j.dube,John,Dube,john\n")
        rows = importer.read_rows(csv_file(text), "staff.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.STAFF_FIELDS)
        results = importer.validate("staff", rows[1:], mapping, self.inst)
        self.assertTrue(any("is not an email address" in problem
                            for problem in results[0]["errors"]))


class CourseImportTests(ImportFixture):
    def test_courses_are_written(self):
        text = ("Code,Title,Programme,Credits,Lecturer\n"
                "IT101,Programming,DIT,4,teach\n")
        rows = importer.read_rows(csv_file(text), "courses.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.COURSE_FIELDS)
        results = importer.validate("courses", rows[1:], mapping, self.inst)
        self.assertEqual(results[0]["errors"], [])
        imported, _errors = importer.run_import("courses", results, self.inst, self.admin)
        self.assertEqual(imported, 1)
        with use_institution(self.inst):
            course = Course.objects.get(code="IT101")
            self.assertEqual(course.title, "Programming")
            self.assertEqual(course.credits, 4)
            self.assertEqual(course.programme, self.programme)
            self.assertEqual(course.lecturer.username, "teach")

    def test_a_course_without_a_programme_is_reported(self):
        text = "Code,Title,Programme\nIT101,Programming,\n"
        rows = importer.read_rows(csv_file(text), "courses.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.COURSE_FIELDS)
        results = importer.validate("courses", rows[1:], mapping, self.inst)
        self.assertIn("Programme code is needed.", results[0]["errors"])

    def test_a_course_code_used_twice_is_reported(self):
        with use_institution(self.inst):
            Course.objects.create(code="IT101", title="Old", programme=self.programme)
        text = "Code,Title,Programme\nIT101,Programming,DIT\n"
        rows = importer.read_rows(csv_file(text), "courses.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.COURSE_FIELDS)
        results = importer.validate("courses", rows[1:], mapping, self.inst)
        self.assertIn("Course code 'IT101' is already used.", results[0]["errors"])


class SubjectImportTests(ImportFixture):
    def test_subjects_are_written(self):
        text = "Code,Name,Level\nMTH1,Mathematics,secondary\n"
        rows = importer.read_rows(csv_file(text), "subjects.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()), importer.SUBJECT_FIELDS)
        results = importer.validate("subjects", rows[1:], mapping, self.inst)
        self.assertEqual(results[0]["errors"], [])
        imported, _errors = importer.run_import("subjects", results, self.inst, self.admin)
        self.assertEqual(imported, 1)
        with use_institution(self.inst):
            subject = Subject.objects.get(code="MTH1")
            self.assertEqual(subject.name, "Mathematics")
            self.assertEqual(subject.level, Subject.Level.SECONDARY)


class FeeStructureImportTests(ImportFixture):
    def test_the_lines_of_one_structure_are_written_together(self):
        text = ("Fee Structure Code,Fee Structure Name,Fee Item,Amount,Mandatory\n"
                "TERM1,Term 1 fees,Tuition,900.00,yes\n"
                "TERM1,Term 1 fees,Books,50.50,no\n")
        rows = importer.read_rows(csv_file(text), "fees.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()),
                                         importer.FEE_STRUCTURE_FIELDS)
        results = importer.validate("fee_structures", rows[1:], mapping, self.inst)
        self.assertEqual(results[0]["errors"], [])
        imported, _errors = importer.run_import("fee_structures", results, self.inst,
                                                 self.bursar)
        self.assertEqual(imported, 2)
        with use_institution(self.inst):
            structure = FeeStructure.objects.get(code="TERM1")
            self.assertEqual(structure.name, "Term 1 fees")
            self.assertEqual(structure.items.count(), 2)
            self.assertEqual(structure.total, 950.50)

    def test_an_amount_that_is_not_a_number_is_reported(self):
        text = ("Fee Structure Code,Fee Structure Name,Fee Item,Amount\n"
                "TERM1,Term 1 fees,Tuition,lots\n")
        rows = importer.read_rows(csv_file(text), "fees.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()),
                                         importer.FEE_STRUCTURE_FIELDS)
        results = importer.validate("fee_structures", rows[1:], mapping, self.inst)
        self.assertTrue(any("is not an amount" in problem
                            for problem in results[0]["errors"]))

    def test_a_structure_code_used_twice_is_reported(self):
        with use_institution(self.inst):
            FeeStructure.objects.create(code="TERM1", name="Old")
        text = ("Fee Structure Code,Fee Structure Name,Fee Item,Amount\n"
                "TERM1,Term 1 fees,Tuition,900.00\n")
        rows = importer.read_rows(csv_file(text), "fees.csv")
        mapping = importer.guess_mapping(list(rows[0].keys()),
                                         importer.FEE_STRUCTURE_FIELDS)
        results = importer.validate("fee_structures", rows[1:], mapping, self.inst)
        self.assertIn("Fee structure code 'TERM1' is already used.",
                      results[0]["errors"])


class ImportScreenTests(ImportFixture):
    def test_the_start_page_is_open_to_a_registrar(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("import_start"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Students")

    def test_a_user_without_the_permission_is_refused(self):
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("import_start"))
        self.assertEqual(resp.status_code, 403)

    def test_the_whole_import_is_walked(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("import_start"), {
            "kind": "students",
            "file": csv_file(STUDENT_CSV),
            "access_mode": "invite",
        })
        self.assertEqual(resp.status_code, 302)
        batch = ImportBatch.all_objects.get()
        self.assertEqual(batch.kind, "students")
        self.assertEqual(batch.filename, "students.csv")
        self.assertEqual(len(batch.data_rows), 2)
        self.assertEqual(batch.mapping["first_name"], "First Name")

        resp = self.client.get(reverse("import_map", args=[batch.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "First name")

        resp = self.client.get(reverse("import_preview", args=[batch.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "2 rows read")
        self.assertContains(resp, "2 ready")

        resp = self.client.post(reverse("import_run", args=[batch.pk]))
        self.assertEqual(resp.status_code, 302)
        batch.refresh_from_db()
        self.assertEqual(batch.imported_count, 2)
        self.assertEqual(batch.error_count, 0)
        self.assertEqual(batch.status, ImportBatch.Status.IMPORTED)
        with use_institution(self.inst):
            self.assertEqual(Student.objects.count(), 2)

    def test_an_excel_file_can_be_imported(self):
        self.client.force_login(self.admin)
        upload = xlsx_file([
            ["Student Number", "First Name", "Last Name", "Programme"],
            ["ST010", "Chen", "Moyo", "DIT"],
        ])
        self.client.post(reverse("import_start"), {"kind": "students", "file": upload, "access_mode": "invite"})
        batch = ImportBatch.all_objects.get()
        self.client.get(reverse("import_map", args=[batch.pk]))
        self.client.post(reverse("import_run", args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(batch.imported_count, 1)
        with use_institution(self.inst):
            self.assertTrue(Student.objects.filter(student_no="ST010").exists())

    def test_rows_with_problems_are_left_out_and_reported(self):
        self.client.force_login(self.admin)
        text = ("Student Number,First Name,Last Name,Programme\n"
                "ST001,Tariro,Dube,DIT\n"
                "ST002,Blessing,Sibanda,NOTHERE\n")
        self.client.post(reverse("import_start"), {
            "kind": "students", "file": csv_file(text), "access_mode": "invite"})
        batch = ImportBatch.all_objects.get()
        self.client.get(reverse("import_map", args=[batch.pk]))
        resp = self.client.get(reverse("import_preview", args=[batch.pk]))
        self.assertContains(resp, "1 with a problem")
        self.client.post(reverse("import_run", args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(batch.imported_count, 1)
        self.assertEqual(batch.error_count, 1)
        with use_institution(self.inst):
            self.assertEqual(Student.objects.count(), 1)

    def test_the_error_report_can_be_downloaded(self):
        self.client.force_login(self.admin)
        text = ("Student Number,First Name,Last Name,Programme\n"
                "ST001,Tariro,Dube,DIT\n"
                "ST002,Blessing,Sibanda,NOTHERE\n")
        self.client.post(reverse("import_start"), {
            "kind": "students", "file": csv_file(text), "access_mode": "invite"})
        batch = ImportBatch.all_objects.get()
        self.client.get(reverse("import_map", args=[batch.pk]))
        self.client.post(reverse("import_run", args=[batch.pk]))
        resp = self.client.get(reverse("import_errors", args=[batch.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        body = resp.content.decode("utf-8-sig")
        self.assertIn("Unknown programme 'NOTHERE'.", body)
        self.assertIn("ST002", body)

    def test_the_import_is_written_to_the_audit_log(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("import_start"), {
            "kind": "students", "file": csv_file(STUDENT_CSV), "access_mode": "invite"})
        batch = ImportBatch.all_objects.get()
        self.client.get(reverse("import_map", args=[batch.pk]))
        self.client.post(reverse("import_run", args=[batch.pk]))
        from core.models import AuditEntry

        self.assertTrue(
            AuditEntry.all_objects.filter(action="import.imported").exists())

    def test_the_list_of_imports_is_shown(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("import_start"), {
            "kind": "students", "file": csv_file(STUDENT_CSV), "access_mode": "invite"})
        resp = self.client.get(reverse("import_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "students.csv")

    def test_a_file_of_the_wrong_kind_is_refused(self):
        self.client.force_login(self.admin)
        upload = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        resp = self.client.post(reverse("import_start"),
                                 {"kind": "students", "file": upload, "access_mode": "invite"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ImportBatch.objects.count(), 0)

    def test_an_import_of_another_institution_cannot_be_opened(self):
        other = Institution.objects.create(name="Beta School", slug="beta", type="secondary")
        ensure_roles(other)
        batch = ImportBatch.objects.create(
            institution=other, kind="students", filename="beta.csv", rows=[])
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("import_map", args=[batch.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_the_bursar_may_import_fee_structures(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(reverse("import_start"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Fee structures")
        self.assertNotContains(resp, "Students")
