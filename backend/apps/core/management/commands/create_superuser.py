"""Idempotently create a superuser from environment-provided credentials."""

import logging
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand


User = get_user_model()
security_logger = logging.getLogger("security.audit")


class Command(BaseCommand):
    help = "Create a superuser from DJANGO_SUPERUSER_* environment variables."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")

        provided = (username, email, password)
        if not any(provided):
            self.stdout.write(
                self.style.WARNING(
                    "Superuser creation skipped: DJANGO_SUPERUSER_* variables are empty."
                )
            )
            return

        missing = [
            name
            for name, value in (
                ("DJANGO_SUPERUSER_USERNAME", username),
                ("DJANGO_SUPERUSER_EMAIL", email),
                ("DJANGO_SUPERUSER_PASSWORD", password),
            )
            if not value
        ]
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f'Superuser creation skipped: missing variables {", ".join(missing)}.'
                )
            )
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.SUCCESS(f'Superuser "{username}" already exists; skipping.'))
            return

        try:
            validate_password(password)
        except ValidationError as error:
            security_logger.warning(
                'Superuser creation failed password validation for "%s"', username
            )
            self.stderr.write(
                self.style.ERROR(f'Password validation failed: {"; ".join(error.messages)}')
            )
            raise SystemExit(1) from error

        User.objects.create_superuser(username=username, email=email, password=password)
        security_logger.info('Superuser "%s" created by management command', username)
        self.stdout.write(self.style.SUCCESS(f'Superuser "{username}" created successfully.'))
