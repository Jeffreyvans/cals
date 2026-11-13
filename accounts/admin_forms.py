"""Forms for the administrator Users area."""

from django import forms
from django.contrib.auth.forms import PasswordChangeForm

from academics.models import Programme, SchoolClass, Stream
from core.models import Department
from finance.models import FeeStructure

from .models import User
from .user_admin import assignable_roles
from .validators import validate_strong_password

ACCESS_CHOICES = (
    ("invitation", "Send invitation email (single-use link, 72 hours)"),
    ("temporary_password", "Create with a temporary password (show once)"),
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

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            return ""
        queryset = User.objects.filter(
            institution=self.institution, email__iexact=email, is_active=True
        )
        instance = getattr(self, "instance", None)
        if instance is not None and instance.pk:
            queryset = queryset.exclude(pk=instance.pk)
        if queryset.exists():
            raise forms.ValidationError(
                "An account at this institution already uses that email address."
            )
        return email


class StaffAccountForm(InstitutionFormMixin, forms.Form):
    full_name = forms.CharField(max_length=150, label="Full name")
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
    access_mode = forms.ChoiceField(choices=ACCESS_CHOICES, initial="invitation")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = assignable_roles(self.actor)
        self.fields["departments"].queryset = Department.all_objects.filter(
            institution=self.institution
        ).order_by("name")


class StudentAccountForm(InstitutionFormMixin, forms.Form):
    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, label="Last name")
    email = forms.EmailField(required=False, help_text="Leave empty to use a temporary password.")
    phone = forms.CharField(max_length=30, required=False)
    username = forms.CharField(
        required=False, help_text="Leave empty and one is made from the name."
    )
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
    access_mode = forms.ChoiceField(choices=ACCESS_CHOICES, initial="invitation")
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


class EditUserForm(InstitutionFormMixin, forms.ModelForm):
    role = forms.ModelChoiceField(queryset=None, required=False)
    departments = forms.ModelMultipleChoiceField(
        queryset=None, required=False, label="Departments"
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone"]

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


class StrongPasswordChangeForm(PasswordChangeForm):
    def clean_new_password2(self):
        password = super().clean_new_password2()
        user = self.user
        validate_strong_password(password, username=user.username, email=user.email)
        return password
