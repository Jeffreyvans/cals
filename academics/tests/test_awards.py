from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from academics.models import AwardRule
from academics.services import award_classification, submit_results

from .test_reports import BaseAssessment


class AwardServiceTests(BaseAssessment):
    def test_default_diploma_awards_follow_the_gpa(self):
        for mark, label in [("80", "Distinction"), ("70", "Merit"),
                            ("60", "Pass"), ("30", "Fail")]:
            self.e_a.mark = Decimal(mark)
            self.assertEqual(award_classification(self.a, [self.e_a]), label, mark)

    def test_degree_awards_start_at_first_class(self):
        self.programme.level = "degree"
        self.programme.save()
        self.a.refresh_from_db()
        self.e_a.mark = Decimal("80")
        self.assertEqual(award_classification(self.a, [self.e_a]), "First Class")
        self.e_a.mark = Decimal("66")
        self.assertEqual(award_classification(self.a, [self.e_a]), "Upper Second Class")

    def test_saved_rules_replace_the_defaults_for_that_level(self):
        AwardRule.objects.create(level="diploma", label="Honours",
                                 min_gpa=Decimal("3.50"))
        self.e_a.mark = Decimal("80")
        self.assertEqual(award_classification(self.a, [self.e_a]), "Honours")
        self.e_a.mark = Decimal("60")
        self.assertIsNone(award_classification(self.a, [self.e_a]))

    def test_generic_rules_apply_when_a_level_has_none(self):
        AwardRule.objects.create(level="", label="Credit", min_gpa=Decimal("2.50"))
        self.e_a.mark = Decimal("80")
        self.assertEqual(award_classification(self.a, [self.e_a]), "Credit")
        self.e_a.mark = Decimal("60")
        self.assertIsNone(award_classification(self.a, [self.e_a]))

    def test_level_rules_take_precedence_over_generic_rules(self):
        AwardRule.objects.create(level="", label="Credit", min_gpa=Decimal("2.00"))
        AwardRule.objects.create(level="diploma", label="Honours",
                                 min_gpa=Decimal("3.50"))
        self.e_a.mark = Decimal("80")
        self.assertEqual(award_classification(self.a, [self.e_a]), "Honours")

    def test_unmarked_student_has_no_award(self):
        self.assertIsNone(award_classification(self.a, [self.e_a]))

    def test_built_in_rules_are_ordered_from_highest_to_lowest(self):
        rules = AwardRule.for_level("degree")
        self.assertEqual([r.label for r in rules],
                         ["First Class", "Upper Second Class", "Lower Second Class",
                          "Third Class", "Fail"])


class AwardRuleValidationTests(BaseAssessment):
    def test_labels_are_case_insensitively_unique_per_level(self):
        AwardRule.objects.create(level="diploma", label="Distinction",
                                 min_gpa=Decimal("3.50"))
        with self.assertRaisesMessage(ValidationError,
                                      "already exists for this level"):
            AwardRule.objects.create(level="diploma", label="distinction",
                                     min_gpa=Decimal("3.00"))

    def test_the_same_label_can_exist_for_another_level(self):
        AwardRule.objects.create(level="diploma", label="Distinction",
                                 min_gpa=Decimal("3.50"))
        AwardRule.objects.create(level="degree", label="Distinction",
                                 min_gpa=Decimal("3.50"))
        self.assertEqual(AwardRule.objects.count(), 2)


class AwardAdminTests(BaseAssessment):
    def test_admin_adds_and_removes_an_award(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("grade_bands"), {
            "action": "award", "level": "diploma", "label": "Honours",
            "min_gpa": "3.50"})
        self.assertRedirects(resp, reverse("grade_bands"))
        rule = AwardRule.objects.get(label="Honours")
        resp = self.client.post(reverse("award_rule_delete", args=[rule.pk]))
        self.assertRedirects(resp, reverse("grade_bands"))
        self.assertFalse(AwardRule.objects.exists())

    def test_duplicate_award_shows_a_form_error(self):
        self.client.force_login(self.admin)
        AwardRule.objects.create(level="diploma", label="Honours",
                                 min_gpa=Decimal("3.50"))
        resp = self.client.post(reverse("grade_bands"), {
            "action": "award", "level": "diploma", "label": "honours",
            "min_gpa": "3.00"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists for this level")

    def test_registrar_cannot_manage_awards(self):
        self.client.force_login(self.registrar)
        self.assertEqual(self.client.get(reverse("grade_bands")).status_code, 403)


class AwardReportTests(BaseAssessment):
    def test_report_shows_the_projected_award(self):
        self.e_a.mark = Decimal("80")
        self.e_a.save()
        self.sa.mark = Decimal("70")
        self.sa.save()
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("my_report"))
        self.assertContains(resp, "Projected award: Distinction")

    def test_hidden_results_do_not_show_an_award(self):
        self.e_a.mark = Decimal("80")
        self.e_a.save()
        submit_results("course", self.course, self.term, self.lecturer)
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("my_report"))
        self.assertNotContains(resp, "Projected award")
        self.assertContains(resp, "Awaiting release")
