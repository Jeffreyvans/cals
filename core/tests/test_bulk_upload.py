"""Tests for the dedicated student bulk upload.

The planner and the writer are exercised directly: a thread only makes the
result harder to observe and is not part of the behaviour under test.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from openpyxl import load_workbook

from academics.models import (
    AcademicYear,
    Course,
    CourseOffering,
    Enrollment,
    Programme,
    Student,
    Term,
)
from accounts.models import User
from core import bulk_upload, importer, jobs
from core.models import (
    AuditEntry,
    BulkUploadMatch,
    ImportBatch,
    Institution,
    OutboundMessage,
)
from core.roles import ensure_roles
from core.tenancy import use_institution
from finance.models import Invoice


def csv_upload(text, name="students.csv"):
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")


class BulkUploadBase(TestCase):
    def setUp(self):
        self.inst = Institution.objects.create(
            name="Upload College", slug="upload-college", type="college")
        self.other = Institution.objects.create(
            name="Other College", slug="other-college", type="college")
        self.roles = ensure_roles(self.inst)
        ensure_roles(self.other)
        self.actor = self._user("reg", self.inst, role="registrar",
                                primary_role=self.roles["registrar"])
        self.other_actor = self._user("other-reg", self.other, role="registrar")
        self.data = self._structure(self.inst, code="DIT")
        self.other_data = self._structure(self.other, code="OTHER")

    @staticmethod
    def _user(username, institution, *, role="student", email="", **extra):
        return User.objects.create_user(
            username, email=email, password=None, role=role,
            institution=institution, **extra)

    @staticmethod
    def _structure(institution, *, code="DIT", tuition=Decimal("0")):
        with use_institution(institution):
            programme = Programme.objects.create(
                institution=institution, code=code, name=f"{code} Programme",
                level="diploma", tuition_fee=tuition)
            year = AcademicYear.objects.create(
                institution=institution, name="2026",
                start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                is_current=True)
            term = Term.objects.create(
                institution=institution, academic_year=year, name="Term 1",
                start_date=date(2026, 1, 1), end_date=date(2026, 4, 1),
                is_current=True)
            course = Course.objects.create(
                institution=institution, code=f"{code}101",
                title=f"{code} Course", programme=programme, credits=3)
        return SimpleNamespace(
            programme=programme, year=year, term=term, course=course)

    def _existing_student(self, institution, username, programme, *, number,
                          email="", first_name=None, last_name=None, **fields):
        phone = fields.pop("phone", "")
        parts = username.replace("-", ".").split(".")
        user = self._user(
            username, institution, email=email,
            first_name=first_name or parts[0][:150].title(),
            last_name=last_name or (parts[1][:150].title() if len(parts) > 1 else ""))
        if phone:
            user.phone = phone
            user.save(update_fields=["phone"])
        with use_institution(institution):
            return Student.objects.create(
                institution=institution, user=user, programme=programme,
                student_no=number, **fields)

    def _batch(self, rows, *, options=None, access_mode=None, institution=None):
        fields = importer.FIELDS_BY_KIND[bulk_upload.KIND]
        header = {field["key"]: field["label"] for field in fields}
        mapping = {field["key"]: field["key"] for field in fields}
        return ImportBatch.objects.create(
            institution=institution or self.inst,
            kind=bulk_upload.KIND,
            filename="students.csv",
            rows=[header] + list(rows),
            mapping=mapping,
            options={**bulk_upload.DEFAULT_OPTIONS, **(options or {})},
            created_by=self.actor,
            status=ImportBatch.Status.UPLOADED,
            access_mode=access_mode or ImportBatch.AccessMode.NO_LOGIN,
        )

    def _run(self, batch, via_jobs=False):
        runner = jobs.perform_import if via_jobs else bulk_upload.perform
        return runner(batch, self.actor)

    @staticmethod
    def _row(**values):
        return dict(values)

    def _base_row(self, **extra):
        row = self._row(
            first_name="Tariro", last_name="Dube",
            date_of_birth="2008-03-04", programme="DIT",
            email="tariro.dube@example.com", phone="0771234567",
        )
        row.update(extra)
        return row


class PlannerAndWriteTests(BulkUploadBase):
    def test_new_student_uses_generated_number_and_no_login(self):
        batch = self._batch([self._base_row(courses="DIT101", term="Term 1")])
        results, summary = bulk_upload.Planner(batch).build()
        self.assertEqual(summary["created"], 1)
        self.assertEqual(results[0]["action"], "create")

        self._run(batch, via_jobs=True)

        student = Student.all_objects.get(
            institution=self.inst, user__email="tariro.dube@example.com")
        self.assertTrue(student.student_no)
        self.assertEqual(student.gender, "")
        self.assertEqual(batch.status, ImportBatch.Status.IMPORTED)
        self.assertEqual(batch.summary["written"], 1)
        self.assertFalse(student.user.has_usable_password())
        self.assertFalse(OutboundMessage.all_objects.filter(
            institution=self.inst, recipient=student.user.email).exists())
        match = BulkUploadMatch.objects.get(institution=self.inst, student=student)
        self.assertEqual(match.source_row, 2)

    def test_manual_number_and_temporary_password_are_reported_once(self):
        batch = self._batch(
            [self._base_row(registration_number="stu-100", gender="Female")],
            access_mode=ImportBatch.AccessMode.TEMPORARY_PASSWORD)
        written, errors = self._run(batch)
        self.assertEqual((written, errors), (1, []))

        student = Student.all_objects.get(institution=self.inst, student_no="STU-100")
        self.assertEqual(student.gender, "female")
        credential = batch.credentials[0]
        self.assertEqual(credential["registration_number"], "STU-100")
        self.assertTrue(credential["password"])
        self.assertTrue(student.user.must_change_password)

        audit = json.dumps([
            {"before": item.before, "after": item.after}
            for item in AuditEntry.objects.filter(institution=self.inst)
        ])
        self.assertNotIn(credential["password"], audit)

    def test_reupload_without_identity_updates_instead_of_duplicating(self):
        # No registration number, email or username: only the import fingerprint
        # can recognise the row on the second upload.
        first_row = self._row(
            first_name="Tariro", last_name="Dube", date_of_birth="2008-03-04",
            programme="DIT", phone="0771111111")
        first = self._batch([first_row])
        self._run(first)
        original = Student.all_objects.get(institution=self.inst)

        second = self._batch([{**first_row, "phone": "0772222222"}])
        results, _summary = bulk_upload.Planner(second).build()
        self.assertEqual(results[0]["action"], "update")
        self.assertEqual(results[0]["match"], "previous import")
        self._run(second)

        self.assertEqual(Student.all_objects.filter(institution=self.inst).count(), 1)
        original.refresh_from_db()
        self.assertEqual(original.user.phone, "0772222222")
        self.assertEqual(
            BulkUploadMatch.objects.filter(institution=self.inst).count(), 1)

    def test_update_never_erases_blank_cells_or_changes_the_number(self):
        student = self._existing_student(
            self.inst, "existing.one", self.data.programme, number="OLD-001",
            email="old@example.com", phone="0771111111",
            guardian_name="Mrs Dube", guardian_phone="0773333333",
            year_or_grade="2")
        row = self._row(
            registration_number="DIFFERENT", email=student.user.email,
            first_name="Changed", phone="", guardian_name="",
            guardian_phone="", year_or_grade="4", date_of_birth="2008-03-04")
        batch = self._batch([row])
        results, _summary = bulk_upload.Planner(batch).build()
        self.assertEqual(results[0]["action"], "update")
        self.assertTrue(any("kept 'OLD-001'" in note for note in results[0]["warnings"]))
        self.assertTrue(any("Update year or grade" in note
                            for note in results[0]["warnings"]))

        self._run(batch)
        student.refresh_from_db()
        student.user.refresh_from_db()
        self.assertEqual(student.student_no, "OLD-001")
        self.assertEqual(student.user.first_name, "Changed")
        self.assertEqual(student.user.phone, "0771111111")
        self.assertEqual(student.guardian_name, "Mrs Dube")
        self.assertEqual(student.guardian_phone, "0773333333")
        self.assertEqual(student.year_or_grade, "2")

        allowed = self._batch([row], options={"update_year_grade": True})
        self._run(allowed)
        student.refresh_from_db()
        self.assertEqual(student.year_or_grade, "4")

    def test_all_strong_match_paths_are_used(self):
        by_number = self._existing_student(
            self.inst, "match.number", self.data.programme,
            number="MATCH-1", email="number@example.com")
        by_email = self._existing_student(
            self.inst, "match.email", self.data.programme,
            number="MATCH-2", email="email@example.com")
        by_username = self._existing_student(
            self.inst, "match.username", self.data.programme,
            number="MATCH-3", email="username@example.com")
        rows = [
            self._row(registration_number="MATCH-1", first_name="A",
                      last_name="Number", programme="DIT", guardian_name="By number"),
            self._row(email="email@example.com", first_name="B",
                      last_name="Email", programme="DIT", guardian_name="By email"),
            self._row(username="match.username", first_name="C",
                      last_name="Username", programme="DIT", guardian_name="By username"),
        ]
        batch = self._batch(rows)
        results, _summary = bulk_upload.Planner(batch).build()
        self.assertEqual([item["match"] for item in results],
                         ["registration number", "email", "username"])
        self._run(batch)
        for student, guardian in ((by_number, "By number"), (by_email, "By email"),
                                  (by_username, "By username")):
            student.refresh_from_db()
            self.assertEqual(student.guardian_name, guardian)

    def test_fuzzy_match_is_only_flagged_and_never_changed(self):
        student = self._existing_student(
            self.inst, "fuzzy.student", self.data.programme, number="FUZZY-1",
            date_of_birth=date(2008, 3, 4), guardian_name="Do not change")
        row = self._row(first_name="Different", last_name="Student",
                        date_of_birth="2008-03-04", programme="DIT",
                        guardian_name="Should not be saved")
        batch = self._batch([row])
        results, summary = bulk_upload.Planner(batch).build()
        self.assertEqual(results[0]["action"], "skip")
        self.assertTrue(results[0]["fuzzy"])
        self.assertEqual(summary["skipped"], 1)

        self._run(batch)
        student.refresh_from_db()
        self.assertEqual(student.guardian_name, "Do not change")
        self.assertEqual(batch.summary["written"], 0)

    def test_fuzzy_key_is_normalised_the_same_way_for_stored_and_planned_rows(self):
        student = self._existing_student(
            self.inst, "punctuated", self.data.programme, number="PUNC-1",
            date_of_birth=date(2008, 3, 4), last_name="Student")
        row = self._row(first_name="Another", last_name="Student",
                        date_of_birth="2008-03-04", programme="D.I.T.",
                        email="punctuated@example.com")
        results, _summary = bulk_upload.Planner(self._batch([row])).build()
        self.assertEqual(results[0]["action"], "skip")
        self.assertEqual(results[0]["student_id"], student.pk)

    def test_missing_objects_are_rejected_by_default_and_created_on_request(self):
        row = self._row(
            first_name="New", last_name="Student", programme="Form 5",
            stream_or_cohort="Blue", intake_year="2029",
            courses="SCIENCE101", term="Term 1")
        rejected = self._batch([row])
        results, _summary = bulk_upload.Planner(rejected).build()
        self.assertEqual(results[0]["action"], "error")
        self.assertTrue(any("Programme or class" in error
                            for error in results[0]["errors"]))

        batch = self._batch([row], options={
            "create_missing_programmes": True,
            "create_missing_classes": True,
            "create_missing_courses": True,
        })
        results, summary = bulk_upload.Planner(batch).build()
        self.assertEqual(results[0]["action"], "create")
        self.assertGreaterEqual(summary["programmes_created"], 1)
        self.assertTrue(all("review needed" in str(item["note"])
                            for item in results[0]["created"]))
        self._run(batch)

        with use_institution(self.inst):
            self.assertTrue(Programme.all_objects.filter(code="FORM5").exists())
            self.assertTrue(Course.all_objects.filter(code="SCIENCE101").exists())
            created_year = AcademicYear.all_objects.filter(name="2029").first()
            self.assertIsNotNone(created_year)
        # An existing programme is never renamed by the import.
        self.data.programme.refresh_from_db()
        self.assertEqual(self.data.programme.name, "DIT Programme")

    def test_existing_enrolment_is_kept_and_missing_one_is_added(self):
        student = self._existing_student(
            self.inst, "enrolled.one", self.data.programme, number="ENR-1")
        other_course = Course.objects.create(
            institution=self.inst, code="DIT102", title="Second Course",
            programme=self.data.programme)
        existing = Enrollment.objects.create(
            institution=self.inst, student=student, course=self.data.course,
            term=self.data.term, status="registered", mark=Decimal("78"))
        batch = self._batch([self._row(
            registration_number="ENR-1", first_name="Enrolled", last_name="One",
            programme="DIT", courses="DIT101;DIT102", term="Term 1")],
            options={"apply_registration_rules": False})
        self._run(batch)

        existing.refresh_from_db()
        self.assertEqual(existing.mark, Decimal("78"))
        self.assertTrue(Enrollment.objects.filter(
            student=student, course=other_course, status="registered").exists())
        self.assertTrue(any("already enrolled" in warning
                            for item in batch.warnings for warning in item["messages"]))

    def test_removing_enrolments_needs_confirmation_and_keeps_marked_rows(self):
        student = self._existing_student(
            self.inst, "remove.one", self.data.programme, number="REM-1")
        course2 = Course.objects.create(
            institution=self.inst, code="DIT102", title="Second Course",
            programme=self.data.programme)
        kept = Enrollment.objects.create(
            institution=self.inst, student=student, course=self.data.course,
            term=self.data.term, status="registered")
        dropped = Enrollment.objects.create(
            institution=self.inst, student=student, course=course2,
            term=self.data.term, status="registered")
        row = self._row(registration_number="REM-1", first_name="Remove",
                        last_name="One", programme="DIT", courses="DIT101",
                        term="Term 1")
        options = {"apply_registration_rules": False,
                   "remove_missing_enrolments": True}
        unconfirmed = self._batch([row], options=options)
        self._run(unconfirmed)
        dropped.refresh_from_db()
        self.assertEqual(dropped.status, "registered")

        confirmed = self._batch(
            [row], options={**options, "confirmed_remove": True})
        self._run(confirmed)
        kept.refresh_from_db()
        dropped.refresh_from_db()
        self.assertEqual(kept.status, "registered")
        self.assertEqual(dropped.status, "dropped")
        self.assertTrue(Enrollment.all_objects.filter(pk=dropped.pk).exists())

    def test_registration_rules_can_be_skipped_with_a_warning(self):
        course = self.data.course
        offering = CourseOffering.objects.create(
            institution=self.inst, course=course, term=self.data.term, capacity=1)
        other_student = self._existing_student(
            self.inst, "fills.place", self.data.programme, number="FILL-1")
        Enrollment.objects.create(
            institution=self.inst, student=other_student, course=course,
            term=self.data.term, offering=offering, status="registered")

        row = self._row(first_name="Late", last_name="Student", programme="DIT",
                        courses="DIT101", term="Term 1")
        blocked = self._batch([row])
        self._run(blocked)
        self.assertFalse(Enrollment.objects.filter(
            student__user__last_name="Student", course=course).exists())

        allowed = self._batch([row], options={"apply_registration_rules": False})
        self._run(allowed)
        self.assertTrue(Enrollment.objects.filter(
            student__user__last_name="Student", course=course).exists())
        self.assertTrue(any("added anyway" in warning
                            for item in allowed.warnings for warning in item["messages"]))

    def test_duplicates_and_bad_data_do_not_stop_a_good_row(self):
        rows = [
            self._row(first_name="One", last_name="Dube", programme="DIT",
                      registration_number="DUP-1"),
            self._row(first_name="Two", last_name="Dube", programme="DIT",
                      registration_number="DUP-1"),
            self._row(first_name="", last_name="Nobody", programme="DIT"),
            self._row(first_name="Bad", last_name="Email", email="not-an-email",
                      programme="DIT"),
            self._base_row(registration_number="GOOD-1"),
        ]
        batch = self._batch(rows)
        written, errors = self._run(batch)
        self.assertEqual(written, 1)
        self.assertEqual(len(errors), 4)
        self.assertEqual(Student.all_objects.filter(institution=self.inst).count(), 1)
        self.assertTrue(Student.all_objects.filter(student_no="GOOD-1").exists())
        self.assertEqual(batch.status, ImportBatch.Status.IMPORTED)

    def test_five_thousand_rows_are_planned_with_bounded_queries(self):
        rows = [
            self._row(
                registration_number=f"LARGE-{index:05d}",
                first_name=f"Student{index}",
                last_name="Bulk",
                programme="DIT",
            )
            for index in range(5000)
        ]
        batch = self._batch(rows)
        with CaptureQueriesContext(connection) as queries:
            results, summary = bulk_upload.Planner(batch).build()
        self.assertEqual(summary["created"], 5000)
        self.assertEqual(len(results), 5000)
        # Lookups are cached for the batch: planning must not issue a query per row.
        self.assertLess(len(queries), 50)

    def test_no_change_rows_are_not_written_but_enrolment_only_changes_are(self):
        student = self._existing_student(
            self.inst, "same.data", self.data.programme, number="SAME-1",
            date_of_birth=date(2008, 3, 4), email="same@example.com",
            phone="0771234567")
        row = self._row(registration_number="SAME-1", first_name="Same",
                        last_name="Data", programme="DIT",
                        date_of_birth="2008-03-04", email="same@example.com",
                        phone="0771234567")
        same = self._batch([row])
        results, summary = bulk_upload.Planner(same).build()
        self.assertEqual(results[0]["action"], "no_change")
        self.assertEqual(summary["unchanged"], 1)
        written, _errors = self._run(same)
        self.assertEqual(written, 0)

        with_course = self._batch([{**row, "courses": "DIT101", "term": "Term 1"}])
        results, _summary = bulk_upload.Planner(with_course).build()
        self.assertEqual(results[0]["action"], "update")
        written, _errors = self._run(with_course)
        self.assertEqual(written, 1)
        self.assertTrue(Enrollment.objects.filter(student=student).exists())

    def test_tenant_isolation_prevents_matches_across_institutions(self):
        self._existing_student(
            self.other, "other.student", self.other_data.programme,
            number="SHARED-1", email="shared@example.com")
        batch = self._batch(
            [self._base_row(registration_number="SHARED-1",
                            email="shared@example.com")])
        results, _summary = bulk_upload.Planner(batch).build()
        self.assertEqual(results[0]["action"], "create")
        self._run(batch)
        self.assertEqual(Student.all_objects.filter(institution=self.inst).count(), 1)
        self.assertEqual(Student.all_objects.filter(institution=self.other).count(), 1)

    def test_undo_deactivates_user_and_cancels_pending_invitation(self):
        batch = self._batch(
            [self._base_row()],
            access_mode=ImportBatch.AccessMode.INVITATION)
        self._run(batch)
        student = Student.all_objects.get(institution=self.inst)
        user_id = student.user_id
        self.assertTrue(OutboundMessage.all_objects.filter(
            institution=self.inst, status=OutboundMessage.Status.QUEUED,
            email_type="student_invitation").exists())

        undone, skipped = bulk_upload.undo(batch, actor=self.actor)
        self.assertEqual((undone, skipped), (1, []))
        self.assertFalse(Student.all_objects.filter(pk=student.pk).exists())
        self.assertFalse(BulkUploadMatch.objects.filter(student_id=student.pk).exists())
        user = User.objects.get(pk=user_id)
        self.assertFalse(user.is_active)
        self.assertFalse(OutboundMessage.all_objects.filter(
            institution=self.inst, recipient=user.email,
            status=OutboundMessage.Status.QUEUED,
            email_type="student_invitation").exists())

    def test_undo_keeps_a_created_invoice_and_its_user_active(self):
        self.data.programme.tuition_fee = Decimal("120.00")
        self.data.programme.save(update_fields=["tuition_fee"])
        batch = self._batch([self._base_row()], options={"first_invoice": True})
        self._run(batch)
        student = Student.all_objects.get(institution=self.inst)
        self.assertTrue(Invoice.all_objects.filter(student=student).exists())

        undone, skipped = bulk_upload.undo(batch, actor=self.actor)
        self.assertEqual(undone, 0)
        self.assertEqual(len(skipped), 1)
        student.refresh_from_db()
        student.user.refresh_from_db()
        self.assertTrue(Student.all_objects.filter(pk=student.pk).exists())
        self.assertTrue(student.user.is_active)
        self.assertTrue(Invoice.all_objects.filter(student=student).exists())

    def test_undo_reverts_supplied_updates_and_skips_later_edits(self):
        student = self._existing_student(
            self.inst, "update.me", self.data.programme, number="UPDATE-1",
            email="old@example.com", phone="0771111111",
            first_name="Tariro", last_name="Dube",
            guardian_name="Mrs Dube", guardian_phone="0773333333",
            year_or_grade="2")
        row = self._row(
            registration_number="UPDATE-1", first_name="Changed",
            email="new@example.com", phone="0772222222",
            guardian_name="Mr New", year_or_grade="4", programme="DIT")
        batch = self._batch([row], options={"update_year_grade": True})

        written, errors = self._run(batch)
        self.assertEqual((written, errors), (1, []))
        student.refresh_from_db()
        student.user.refresh_from_db()
        self.assertEqual(student.user.first_name, "Changed")
        self.assertEqual(student.user.email, "new@example.com")
        self.assertEqual(student.user.phone, "0772222222")
        self.assertEqual(student.guardian_name, "Mr New")
        self.assertEqual(student.year_or_grade, "4")

        undone, skipped = bulk_upload.undo(batch, actor=self.actor)
        self.assertEqual((undone, skipped), (1, []))
        student.refresh_from_db()
        student.user.refresh_from_db()
        self.assertEqual(student.user.first_name, "Tariro")
        self.assertEqual(student.user.email, "old@example.com")
        self.assertEqual(student.user.phone, "0771111111")
        self.assertEqual(student.guardian_name, "Mrs Dube")
        self.assertEqual(student.guardian_phone, "0773333333")
        self.assertEqual(student.year_or_grade, "2")

        # A later edit wins: undo must not overwrite work done after the import.
        later = self._batch([row], options={"update_year_grade": True})
        self._run(later)
        student.user.phone = "0779999999"
        student.user.save(update_fields=["phone"])
        undone, skipped = bulk_upload.undo(later, actor=self.actor)
        self.assertEqual(undone, 0)
        self.assertEqual(len(skipped), 1)
        student.user.refresh_from_db()
        self.assertEqual(student.user.phone, "0779999999")

    def test_deliver_invitations_is_rate_limited_in_chunks(self):
        batch = self._batch([
            self._base_row(email="first@example.com"),
            self._base_row(email="second@example.com", last_name="Other"),
        ], access_mode=ImportBatch.AccessMode.INVITATION)
        self._run(batch)  # perform() queues the invitations once
        self.assertEqual(OutboundMessage.all_objects.filter(
            institution=self.inst, status=OutboundMessage.Status.QUEUED).count(), 2)

        with override_settings(BULK_UPLOAD_EMAIL_CHUNK=1):
            sent = bulk_upload.deliver_invitations(batch)
        self.assertEqual(sent, 1)
        self.assertEqual(OutboundMessage.all_objects.filter(
            institution=self.inst, status=OutboundMessage.Status.QUEUED).count(), 1)


class FileAndViewTests(TestCase):
    """The screens use the default institution that ``_institution`` resolves."""

    def setUp(self):
        self.inst = Institution.objects.filter(slug="default").first()
        if self.inst is None:
            self.inst = Institution.objects.create(
                name="Default College", slug="default", type="college")
        self.roles = ensure_roles(self.inst)
        self.actor = BulkUploadBase._user(
            "view-reg", self.inst, role="registrar",
            primary_role=self.roles["registrar"])
        self.data = BulkUploadBase._structure(self.inst, code="VIEW")

    def _batch(self, rows, *, options=None, access_mode=None):
        fields = importer.FIELDS_BY_KIND[bulk_upload.KIND]
        header = {field["key"]: field["label"] for field in fields}
        mapping = {field["key"]: field["key"] for field in fields}
        return ImportBatch.objects.create(
            institution=self.inst,
            kind=bulk_upload.KIND,
            filename="students.csv",
            rows=[header] + list(rows),
            mapping=mapping,
            options={**bulk_upload.DEFAULT_OPTIONS, **(options or {})},
            created_by=self.actor,
            status=ImportBatch.Status.UPLOADED,
            access_mode=access_mode or ImportBatch.AccessMode.NO_LOGIN,
        )

    def _run(self, batch):
        return bulk_upload.perform(batch, self.actor)

    @staticmethod
    def _user(username, institution, *, role="student", email="", **extra):
        return User.objects.create_user(
            username, email=email, password=None, role=role,
            institution=institution, **extra)

    @staticmethod
    def _row(**values):
        return dict(values)

    def _base_row(self, **extra):
        row = self._row(
            first_name="Tariro", last_name="Dube",
            date_of_birth="2008-03-04", programme="VIEW",
            email="tariro.dube@example.com", phone="0771234567")
        row.update(extra)
        return row

    def _login(self, user):
        self.client.force_login(user)

    def test_reader_accepts_aliases_and_ignores_blank_and_padded_rows(self):
        text = ("Reg No,First Name,Surname,DOB,Programme,Courses\n"
                "  ,  ,  ,  ,  ,  \n"
                " STU-1 , Tariro , Dube , 2008-03-04 , DIT , DIT101;DIT102 \n")
        rows = importer.read_rows(csv_upload(text), "students.csv")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["Reg No"], "STU-1")
        self.assertEqual(rows[1]["First Name"], "Tariro")
        self.assertEqual(rows[1]["Courses"], "DIT101;DIT102")
        mapping = importer.guess_mapping(
            list(rows[0].keys()), importer.FIELDS_BY_KIND[bulk_upload.KIND])
        self.assertEqual(mapping["registration_number"], "Reg No")
        self.assertEqual(mapping["courses"], "Courses")

    def test_reader_checks_file_type_and_row_limit(self):
        fake = SimpleUploadedFile(
            "fake.xlsx", b"not a workbook",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with self.assertRaises(importer.ImportError):
            importer.read_rows(fake, "fake.xlsx")
        with override_settings(BULK_UPLOAD_MAX_ROWS=1):
            with self.assertRaises(importer.ImportError):
                importer.read_rows(csv_upload(
                    "First Name,Surname\nA,One\nB,Two\n"), "students.csv")

    def test_upload_keeps_default_options_and_preview_report_works(self):
        self._login(self.actor)
        text = ("First Name,Surname,Programme,Registration number\n"
                ",Dube,DIT,STU-9\n")
        response = self.client.post(reverse("bulk_upload_start"), {
            "file": csv_upload(text), "access_mode": "none"})
        self.assertEqual(response.status_code, 302)
        batch = ImportBatch.objects.latest("pk")
        self.assertEqual(batch.options, bulk_upload.DEFAULT_OPTIONS)

        preview = self.client.get(reverse("bulk_upload_preview", args=[batch.pk]))
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.context["summary"]["errors"], 1)

        report = self.client.get(
            reverse("bulk_upload_report", args=[batch.pk]) + "?type=errors")
        self.assertEqual(report.status_code, 200)
        self.assertIn(b"Surname", report.content)

    def test_upload_enforces_configured_byte_limit(self):
        self._login(self.actor)
        with override_settings(BULK_UPLOAD_MAX_BYTES=10):
            response = self.client.post(reverse("bulk_upload_start"), {
                "file": csv_upload("First Name,Surname\nA,One\n"),
                "access_mode": "none"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ImportBatch.objects.exists())

    def test_permission_is_required(self):
        lecturer = self._user("lecturer", self.inst, role="lecturer")
        self._login(lecturer)
        self.assertEqual(
            self.client.get(reverse("bulk_upload_start")).status_code, 403)
        self._login(self.actor)
        self.assertEqual(
            self.client.get(reverse("bulk_upload_start")).status_code, 200)

    def test_excel_has_the_documented_columns_example_and_instructions(self):
        self._login(self.actor)
        response = self.client.get(reverse("bulk_upload_template"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "attachment", response.headers["Content-Disposition"])
        book = load_workbook(BytesIO(response.content))
        self.assertEqual(book.sheetnames, ["Students", "Instructions"])
        sheet = book["Students"]
        headers = [cell.value for cell in sheet[1]]
        self.assertEqual(
            headers,
            [field["label"] for field in importer.FIELDS_BY_KIND[bulk_upload.KIND]],
        )
        self.assertEqual(sheet["A2"].value, "STU-2026-0001")
        self.assertEqual(book["Instructions"]["A1"].value, "How to use this template")

    def test_results_file_shows_credentials_once_and_audit_stays_clean(self):
        batch = self._batch(
            [self._base_row()],
            access_mode=ImportBatch.AccessMode.TEMPORARY_PASSWORD)
        self._run(batch)
        password = batch.credentials[0]["password"]
        self._login(self.actor)

        first = self.client.get(reverse("bulk_upload_results", args=[batch.pk]))
        self.assertEqual(first.status_code, 200)
        self.assertIn(password.encode(), first.content)
        batch.refresh_from_db()
        self.assertEqual(batch.credentials, [])
        self.assertTrue(batch.credentials_downloaded)

        second = self.client.get(reverse("bulk_upload_results", args=[batch.pk]))
        self.assertNotIn(password.encode(), second.content)
        for entry in AuditEntry.objects.filter(institution=self.inst):
            self.assertNotIn(password, json.dumps(entry.after or {}))
            self.assertNotIn(password, json.dumps(entry.before or {}))

    def test_run_requires_removal_confirmation(self):
        self._login(self.actor)
        text = ("First Name,Surname,Programme,Registration number\n"
                "A,One,DIT,STU-1\n")
        self.client.post(reverse("bulk_upload_start"), {
            "file": csv_upload(text), "access_mode": "none"})
        batch = ImportBatch.objects.latest("pk")
        response = self.client.post(reverse("bulk_upload_run", args=[batch.pk]), {
            "remove_enrolments": "on", "apply_rules": "on"})
        self.assertEqual(response.status_code, 302)
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.UPLOADED)
        self.assertFalse(Student.all_objects.filter(institution=self.inst).exists())

    @patch("core.bulk_upload.threading.Thread")
    def test_background_fallback_starts_a_daemon_thread(self, thread):
        job = jobs.create_job(
            "import", self.inst, self.actor, label="Upload", total=1,
            object_id=1)
        batch = self._batch([self._base_row()])
        returned = bulk_upload.start_background(batch, self.actor, job)
        self.assertEqual(returned, job)
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs["daemon"])

    def test_result_page_links_to_undo(self):
        batch = self._batch([self._base_row()])
        self._run(batch)
        self._login(self.actor)
        response = self.client.get(reverse("bulk_upload_result", args=[batch.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Undo")
