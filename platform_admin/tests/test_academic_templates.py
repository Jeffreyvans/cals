"""Platform screens for editing the versioned academic templates."""

import json

from django.urls import reverse

from academics.hierarchy import template_for
from platform_admin.models import AcademicTemplate, PlatformAuditLog

from .base import PlatformTestBase


class AcademicTemplateScreenTests(PlatformTestBase):
    def setUp(self):
        super().setUp()
        self.url = reverse("platform_admin:academic_templates")

    def test_main_admin_sees_the_template_editor(self):
        self.login(self.main)
        response = self.client.get(self.url, {"code": "university"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Academic templates")
        self.assertContains(response, "University")
        self.assertIn("data_json", response.context)

    def test_publishing_creates_a_new_version_and_is_audited(self):
        self.login(self.main)
        old = AcademicTemplate.current("university")
        data = json.loads(json.dumps(old.data))
        data["levels"][0]["name"] = "Foundation Year"
        data["levels"][0]["code"] = "FY"

        response = self.client.post(self.url, {
            "code": "university", "name": "University (edited)",
            "data": json.dumps(data), "action": "publish"})
        self.assertRedirects(response, f"{self.url}?code=university")

        new = AcademicTemplate.current("university")
        self.assertEqual(new.version, old.version + 1)
        self.assertEqual(new.data["levels"][0]["name"], "Foundation Year")
        self.assertFalse(AcademicTemplate.objects.get(pk=old.pk).is_active)
        self.assertEqual(template_for("university")["levels"][0]["name"],
                         "Foundation Year")
        self.assertTrue(PlatformAuditLog.objects.filter(
            action="platform.academic_template.published",
            record_id=str(new.pk)).exists())

    def test_activating_an_old_version_rolls_back_the_default(self):
        self.login(self.main)
        old = AcademicTemplate.current("university")
        data = json.loads(json.dumps(old.data))
        data["levels"][0]["name"] = "Year Zero"
        AcademicTemplate.publish("university", "University v2", data, actor=self.main)
        newer = AcademicTemplate.current("university")

        response = self.client.post(self.url, {
            "code": "university", "action": "activate", "version": old.pk})
        self.assertRedirects(response, f"{self.url}?code=university")
        self.assertTrue(AcademicTemplate.objects.get(pk=old.pk).is_active)
        self.assertFalse(AcademicTemplate.objects.get(pk=newer.pk).is_active)
        self.assertEqual(template_for("university")["levels"][0]["name"],
                         old.data["levels"][0]["name"])
        self.assertTrue(PlatformAuditLog.objects.filter(
            action="platform.academic_template.activated",
            record_id=str(old.pk)).exists())

    def test_an_invalid_template_body_is_not_published(self):
        self.login(self.main)
        count = AcademicTemplate.objects.filter(code="university").count()
        response = self.client.post(self.url, {
            "code": "university", "name": "Broken", "action": "publish",
            "data": json.dumps({"levels": "not a list"})})
        self.assertRedirects(response, f"{self.url}?code=university")
        self.assertEqual(AcademicTemplate.objects.filter(code="university").count(), count)

    def test_support_staff_cannot_edit_templates(self):
        self.login(self.support)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
