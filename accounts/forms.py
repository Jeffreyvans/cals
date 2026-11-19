"""Forms for the public registration and contact pages."""

import re

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from core.models import Department, Institution

from .models import User
from .registration import (
    CONTACT_EMAIL,
    STAFF_POSITIONS,
    allowed_staff_positions,
    position_options,
    public_institutions,
)
from .usernames import USERNAME_FORMAT_HINT, normalize_identifier, validate_username
from .validators import add_password_errors

PHONE_RE = re.compile(r"^\+?[0-9][0-9\s().-]{5,19}$")
IDENTIFIER_INVALID_MESSAGE = "Wrong email, username or password. Try again."
IDENTIFIER_AMBIGUOUS_MESSAGE = (
    "This email address is used by more than one account. "
    "Enter your username to sign in, or use your institution's sign-in page."
)


def _identifier_candidates(identifier):
    value = normalize_identifier(identifier)
    if not value:
        return User.objects.none(), value
    if "@" in value:
        queryset = User.objects.filter(email__iexact=value)
        # A username could in theory contain '@', so include an exact username match too.
        queryset = queryset | User.objects.filter(username__iexact=value)
        return queryset.distinct(), value
    return User.objects.filter(username__iexact=value), value


def _resolve_main_login_user(identifier):
    candidates, value = _identifier_candidates(identifier)
    users = list(candidates[:3])
    if not users:
        return None, value, "invalid"
    if len(users) == 1:
        return users[0], value, "ok"
    # Multiple matches: allow if they all resolve to the same account (unlikely but safe),
    # or if a username match is unambiguous.
    username_matches = [u for u in users if normalize_identifier(u.username).lower() == value.lower()]
    if len(username_matches) == 1:
        return username_matches[0], value, "ok"
    institutions = {u.institution_id for u in users}
    if len(institutions) > 1:
        return None, value, "ambiguous"
    # Same institution, multiple accounts sharing an email: cannot safely disambiguate on main login.
    return None, value, "ambiguous"


def _resolve_institution_login_user(identifier, institution):
    candidates, value = _identifier_candidates(identifier)
    users = list(candidates)
    if institution is None:
        if len(users) == 1:
            return users[0], value
        return None, value
    allowed = [u for u in users if u.institution_id == institution.pk or getattr(u, "is_superuser", False)]
    if len(allowed) == 1:
        return allowed[0], value
    # If multiple, prefer a username match inside the institution.
    username_matches = [
        u for u in allowed
        if normalize_identifier(u.username).lower() == value.lower()
    ]
    if len(username_matches) == 1:
        return username_matches[0], value
    return None, value


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


class IdentifierAuthenticationForm(AuthenticationForm):
    """Sign in with either a username or an email address."""

    resolved_user = None
    ambiguous_identifier = False

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        self.fields["username"].label = "Email or username"
        self.fields["username"].widget.attrs.setdefault("autocomplete", "username")
        self.fields["username"].widget.attrs.setdefault(
            "placeholder", "Email or username"
        )

    def clean(self):
        identifier = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if not identifier or not password:
            return self.cleaned_data
        user, _normalized, status = _resolve_main_login_user(identifier)
        if status == "ambiguous":
            self.resolved_user = None
            self.ambiguous_identifier = True
            raise ValidationError(IDENTIFIER_AMBIGUOUS_MESSAGE, code="ambiguous_identifier")
        if status != "ok" or user is None:
            self.resolved_user = None
            raise self.get_invalid_login_error()
        self.resolved_user = user
        from .user_admin import is_locked

        if is_locked(user):
            raise self.get_invalid_login_error()
        self.user_cache = authenticate(
            self.request, username=user.username, password=password
        )
        if self.user_cache is None:
            raise self.get_invalid_login_error()
        self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data

    def get_invalid_login_error(self):
        return ValidationError(IDENTIFIER_INVALID_MESSAGE, code="invalid_login")


