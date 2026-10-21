from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models


class TenantUserQuerySet(models.QuerySet):
    def for_institution(self, institution):
        if institution is None:
            return self.none()
        pk = institution if isinstance(institution, int) else institution.pk
        return self.filter(institution_id=pk)


class TenantUserManager(UserManager):
    """Only returns users of the current institution."""

    use_in_migrations = False

    def get_queryset(self):
        from core.tenancy import current_institution_id

        return TenantUserQuerySet(self.model, using=self._db).filter(
            institution_id=current_institution_id()
        )

    def for_institution(self, institution):
        return TenantUserQuerySet(self.model, using=self._db).for_institution(institution)


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
    institution = models.ForeignKey(
        "core.Institution", null=False, blank=True, on_delete=models.CASCADE, related_name="users"
    )
    primary_role = models.ForeignKey(
        "core.Role", null=True, blank=True, on_delete=models.SET_NULL, related_name="primary_users"
    )
    extra_roles = models.ManyToManyField("core.Role", blank=True, related_name="extra_users")

    # `objects` stays unfiltered so that sign-in can find a user before the
    # institution is known. `tenant_objects` is the one to use for listings.
    objects = UserManager()
    tenant_objects = TenantUserManager()

    @property
    def is_office(self):
        return self.role in ("admin", "registrar", "bursar")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.institution_id is None:
            from core.tenancy import current_institution_id, default_institution_id

            self.institution_id = current_institution_id() or default_institution_id()

    def save(self, *args, **kwargs):
        if self.institution_id is None:
            from core.tenancy import current_institution_id, default_institution_id

            self.institution_id = current_institution_id() or default_institution_id()
        super().save(*args, **kwargs)

    def permission_codes(self):
        """Every permission code this user holds."""
        from core.access import permission_codes_for

        return permission_codes_for(self)

    def has_code(self, code):
        """True when the user holds this permission code."""
        from core.access import user_has_code

        return user_has_code(self, code)
