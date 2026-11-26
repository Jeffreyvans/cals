"""Forms for the enterprise portal screens.

The group screens post JSON values directly, but sponsor accounts need ordinary
validated forms because they create users' access to another person's data.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from enterprise.models import SponsorAccount


class SponsorAccountForm(forms.ModelForm):
    class Meta:
        model = SponsorAccount
        fields = [
            "user", "sponsor", "organisation", "role", "sharing_level",
            "notify_email", "notify_in_app", "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = get_user_model().objects.filter(is_active=True)
        self.fields["sponsor"].queryset = SponsorAccount._meta.get_field(
            "sponsor"
        ).remote_field.model.all_objects.filter(is_active=True)
        self.fields["organisation"].queryset = SponsorAccount._meta.get_field(
            "organisation"
        ).remote_field.model.objects.all()
        self.fields["organisation"].required = False
        self.fields["sharing_level"].choices = [
            choice for choice in SponsorAccount.SharingLevel.choices
            if choice[0] != SponsorAccount.SharingLevel.WITHDRAWN
        ]


class SponsorLinkForm(forms.Form):
    """Attach an existing student to a sponsor with a bounded contribution."""

    student = forms.ModelChoiceField(queryset=None)
    percentage = forms.DecimalField(
        max_digits=5, decimal_places=2, required=False, min_value=0, max_value=100
    )
    fixed_amount = forms.DecimalField(
        max_digits=10, decimal_places=2, required=False, min_value=0
    )
    valid_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    valid_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    reference = forms.CharField(max_length=80, required=False)

    def __init__(self, *args, sponsor=None, **kwargs):
        super().__init__(*args, **kwargs)
        from academics.models import Student

        self.sponsor = sponsor
        self.fields["student"].queryset = Student.all_objects.filter(
            institution=sponsor.institution
        ).select_related("user") if sponsor else Student.all_objects.none()

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("percentage") and not cleaned.get("fixed_amount"):
            raise ValidationError("Enter a percentage or a fixed amount.")
        start, end = cleaned.get("valid_from"), cleaned.get("valid_to")
        if start and end and end < start:
            raise ValidationError("The end date must be on or after the start date.")
        today = timezone.localdate()
        if start and end and end < today:
            raise ValidationError("A new sponsorship must not have already ended.")
        return cleaned