class InstitutionAuthenticationForm(AuthenticationForm):
    """Sign-in that only accepts accounts belonging to one institution.

    The identifier may be a username or an email address. A valid account for a
    different institution is refused with the same message as a wrong password.
    The page never reveals whether the account exists somewhere else.
    """

    resolved_user = None

    def __init__(self, request=None, *args, institution=None, **kwargs):
        self.institution = institution
        super().__init__(request, *args, **kwargs)
        self.fields["username"].label = "Email or username"
        self.fields["username"].widget.attrs.setdefault("autocomplete", "username")
        self.fields["username"].widget.attrs.setdefault(
            "placeholder", "Email or username"
        )

    def clean(self):
        identifier = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if not identifier or not password:
            return self.cleaned_data
        user, _normalized = _resolve_institution_login_user(identifier, self.institution)
        if user is None:
            self.resolved_user = None
            raise self.get_invalid_login_error()
        self.resolved_user = user
        from .user_admin import is_locked

        if is_locked(user):
            raise self.get_invalid_login_error()
        self.user_cache = authenticate(
            self.request, username=user.username, password=password
        )
        if self.user_cache is None:
            raise self.get_invalid_login_error()
        self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if getattr(user, "is_superuser", False):
            return
        institution = self.institution
        if institution is not None and user.institution_id != institution.pk:
            raise self.get_invalid_login_error()

    def get_invalid_login_error(self):
        return ValidationError(IDENTIFIER_INVALID_MESSAGE, code="invalid_login")


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
        help_text=f"You will use this to sign in. {USERNAME_FORMAT_HINT}",
    )
    password = forms.CharField(
        label="Choose a password",
        widget=forms.PasswordInput,
        help_text="At least 10 characters with capital and small letters and a number.",
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, institution=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.locked_institution = institution
        if institution is not None:
            # A sign-up link locks the form to its own institution.
            field = self.fields["institution"]
            field.queryset = Institution.objects.filter(pk=institution.pk)
            field.widget = forms.HiddenInput()
            field.initial = institution.pk
            field.help_text = ""

    def clean_registration_number(self):
        return " ".join(self.cleaned_data["registration_number"].split())

    def clean_surname(self):
        return " ".join(self.cleaned_data["surname"].split())

    def clean_contact(self):
        return _clean_contact(self.cleaned_data.get("contact"))

    def clean_username(self):
        try:
            return validate_username(self.cleaned_data.get("username"))
        except ValidationError as exc:
            raise ValidationError(exc.messages) from exc

    def clean(self):
        cleaned = super().clean()
        add_password_errors(
            self,
            "password",
            cleaned.get("password"),
            username=cleaned.get("username", ""),
            email=cleaned.get("contact", ""),
        )
        if cleaned.get("website"):
            self.add_error(
                None,
                "We could not create this account. Check your details and try again.",
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
        help_text=f"You will use this to sign in after approval. {USERNAME_FORMAT_HINT}",
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

    def __init__(self, *args, institution=None, positions=None, **kwargs):
        super().__init__(*args, **kwargs)
        locked = institution
        if locked is None:
            raw_institution = None
            if self.is_bound:
                raw_institution = self.data.get("institution")
            elif self.initial.get("institution"):
                raw_institution = self.initial.get("institution")
            if raw_institution:
                locked = public_institutions().filter(pk=raw_institution).first()
        self.institution = locked

        if locked is not None:
            # A sign-up link locks the form to its own institution.
            field = self.fields["institution"]
            field.queryset = Institution.objects.filter(pk=locked.pk)
            field.widget = forms.HiddenInput()
            field.initial = locked.pk
            field.help_text = ""

        # Only offer the positions this institution (or link) has switched on.
        # The server still re-checks the submitted code, because a visitor can
        # edit the HTML before posting.
        if positions is not None:
            allowed = set(positions)
        elif locked is not None:
            allowed = set(allowed_staff_positions(locked))
        else:
            allowed = set()
        self.allowed_positions = allowed
        if locked is not None:
            self.fields["position"].choices = [
                ("", "Choose a position"),
                *[
                    (item["code"], item["label"])
                    for item in position_options()
                    if item["code"] in allowed
                ],
            ]

        if locked is not None:
            self.fields["department"].queryset = Department.all_objects.filter(
                institution=locked
            ).order_by("name")
        else:
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
        try:
            return validate_username(self.cleaned_data.get("username"))
        except ValidationError as exc:
            raise ValidationError(exc.messages) from exc

    def clean(self):
        cleaned = super().clean()
        add_password_errors(
            self,
            "password",
            cleaned.get("password"),
            username=cleaned.get("username", ""),
            email=cleaned.get("email", ""),
        )
        if cleaned.get("website"):
            self.add_error(
                None, "We could not send this request. Check your details and try again."
            )
        institution = cleaned.get("institution")
        position = cleaned.get("position")
        department = cleaned.get("department")
        if institution and position:
            if position in STAFF_POSITIONS and position not in self.allowed_positions:
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


class DemoRequestForm(forms.Form):
    """The pricing page's "Request a demo" form."""

    name = forms.CharField(label="Your name", max_length=120)
    institution = forms.CharField(label="Institution", max_length=200)
    email = forms.EmailField(label="Email address", max_length=200)
    phone = forms.CharField(
        label="Phone number (optional)",
        max_length=30,
        required=False,
    )
    message = forms.CharField(
        label="What would you like to see?",
        widget=forms.Textarea(attrs={"rows": 4}),
        max_length=2000,
        required=False,
        help_text="For example: fees, results, the parent portal or admissions.",
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_message(self):
        return (self.cleaned_data.get("message") or "").strip()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):
            raise ValidationError(
                "We could not send this request. Check your details and try again."
            )
        return cleaned
