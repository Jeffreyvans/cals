"""Password strength rules for public registration."""

from django.core.exceptions import ValidationError

COMMON_PASSWORDS = {
    "password",
    "password1",
    "password123",
    "12345678",
    "123456789",
    "1234567890",
    "qwerty123",
    "letmein",
    "welcome",
    "admin123",
    "student",
    "edusphere",
}


def validate_strong_password(password, username="", email=""):
    """Raise a plain-English error when a password is too easy to guess."""
    password = password or ""
    if len(password) < 10:
        raise ValidationError("Use at least 10 characters for your password.")
    if password.casefold() in COMMON_PASSWORDS:
        raise ValidationError("That password is too common. Choose another one.")
    if password.isdigit():
        raise ValidationError("Use letters as well as numbers in your password.")
    if password.isalpha():
        raise ValidationError("Add at least one number to your password.")
    if not any(char.isupper() for char in password):
        raise ValidationError("Add at least one capital letter to your password.")
    if not any(char.islower() for char in password):
        raise ValidationError("Add at least one small letter to your password.")
    username = (username or "").strip().casefold()
    if username and len(username) > 2 and username in password.casefold():
        raise ValidationError("Do not use your username inside your password.")
    email = (email or "").strip().casefold()
    local = email.split("@")[0] if "@" in email else ""
    if local and len(local) > 2 and local in password.casefold():
        raise ValidationError("Do not use your email address inside your password.")
    return password
