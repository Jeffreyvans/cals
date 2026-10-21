from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        REGISTRAR = "registrar", "Registrar"
        BURSAR = "bursar", "Bursar / Accounts"
        LECTURER = "lecturer", "Lecturer / Teacher"
        STUDENT = "student", "Student / Pupil"
        PARENT = "parent", "Parent / Guardian"
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT)
    phone = models.CharField(max_length=30, blank=True)

    @property
    def is_office(self):
        return self.role in ("admin", "registrar", "bursar")
