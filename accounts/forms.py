"""Forms for the public registration and contact pages."""

import re

from django import forms
from django.core.exceptions import ValidationError

from core.models import Department

from .registration import (
    CONTACT_EMAIL,
    STAFF_POSITIONS,
    allowed_staff_positions,
    position_options,
    public_institutions,
)
from .validators import validate_strong_password

PHONE_RE = re.compile(r"^\+?[0-9][0-9\s().-]{5,19}$")


def _institution_field():
    return forms.ModelChoiceField(
        queryset=public_institutions(),
        empty_label="Choose your institution",
        label="Institution",
        widget=forms.Select(attrs={"data-searchable": "true"}),
        help_text="Only institutions that have switched public registration on are listed.",
    )


def _clean_contact(contact):
    """Accept one field that may be an email address or a phone number."""
    contact = " ".join((contact or "").split()).strip()
    if not contact:
        raise ValidationError("Enter your email address or phone number.")
    if "@" in contact:
        try:
            forms.EmailField().clean(contact)
        except ValidationError as exc:
            raise ValidationError(
                "Enter a valid email address, or use your phone number instead."
            ) from exc
        return contact.lower()
    if not PHONE_RE.match(contact):
        raise ValidationError(
            "Enter a valid phone number, for example 0771234567, or an email address."
        )
    return contact


class StudentRegistrationForm(forms.Form):
    institution = _institution_field()
    registration_number = forms.CharField(
        label="Registration number",
        max_length=30,
        help_text="The student number on your admission letter.",
    )
    surname = forms.CharField(label="Surname", max_length=120)
    date_of_birth = forms.DateField(
        label="Date of birth",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    contact = forms.CharField(
        label="Email or phone number",
        max_length=120,
        help_text="We use this to confirm your account and to reach you.",
    )
    username = forms.CharField(
        label="Choose a username",
        max_length=60,
        help_text="You will use this to sign in.",
    )
    password = forms.CharField(
        label="Choose a password",
        widget=forms.PasswordInput,
        help_text="At least 10 characters with capital and small letters and a number.",
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_registration_number(self):
        return " ".join(self.cleaned_data["registration_number"].split())

    def clean_surname(self):
        return " ".join(self.cleaned_data["surname"].split())

    def clean_contact(self):
        return _clean_contact(self.cleaned_data.get("contact"))

    def clean_username(self):
        username = " ".join(self.cleaned_data["username"].split())
        if not re.match(r"^[A-Za-z0-9._-]{3,60}$", username):
            raise ValidationError(
                "Use 3 to 60 letters, numbers, dots, underscores or hyphens."
            )
        return username

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_strong_password(
            password,
            self.cleaned_data.get("username", ""),
            self.cleaned_data.get("contact", ""),
        )
        return password

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):
            raise ValidationError(
                "We could not create this account. Check your details and try again."
            )
        return cleaned


class StaffRegistrationForm(forms.Form):
    institution = _institution_field()
    full_name = forms.CharField(label="Full name", max_length=150)
    email = forms.EmailField(label="Email address", max_length=200)
    phone = forms.CharField(label="Phone number", max_length=30)
    position = forms.ChoiceField(
        label="Position",
        choices=[("", "Choose a position")] + [
            (item["code"], item["label"]) for item in position_options()
        ],
        help_text="An administrator will confirm this before your account is activated.",
    )
    department = forms.ModelChoiceField(
        label="Department (if relevant)",
        queryset=Department.all_objects.none(),
        required=False,
        empty_label="No department yet",
    )
    username = forms.CharField(
        label="Choose a username",
        max_length=60,
        help_text="You will use this to sign in after approval.",
    )
    password = forms.CharField(
        label="Choose a password",
        widget=forms.PasswordInput,
        help_text="At least 10 characters with capital and small letters and a number.",
    )
    staff_no = forms.CharField(
        label="Staff number (optional)", max_length=30, required=False
    )
    message = forms.CharField(
        label="Message to the administrator (optional)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        institution = None
        raw_institution = None
        if self.is_bound:
            raw_institution = self.data.get("institution")
        elif self.initial.get("institution"):
            raw_institution = self.initial.get("institution")
        if raw_institution:
            institution = public_institutions().filter(pk=raw_institution).first()
        self.institution = institution

        # Only offer the positions this institution has switched on. The
        # server still re-checks the submitted code, because a visitor can
        # edit the HTML before posting.
        if institution is not None:
            allowed = set(allowed_staff_positions(institution))
            self.fields["position"].choices = [
                ("", "Choose a position"),
                *[
                    (item["code"], item["label"])
                    for item in position_options()
                    if item["code"] in allowed
                ],
            ]

        self.fields["department"].queryset = Department.all_objects.filter(
            institution__public_registration=True
        ).select_related("institution").order_by("institution__name", "name")

        def label(department):
            return f"{department.name} ({department.institution.name})"

        self.fields["department"].label_from_instance = label

    def clean_full_name(self):
        return " ".join(self.cleaned_data["full_name"].split())

    def clean_phone(self):
        phone = " ".join(self.cleaned_data["phone"].split())
        if not PHONE_RE.match(phone):
            raise ValidationError("Enter a valid phone number, for example 0771234567.")
        return phone

    def clean_username(self):
        username = " ".join(self.cleaned_data["username"].split())
        if not re.match(r"^[A-Za-z0-9._-]{3,60}$", username):
            raise ValidationError(
                "Use 3 to 60 letters, numbers, dots, underscores or hyphens."
            )
        return username

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_strong_password(
            password,
            self.cleaned_data.get("username", ""),
            self.cleaned_data.get("email", ""),
        )
        return password

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):
            raise ValidationError(
                "We could not send this request. Check your details and try again."
            )
        institution = cleaned.get("institution")
        position = cleaned.get("position")
        department = cleaned.get("department")
        if institution and position:
            if position in STAFF_POSITIONS and position not in allowed_staff_positions(
                institution
            ):
                self.add_error(
                    "position",
                    "This institution is not accepting registrations for that position.",
                )
        if institution and department and department.institution_id != institution.pk:
            self.add_error(
                "department", "Choose a department from the selected institution."
            )
        return cleaned


class ContactForm(forms.Form):
    name = forms.CharField(label="Your name", max_length=120)
    email = forms.EmailField(label="Your email address", max_length=200)
    phone = forms.CharField(label="Phone number (optional)", max_length=30, required=False)
    message = forms.CharField(
        label="How can we help?",
        widget=forms.Textarea(attrs={"rows": 5}),
        max_length=4000,
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_message(self):
        message = self.cleaned_data["message"].strip()
        if len(message) < 10:
            raise ValidationError("Tell us a little more so we can help.")
        return message

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):
            raise ValidationError(
                "We could not send this message. Check your details and try again."
            )
        return cleaned

    def recipient(self):
        return CONTACT_EMAIL
