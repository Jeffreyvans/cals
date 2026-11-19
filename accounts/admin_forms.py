"""Forms for the administrator Users area."""

from django import forms
from django.contrib.auth.forms import (
    AdminPasswordChangeForm,
    PasswordChangeForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.urls import reverse

from academics.models import Programme, SchoolClass, Stream
from core.models import Department
from finance.models import FeeStructure

from .models import User
from .user_admin import assignable_roles, validate_user_email
from .username_fields import USERNAME_FORMAT_HINT, UsernameField
from .usernames import normalize_email
from .validators import add_password_errors

ACCESS_CHOICES = (
    ("invite", "Send an invitation email"),
    ("temp_password", "Create a temporary password"),
)


def _style(field):
    field.widget.attrs.setdefault("class", "input")
    return field


class InstitutionFormMixin:
    def __init__(self, *args, institution=None, user=None, **kwargs):
        self.institution = institution
        self.actor = user
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            _style(field)
        username_field = self.fields.get("username")
        if username_field is not None:
            username_field.widget.attrs["data-username-check-url"] = reverse(
                "username_availability"
            )
            instance = getattr(self, "instance", None)
            if instance is not None and instance.pk:
                username_field.widget.attrs["data-username-exclude"] = str(instance.pk)
                username_field.exclude_user = instance

    def clean_email(self):
        email = normalize_email(self.cleaned_data.get("email") or "")
        if not email:
            return ""
        try:
            validate_email(email)
        except DjangoValidationError:
            raise forms.ValidationError("Enter a valid email address.") from None
        return email


class StaffAccountForm(InstitutionFormMixin, forms.Form):
    full_name = forms.CharField(max_length=150, label="Full name")
    username = UsernameField()
    email = forms.EmailField(label="Email")
    phone = forms.CharField(max_length=30, required=False)
    role = forms.ModelChoiceField(queryset=None, label="Role")
    departments = forms.ModelMultipleChoiceField(
        queryset=None, required=False, label="Departments"
    )
    job_title = forms.CharField(max_length=120, required=False)
    staff_no = forms.CharField(max_length=30, required=False, label="Staff number")
    start_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    access_mode = forms.ChoiceField(
        choices=ACCESS_CHOICES,
        initial="invite",
        widget=forms.RadioSelect,
        label="How should this person get access?",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = assignable_roles(self.actor)
        self.fields["departments"].queryset = Department.all_objects.filter(
            institution=self.institution
        ).order_by("name")

    def clean_email(self):
        email = normalize_email(self.cleaned_data.get("email") or "")
        if not email:
            return ""
        try:
            return validate_user_email(
                email, institution=self.institution, staff_or_admin=True
            )
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc


class StudentAccountForm(InstitutionFormMixin, forms.Form):
    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, label="Last name")
    email = forms.EmailField(required=False, help_text="Leave empty to use a temporary password.")
    phone = forms.CharField(max_length=30, required=False)
    username = UsernameField()
    programme = forms.ModelChoiceField(queryset=None, label="Programme or class")
    school_class = forms.ModelChoiceField(
        queryset=None, required=False, label="Class (for a school)"
    )
    stream = forms.ModelChoiceField(queryset=None, required=False)
    year_or_grade = forms.CharField(max_length=20, initial="1", label="Year or grade")
    date_of_birth = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    guardian_name = forms.CharField(max_length=120, required=False)
    guardian_phone = forms.CharField(max_length=30, required=False)
    access_mode = forms.ChoiceField(
        choices=ACCESS_CHOICES,
        initial="invite",
        widget=forms.RadioSelect,
        label="How should this person get access?",
    )
    first_invoice = forms.BooleanField(required=False, label="Create the first invoice")
    fee_structure = forms.ModelChoiceField(
        queryset=None, required=False, label="Fee structure"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["programme"].queryset = Programme.all_objects.filter(
            institution=self.institution
        ).order_by("name")
        self.fields["school_class"].queryset = SchoolClass.all_objects.filter(
            institution=self.institution
        ).order_by("name")
        self.fields["stream"].queryset = Stream.all_objects.filter(
            institution=self.institution
        ).order_by("name")
        self.fields["fee_structure"].queryset = FeeStructure.all_objects.filter(
            institution=self.institution, is_active=True
        ).order_by("name")

    def clean_email(self):
        email = normalize_email(self.cleaned_data.get("email") or "")
        if not email:
            return ""
        try:
            validate_email(email)
        except DjangoValidationError:
            raise forms.ValidationError("Enter a valid email address.") from None
        return email


class RegistrationApprovalForm(InstitutionFormMixin, forms.Form):
    """The fields an administrator chooses when approving a staff request."""

    position = forms.ChoiceField(label="Position")
    department = forms.ModelChoiceField(
        queryset=Department.objects.none(), required=False, label="Department (optional)"
    )
    username = UsernameField()

    def __init__(self, *args, institution=None, registrant=None, **kwargs):
        super().__init__(*args, institution=institution, **kwargs)
        from .registration import STAFF_POSITIONS, allowed_staff_positions

        codes = allowed_staff_positions(institution)
        self.fields["position"].choices = [
            (code, STAFF_POSITIONS[code].label) for code in codes
        ]
        self.fields["department"].queryset = Department.all_objects.filter(
            institution=institution
        ).order_by("name")
        self.fields["username"].widget.attrs["data-username-check-url"] = reverse(
            "username_availability"
        )
        if registrant is not None and registrant.pk:
            self.fields["username"].exclude_user = registrant
            self.fields["username"].widget.attrs["data-username-exclude"] = str(
                registrant.pk
            )


class EditUserForm(InstitutionFormMixin, forms.ModelForm):
    username = UsernameField(required=True, help_text=USERNAME_FORMAT_HINT)
    role = forms.ModelChoiceField(queryset=None, required=False)
    departments = forms.ModelMultipleChoiceField(
        queryset=None, required=False, label="Departments"
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = assignable_roles(self.actor)
        self.fields["role"].initial = self.instance.primary_role_id
        self.fields["departments"].queryset = Department.all_objects.filter(
            institution=self.institution
        ).order_by("name")
        profile = getattr(self.instance, "staff_profile", None)
        if profile is not None:
            self.fields["departments"].initial = profile.departments.values_list(
                "pk", flat=True
            )
        if self.instance and self.instance.pk:
            self.fields["username"].initial = self.instance.username
        from .user_admin import is_institution_admin

        if not is_institution_admin(self.actor):
            self.fields["username"].disabled = True
            self.fields["username"].required = False
            self.fields["username"].help_text = "Only an institution administrator can change usernames."

    def clean_email(self):
        email = normalize_email(self.cleaned_data.get("email") or "")
        if not email:
            return ""
        staff_or_admin = getattr(self.instance, "role", "") not in (User.Role.STUDENT, User.Role.PARENT)
        try:
            return validate_user_email(
                email,
                institution=self.institution,
                exclude_user=self.instance,
                staff_or_admin=staff_or_admin,
            )
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc

    def clean_role(self):
        role = self.cleaned_data.get("role")
        is_admin = (
            self.instance.role == User.Role.ADMIN
            or getattr(self.instance.primary_role, "code", "") == "institution_admin"
        )
        if (
            role is not None
            and self.instance.pk == getattr(self.actor, "pk", None)
            and is_admin
            and role.code != "institution_admin"
        ):
            raise forms.ValidationError(
                "You cannot remove your own administrator role."
            )
        return role

    def clean_username(self):
        from .user_admin import is_institution_admin

        if not is_institution_admin(self.actor):
            return self.instance.username
        return self.cleaned_data.get("username", "")


class LinkParentForm(InstitutionFormMixin, forms.Form):
    parent = forms.ModelChoiceField(queryset=None, label="Parent account")
    student = forms.ModelChoiceField(queryset=None, label="Student")
    relationship = forms.CharField(
        max_length=40, required=False, help_text="For example mother, father or guardian."
    )
    can_view_finance = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = User.tenant_objects.filter(
            institution=self.institution, role__in=(User.Role.PARENT, User.Role.STAFF)
        ).order_by("first_name", "last_name")
        from academics.models import Student

        self.fields["student"].queryset = Student.all_objects.filter(
            institution=self.institution
        ).select_related("user").order_by("student_no")


class DuplicateForm(InstitutionFormMixin, forms.Form):
    primary = forms.ModelChoiceField(queryset=None, label="Keep this account")
    duplicate = forms.ModelChoiceField(queryset=None, label="Duplicate account")
    merge = forms.BooleanField(
        required=False,
        label="Merge links where safe (otherwise only flag the duplicate)",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = User.tenant_objects.filter(
            institution=self.institution, is_active=True
        ).order_by("first_name", "last_name")
        self.fields["primary"].queryset = queryset
        self.fields["duplicate"].queryset = queryset

    def clean(self):
        cleaned = super().clean()
        primary = cleaned.get("primary")
        duplicate = cleaned.get("duplicate")
        if primary and duplicate and primary.pk == duplicate.pk:
            raise forms.ValidationError("Choose two different accounts.")
        return cleaned


class StrongPasswordValidationMixin:
    """EduSphere's strength rules, applied in Django 5.1's clean() hook.

    Password validation moved from ``clean_new_password2()`` into
    ``SetPasswordForm.clean()``. Overriding the old hook raises an
    AttributeError on every POST, so extra rules are added in clean() instead.
    """

    password_field = "new_password2"

    def password_context(self, cleaned):
        user = getattr(self, "user", None)
        return getattr(user, "username", ""), getattr(user, "email", "")

    def clean(self):
        cleaned = super().clean()
        username, email = self.password_context(cleaned)
        add_password_errors(
            self,
            self.password_field,
            (cleaned or {}).get(self.password_field),
            username=username,
            email=email,
        )
        return cleaned


class StrongPasswordChangeForm(StrongPasswordValidationMixin, PasswordChangeForm):
    """The forced first-login password change."""


class StrongSetPasswordForm(StrongPasswordValidationMixin, SetPasswordForm):
    """The new-password step of a password reset."""


class StrongUserCreationForm(StrongPasswordValidationMixin, UserCreationForm):
    """Django admin's add-user form."""

    password_field = "password2"

    class Meta(UserCreationForm.Meta):
        model = User

    def password_context(self, cleaned):
        return (cleaned or {}).get("username", ""), ""


class StrongAdminPasswordChangeForm(StrongPasswordValidationMixin, AdminPasswordChangeForm):
    """Django admin's change-password form."""

    password_field = "password2"


class InvitationPasswordForm(StrongPasswordValidationMixin, forms.Form):
    """Choose a password from a one-time invitation link."""

    password_field = "password"
    password = forms.CharField(
        label="New password",
        widget=forms.PasswordInput,
        strip=False,
        help_text="At least 10 characters with capital and small letters and a number.",
    )
    password_confirm = forms.CharField(
        label="Repeat the password", widget=forms.PasswordInput, strip=False
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if (
            (cleaned or {}).get("password")
            and cleaned.get("password_confirm")
            and cleaned["password"] != cleaned["password_confirm"]
        ):
            self.add_error("password_confirm", "The two passwords do not match.")
        return cleaned
