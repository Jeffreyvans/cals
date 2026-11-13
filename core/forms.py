"""Shared model-form plumbing used by the department registers."""

from django import forms
from django.contrib.auth import get_user_model


class TenantModelForm(forms.ModelForm):
    """Scope user pickers to the current institution and use HTML5 inputs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.tenancy import current_institution_id

        user_model = get_user_model()
        institution_id = current_institution_id()
        for field in self.fields.values():
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
            elif isinstance(field, forms.DateTimeField):
                field.widget = forms.DateTimeInput(
                    attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
                )
            elif isinstance(field, forms.TimeField):
                field.widget = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
            related = getattr(field, "queryset", None)
            if related is not None and field.__class__.__name__ == "ModelChoiceField":
                if related.model is user_model and institution_id is not None:
                    field.queryset = related.model.objects.filter(
                        institution_id=institution_id
                    )


def form_for(model, exclude=()):
    """Build a model form, leaving out fields the screens set themselves."""

    base_exclude = [
        "institution",
        "created",
        "updated",
        "created_by",
        "uploaded_by",
        "requested_by",
        "reported_by",
        "issued_by",
        "received_by",
        "opened_by",
        "author",
    ]
    exclude = list(dict.fromkeys([*base_exclude, *exclude]))
    meta = type("Meta", (), {"model": model, "fields": "__all__", "exclude": exclude})
    return type(
        f"{model.__name__}Form",
        (TenantModelForm,),
        {"Meta": meta, "__module__": __name__},
    )
