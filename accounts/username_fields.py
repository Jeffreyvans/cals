"""The shared optional username field used by every add-user form."""

from django import forms

from .usernames import USERNAME_FORMAT_HINT, USERNAME_HELP_TEXT, validate_username


class UsernameField(forms.CharField):
    """A lowercase, validated username with an external live-availability check.

    JavaScript is only an enhancement: ``clean()`` always performs the same
    validation on the server, so the form works with JS disabled.
    """

    def __init__(self, *, exclude_user=None, help_text=USERNAME_HELP_TEXT, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("label", "Username")
        kwargs.setdefault(
            "widget",
            forms.TextInput(
                attrs={
                    "autocomplete": "username",
                    "autocapitalize": "none",
                    "spellcheck": "false",
                    "minlength": "4",
                    "maxlength": "30",
                }
            ),
        )
        super().__init__(help_text=help_text, **kwargs)
        self.exclude_user = exclude_user

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return ""
        return validate_username(value, exclude_user=self.exclude_user)


__all__ = ["USERNAME_FORMAT_HINT", "USERNAME_HELP_TEXT", "UsernameField"]
