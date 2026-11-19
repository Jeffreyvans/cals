"""Forms for the public application and the platform consent screens."""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounts.models import SignupLink
from accounts.username_fields import USERNAME_FORMAT_HINT, UsernameField
from core.models import Institution
from core.modules import ALL_MODULES, MODULE_LABELS
from core.slugs import normalize_domain, normalize_slug, validate_slug

from .models import (
    ACCESS_CATEGORIES,
    AccessRequest,
    Plan,
    PlatformAnnouncement,
    PlatformInvoice,
    PlatformPayment,
    PlatformRole,
    Subscription,
)
from .uploads import sanitise_upload

User = get_user_model()


class InstitutionApplicationForm(forms.Form):
    """The public "Apply to use EduSphere" form.

    The form deliberately does not say whether an institution already exists.
    A school with an existing site can still apply; staff will merge the two.
    """

    institution_name = forms.CharField(
        max_length=200, label="Institution name",
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
    institution_type = forms.ChoiceField(choices=Institution.Type.choices)
    country = forms.CharField(max_length=100)
    town = forms.CharField(max_length=100)
    registration_number = forms.CharField(max_length=100, required=False)
    expected_students = forms.IntegerField(min_value=0, max_value=1_000_000, initial=0)
    expected_staff = forms.IntegerField(min_value=0, max_value=100_000, initial=0)
    contact_person = forms.CharField(max_length=150, label="Contact person")
    position = forms.CharField(max_length=120, required=False)
    phone = forms.CharField(max_length=40)
    email = forms.EmailField(max_length=254)
    website = forms.URLField(max_length=200, required=False)
    preferred_plan = forms.ModelChoiceField(
        queryset=Plan.objects.none(), required=False, empty_label="No preference"
    )
    modules = forms.MultipleChoiceField(
        choices=[(code, MODULE_LABELS.get(code, code)) for code in ALL_MODULES],
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    registration_certificate = forms.FileField(required=False)
    authorisation_letter = forms.FileField(required=False)
    agreed_to_terms = forms.BooleanField(
        required=True,
        label="I accept the data processing statement and terms.",
    )
    # A hidden field real people never fill in. Bots usually do.
    website_url = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["preferred_plan"].queryset = Plan.objects.filter(is_active=True)
        self.fields["country"].initial = "Zimbabwe"
        for name in ("registration_certificate", "authorisation_letter"):
            self.fields[name].help_text = "PDF, PNG, JPG or WEBP up to 5 MB."

    def clean_website_url(self):
        value = (self.cleaned_data.get("website_url") or "").strip()
        if value:
            raise ValidationError("This form could not be submitted.")
        return value

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()

    def clean(self):
        cleaned = super().clean()
        for field in ("registration_certificate", "authorisation_letter"):
            upload = cleaned.get(field)
            if not upload:
                continue
            try:
                safe = sanitise_upload(upload, stem=field)
            except ValidationError as exc:
                self.add_error(field, exc)
                continue
            # Keep the validated bytes; the service reads from the start again.
            upload.seek(0)
            cleaned[field] = upload
            cleaned[f"_{field}_safe"] = safe
        return cleaned


class AccessRequestForm(forms.Form):
    institution = forms.ModelChoiceField(queryset=Institution.objects.none())
    categories = forms.MultipleChoiceField(
        choices=[(code, label) for code, label, _help in ACCESS_CATEGORIES],
        widget=forms.CheckboxSelectMultiple,
    )
    mode = forms.ChoiceField(choices=AccessRequest.Mode.choices, initial=AccessRequest.Mode.READ)
    include_export = forms.BooleanField(required=False)
    reason = forms.CharField(
        min_length=20,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "At least 20 characters."}),
    )
    reference = forms.CharField(
        max_length=120, required=False,
        label="Related ticket or application reference",
    )
    duration_hours = forms.IntegerField(min_value=1, max_value=168, initial=24)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["institution"].queryset = Institution.objects.filter(
            is_active=True, archived_at__isnull=True, deleted_at__isnull=True
        ).order_by("name")


