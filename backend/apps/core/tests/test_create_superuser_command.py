import io
import os
import secrets
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase


class CreateSuperuserCommandTests(TestCase):
    env_names = (
        "DJANGO_SUPERUSER_USERNAME",
        "DJANGO_SUPERUSER_EMAIL",
        "DJANGO_SUPERUSER_PASSWORD",
    )

    def test_empty_environment_does_not_create_a_default_account(self):
        with mock.patch.dict(os.environ):
            for name in self.env_names:
                os.environ.pop(name, None)
            call_command("create_superuser", verbosity=0)

        self.assertFalse(get_user_model().objects.exists())

    def test_complete_environment_creates_superuser_without_logging_password(self):
        generated_password = secrets.token_urlsafe(32)
        values = {
            "DJANGO_SUPERUSER_USERNAME": "environment-admin",
            "DJANGO_SUPERUSER_EMAIL": "environment-admin@example.com",
            "DJANGO_SUPERUSER_PASSWORD": generated_password,
        }
        output = io.StringIO()
        with mock.patch.dict(os.environ, values):
            call_command("create_superuser", verbosity=0, stdout=output, stderr=output)

        user = get_user_model().objects.get(username="environment-admin")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password(generated_password))
        self.assertNotIn(generated_password, output.getvalue())
