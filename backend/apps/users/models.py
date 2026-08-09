from __future__ import annotations
import os
from uuid import uuid4

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import RegexValidator, EmailValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

# --------- helpers
def _upload_path(kind: str, instance: "User", filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return f"users/{kind}/{instance.id}/{uuid4().hex}{ext}"

def avatar_upload_to(instance: "User", filename: str) -> str:
    return _upload_path("avatar", instance, filename)

def cover_upload_to(instance: "User", filename: str) -> str:
    return _upload_path("cover", instance, filename)

phone_validator = RegexValidator(
    regex=r"^\+?[0-9]{7,20}$",
    message=_("Enter a valid international phone number."),
)


class UserRole(models.TextChoices):
    ADMIN = "admin", _("Administrator")
    OPERATIONS = "operations", _("Operations User")


# --------- Manager
class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, username, email=None, password=None, **extra_fields):
        if not username:
            raise ValueError("The given username must be set")
        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(username, email, password, **extra_fields)


# --------- Custom User
class User(AbstractUser):
    """
    Кастомная модель User (без location и address).
    """
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    email = models.EmailField(_("email address"), blank=True, validators=[EmailValidator()])
    phone = models.CharField(_("phone"), max_length=24, validators=[phone_validator], blank=True)
    # DEPRECATED: migration input only. Never use for tenant resolution or authorization.
    organization = models.CharField(_("organization"), max_length=255, blank=True)

    # Dashboard access role.  Administrator = full access (incl. user management);
    # Operations User = day-to-day job operations.  (Fine-grained criteria TBD.)
    # DEPRECATED: OrganizationMembership.role is the customer authorization source.
    role = models.CharField(
        _("role"), max_length=20,
        choices=UserRole.choices, default=UserRole.OPERATIONS, db_index=True,
    )

    # Set to True when an admin creates a user with a temp password so the user
    # is forced to set their own password on first login.
    must_change_password = models.BooleanField(_("must change password"), default=False)

    avatar = models.ImageField(
        _("avatar"),
        upload_to=avatar_upload_to,
        blank=True,
        null=True,
    )
    cover = models.ImageField(
        _("cover image"),
        upload_to=cover_upload_to,
        blank=True,
        null=True,
        help_text=_("Background image for profile header."),
    )

    # timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    class Meta:
        db_table = "auth_user"
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def is_admin(self) -> bool:
        """Deprecated legacy compatibility helper; not valid for customer authorization."""
        return bool(self.is_superuser or self.role == UserRole.ADMIN)

    @property
    def completion_percent(self) -> int:
        """
        Прогресс заполнения профиля — без location/address.
        """
        fields = ["first_name", "last_name", "email", "phone", "organization", "avatar", "cover"]
        total = len(fields)
        filled = sum(1 for f in fields if getattr(self, f) not in [None, "", []])
        return int(round((filled / total) * 100)) if total else 0