class EmergencyAccessForm(forms.Form):
    institution = forms.ModelChoiceField(queryset=Institution.objects.none())
    categories = forms.MultipleChoiceField(
        choices=[(code, label) for code, label, _help in ACCESS_CATEGORIES],
        widget=forms.CheckboxSelectMultiple,
    )
    justification = forms.CharField(
        min_length=20,
        widget=forms.Textarea(attrs={"rows": 5}),
        label="Security incident or outage",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["institution"].queryset = Institution.objects.filter(
            archived_at__isnull=True, deleted_at__isnull=True
        ).order_by("name")


class AccessDecisionForm(forms.Form):
    categories = forms.MultipleChoiceField(
        choices=[(code, label) for code, label, _help in ACCESS_CATEGORIES],
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    mode = forms.ChoiceField(choices=AccessRequest.Mode.choices, required=False)
    hours = forms.IntegerField(min_value=1, max_value=168, required=False)
    include_export = forms.BooleanField(required=False)
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class DataProcessingAcceptanceForm(forms.Form):
    accepted = forms.BooleanField(
        required=True,
        label="I accept the data processing statement on behalf of this institution.",
    )
    confirmed_by = forms.CharField(max_length=200, label="Your full name")


class InstitutionEditForm(forms.ModelForm):
    slug = forms.SlugField(
        max_length=100, required=False,
        help_text="Changing this keeps the old address working for 90 days.",
    )

    class Meta:
        model = Institution
        fields = [
            "name", "slug", "type", "address", "contact_email", "contact_phone",
            "website", "public_page_enabled", "access_grant_max_hours",
        ]

    def clean_slug(self):
        value = (self.cleaned_data.get("slug") or "").strip()
        if not value:
            return self.instance.slug
        return validate_slug(value, exclude=self.instance)

    def clean_access_grant_max_hours(self):
        hours = self.cleaned_data.get("access_grant_max_hours") or 24
        return max(1, min(int(hours), 168))


class InstitutionCreateForm(forms.ModelForm):
    """Create a school directly, without waiting for a public application."""

    slug = forms.SlugField(
        max_length=100,
        required=False,
        help_text="Leave blank to build one from the name.",
    )

    class Meta:
        model = Institution
        fields = [
            "name", "type", "address", "contact_email", "contact_phone",
            "website", "public_page_enabled", "access_grant_max_hours",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].initial = ""
        self.fields["contact_email"].required = False
        self.fields["contact_phone"].required = False
        self.fields["website"].required = False
        self.fields["address"].required = False

    def clean_slug(self):
        value = (self.cleaned_data.get("slug") or "").strip()
        value = value or normalize_slug(self.cleaned_data.get("name", ""))
        return validate_slug(value)

    def clean_access_grant_max_hours(self):
        hours = self.cleaned_data.get("access_grant_max_hours") or 24
        return max(1, min(int(hours), 168))

    def save(self, commit=True):
        institution = super().save(commit=False)
        institution.slug = self.cleaned_data["slug"]
        if commit:
            institution.save()
        return institution


ACCESS_MODE_CHOICES = (
    ("invite", "Send an invitation email"),
    ("temp_password", "Create a temporary password"),
)


class InstitutionAdministratorForm(forms.Form):
    """Create the first (or another) administrator of an institution."""

    full_name = forms.CharField(max_length=200, label="Full name")
    username = UsernameField()
    email = forms.EmailField(max_length=200, required=False, label="Email address")
    phone = forms.CharField(max_length=30, required=False, label="Phone")
    access_mode = forms.ChoiceField(
        choices=ACCESS_MODE_CHOICES,
        initial="invite",
        widget=forms.RadioSelect,
        label="How should this person get access?",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs["data-username-check-url"] = reverse(
            "username_availability"
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("access_mode") == "invite" and not cleaned.get("email"):
            self.add_error("email", "An invitation needs an email address.")
        return cleaned


class AdministratorUsernameForm(forms.Form):
    """Let a platform administrator rename an institution administrator."""

    username = UsernameField(required=True, help_text=USERNAME_FORMAT_HINT)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        field = self.fields["username"]
        field.exclude_user = user
        field.widget.attrs["data-username-check-url"] = reverse("username_availability")
        if user is not None and user.pk:
            field.widget.attrs["data-username-exclude"] = str(user.pk)


class PlatformSignupLinkForm(forms.Form):
    """Create a shareable sign-up link from the platform onboarding area."""

    label = forms.CharField(max_length=120, required=False)
    kind = forms.ChoiceField(choices=SignupLink.Kind.choices, initial=SignupLink.Kind.ANY)
    position = forms.CharField(max_length=40, required=False)
    expires_days = forms.IntegerField(
        min_value=1, max_value=365, required=False,
        help_text="Leave empty for a link that never expires.",
    )
    max_uses = forms.IntegerField(
        min_value=1, required=False,
        help_text="Leave empty for unlimited uses.",
    )


class InstitutionDomainForm(forms.Form):
    """The optional per-institution custom domain."""

    custom_domain = forms.CharField(
        max_length=253, required=False, label="Custom domain",
        help_text="For example learn.example.org, without https://.",
    )

    def clean_custom_domain(self):
        value = (self.cleaned_data.get("custom_domain") or "").strip()
        return normalize_domain(value) if value else ""


class PlanForm(forms.ModelForm):
    # The model stores a JSON list, but people choose module names, so the form
    # owns a multiple-choice field and writes a plain list back to the model.
    modules = forms.MultipleChoiceField(
        choices=[(code, MODULE_LABELS.get(code, code)) for code in ALL_MODULES],
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Plan
        fields = [
            "code", "name", "description", "monthly_price", "annual_price",
            "currency", "trial_days", "max_students", "max_staff", "modules",
            "is_active", "ordering",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def clean_modules(self):
        selected = self.cleaned_data.get("modules") or []
        return [code for code in ALL_MODULES if code in set(selected)]


class SubscriptionForm(forms.ModelForm):
    class Meta:
        model = Subscription
        fields = [
            "institution", "plan", "status", "billing_cycle", "trial_ends_at",
            "current_period_start", "current_period_end", "seats", "notes",
        ]
        widgets = {
            "trial_ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "current_period_start": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "current_period_end": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class PlatformInvoiceForm(forms.ModelForm):
    class Meta:
        model = PlatformInvoice
        fields = [
            "institution", "subscription", "number", "description", "amount",
            "currency", "status", "issued_on", "due_on", "paid_on",
        ]
        widgets = {
            "issued_on": forms.DateInput(attrs={"type": "date"}),
            "due_on": forms.DateInput(attrs={"type": "date"}),
            "paid_on": forms.DateInput(attrs={"type": "date"}),
        }


class PlatformPaymentForm(forms.ModelForm):
    class Meta:
        model = PlatformPayment
        fields = ["invoice", "amount", "method", "reference", "paid_at"]
        widgets = {"paid_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


class PlatformRoleForm(forms.ModelForm):
    class Meta:
        model = PlatformRole
        fields = ["code", "name", "description", "permissions"]


class PlatformStaffForm(forms.Form):
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    role = forms.ModelChoiceField(queryset=PlatformRole.objects.all())
    is_main_admin = forms.BooleanField(required=False)
    is_active = forms.BooleanField(required=False, initial=True)

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = PlatformAnnouncement
        fields = ["title", "body", "audience", "is_active"]
        widgets = {"body": forms.Textarea(attrs={"rows": 4})}


class TwoFactorForm(forms.Form):
    code = forms.CharField(max_length=10, label="Authenticator code")

    def clean_code(self):
        value = (self.cleaned_data.get("code") or "").replace(" ", "").strip()
        if not value.isdigit() or len(value) != 6:
            raise ValidationError("Enter the six-digit code from your authenticator app.")
        return value


class PasswordConfirmForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, label="Your password")
