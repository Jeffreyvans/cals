"""Background jobs: progress, tenant isolation and the inline fallback."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import Programme
from core import jobs
from core.models import BackgroundJob, ImportBatch, Institution
from core.roles import ensure_roles
from core.tasks import run_import_job
from core.tenancy import use_institution

User = get_user_model()

ROWS = [
    {"First Name": "First name", "Last Name": "Last name", "Programme": "Programme"},
    {"First Name": "Tariro", "Last Name": "Dube", "Programme": "DIT"},
    {"First Name": "Blessing", "Last Name": "Sibanda", "Programme": "DIT"},
]
MAPPING = {"first_name": "First Name", "last_name": "Last Name",
           "programme": "Programme"}


class BackgroundJobTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha School", slug="alpha", type="college")
        ensure_roles(cls.inst)
        cls.admin = User.objects.create_user(
            "boss", password="pw123456", role="admin", is_superuser=True,
            institution=cls.inst)
        cls.teacher = User.objects.create_user(
            "teach", password="pw123456", role="lecturer", institution=cls.inst)
        with use_institution(cls.inst):
            Programme.objects.create(
                institution=cls.inst, name="Diploma in Technology",
                code="DIT", level="diploma")

    def setUp(self):
        with use_institution(self.inst):
            self.batch = ImportBatch.objects.create(
                institution=self.inst, kind="students", filename="students.csv",
                rows=ROWS, mapping=MAPPING, created_by=self.admin,
            )

    def test_jobs_run_inline_without_a_broker(self):
        self.assertFalse(jobs.jobs_available())

    def test_perform_import_writes_the_rows_and_reports_progress(self):
        job = jobs.create_job(
            "import", self.inst, self.admin, label="Students",
            total=2, object_id=self.batch.pk)
        imported, errors = jobs.perform_import(self.batch, self.admin, job=job)

        self.assertEqual(imported, 2)
        self.assertEqual(errors, [])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.imported_count, 2)
        self.assertEqual(self.batch.status, ImportBatch.Status.IMPORTED)
        job.refresh_from_db()
        self.assertEqual(job.processed, 2)
        self.assertEqual(job.total, 2)
        self.assertEqual(job.percent, 100)

    def test_the_import_task_finishes_the_job(self):
        job = jobs.create_job(
            "import", self.inst, self.admin, label="Students",
            total=2, object_id=self.batch.pk)

        run_import_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.status, BackgroundJob.Status.DONE)
        self.assertEqual(job.result["imported"], 2)
        self.assertEqual(job.result["url"], f"/imports/{self.batch.pk}/result/")

    def test_the_creator_can_watch_the_job(self):
        job = jobs.create_job("import", self.inst, self.admin, label="Students")
        self.client.force_login(self.admin)

        page = self.client.get(reverse("job_detail", args=[job.pk]))
        self.assertEqual(page.status_code, 200)

        payload = self.client.get(reverse("job_status", args=[job.pk])).json()
        self.assertEqual(payload["id"], job.pk)
        self.assertEqual(payload["status"], BackgroundJob.Status.QUEUED)

    def test_another_person_cannot_watch_the_job(self):
        job = jobs.create_job("import", self.inst, self.admin, label="Students")
        self.client.force_login(self.teacher)

        response = self.client.get(reverse("job_detail", args=[job.pk]))

        self.assertEqual(response.status_code, 403)

    def test_another_institution_cannot_see_the_job(self):
        job = jobs.create_job("import", self.inst, self.admin, label="Students")
        other = Institution.objects.create(
            name="Beta School", slug="beta", type="secondary")
        ensure_roles(other)
        outsider = User.objects.create_user(
            "outsider", password="pw123456", role="admin", is_superuser=True,
            institution=other)
        self.client.force_login(outsider)

        response = self.client.get(reverse("job_detail", args=[job.pk]))

        self.assertEqual(response.status_code, 404)

    def test_a_job_without_a_file_has_no_download(self):
        job = jobs.create_job("pdf", self.inst, self.admin, label="Cards")
        self.client.force_login(self.admin)

        response = self.client.get(reverse("job_download", args=[job.pk]))

        self.assertEqual(response.status_code, 404)
