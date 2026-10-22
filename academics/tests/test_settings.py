"""SchoolSettings.get() must work with and without an institution argument."""

from django.test import TestCase

from academics.models import SchoolSettings
from core.models import Institution
from core.tenancy import use_institution


class SchoolSettingsGetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.first = Institution.objects.create(
            name="Riverdale College", slug="riverdale", type="college")
        cls.second = Institution.objects.create(
            name="Hillcrest School", slug="hillcrest", type="secondary")

    def test_get_without_an_argument_uses_the_current_institution(self):
        with use_institution(self.first):
            row = SchoolSettings.get()
        self.assertEqual(row.institution_id, self.first.pk)

    def test_get_with_an_institution_returns_that_institutions_row(self):
        row = SchoolSettings.get(self.second)
        self.assertEqual(row.institution_id, self.second.pk)

    def test_get_accepts_an_institution_pk(self):
        row = SchoolSettings.get(self.second.pk)
        self.assertEqual(row.institution_id, self.second.pk)

    def test_get_creates_the_row_only_once(self):
        first = SchoolSettings.get(self.first)
        second = SchoolSettings.get(self.first)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(SchoolSettings.all_objects.filter(institution=self.first).count(), 1)

    def test_get_works_when_another_institution_is_current(self):
        with use_institution(self.first):
            row = SchoolSettings.get(self.second)
        self.assertEqual(row.institution_id, self.second.pk)
        self.assertEqual(SchoolSettings.all_objects.count(), 1)

    def test_each_institution_gets_its_own_row(self):
        first = SchoolSettings.get(self.first)
        second = SchoolSettings.get(self.second)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.mode, SchoolSettings.Mode.COLLEGE)

    def test_the_tenant_manager_only_shows_the_current_institution(self):
        with use_institution(self.first):
            SchoolSettings.get(self.first)
            self.assertEqual(SchoolSettings.objects.count(), 1)
        with use_institution(self.second):
            SchoolSettings.get(self.second)
            self.assertEqual(SchoolSettings.objects.count(), 1)
            self.assertEqual(SchoolSettings.objects.first().institution_id, self.second.pk)

    def test_switching_the_mode_of_one_institution_leaves_the_other_alone(self):
        with use_institution(self.first):
            row = SchoolSettings.get(self.first)
            row.mode = SchoolSettings.Mode.SCHOOL
            row.save(update_fields=["mode"])
        with use_institution(self.second):
            other = SchoolSettings.get(self.second)
            self.assertEqual(other.mode, SchoolSettings.Mode.COLLEGE)

    def test_get_without_an_institution_falls_back_to_the_default_institution(self):
        from core.models import Institution as Inst

        default = Inst.objects.filter(slug="default").first()
        row = SchoolSettings.get()
        self.assertEqual(row.institution_id, default.pk)
