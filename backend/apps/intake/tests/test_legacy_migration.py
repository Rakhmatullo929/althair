import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap

from django.test import SimpleTestCase


class PopulatedLegacyMigrationTests(SimpleTestCase):
    def run_child(self, code, env):
        result = subprocess.run(
            [sys.executable, '-c', code],
            cwd=Path(__file__).resolve().parents[3],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f'child migration check failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}',
        )

    def test_populated_database_backfills_without_data_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / 'legacy.sqlite3')
            env = os.environ.copy()
            env.update({
                'USE_SQLITE': '1',
                'SQLITE_PATH': database,
                'DEBUG': '1',
                'DJANGO_SETTINGS_MODULE': 'config.settings',
            })
            setup_code = textwrap.dedent(
                """
                import django
                django.setup()
                from django.db import connection
                from django.db.migrations.executor import MigrationExecutor

                targets = [('intake', '0020_jobrecord_is_recurring'), ('users', '0003_add_must_change_password')]
                executor = MigrationExecutor(connection)
                executor.migrate(targets)
                state = executor.loader.project_state(targets)
                User = state.apps.get_model('users', 'User')
                Contact = state.apps.get_model('intake', 'Contact')
                JobRecord = state.apps.get_model('intake', 'JobRecord')
                SystemPrompt = state.apps.get_model('intake', 'SystemPrompt')

                owner = User.objects.create(
                    username='legacy-owner', email='legacy@example.test', password='unusable',
                    role='admin', organization='Legacy MMC', is_superuser=True, is_staff=True,
                )
                contact = Contact.objects.create(name='Legacy Contact', phone='+15550009999')
                JobRecord.objects.create(
                    contact=contact, source_channel='manual', job_number='A-042',
                    service_type='Legacy Job',
                )
                SystemPrompt.objects.create(key='legacy-key', text='legacy prompt')
                """
            )
            self.run_child(setup_code, env)

            migrate = subprocess.run(
                [sys.executable, 'manage.py', 'migrate', '--noinput'],
                cwd=Path(__file__).resolve().parents[3],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                migrate.returncode,
                0,
                msg=f'forward migration failed\nstdout:\n{migrate.stdout}\nstderr:\n{migrate.stderr}',
            )

            assert_code = textwrap.dedent(
                """
                import django
                django.setup()
                from intake.models import Contact, JobRecord, SystemPrompt
                from organizations.models import Organization, OrganizationMembership

                legacy = Organization.objects.get(slug='legacy-mmc')
                assert Contact.objects.filter(organization=legacy, name='Legacy Contact').count() == 1
                assert JobRecord.objects.filter(
                    organization=legacy, job_number='A-042', service_type='Legacy Job',
                ).count() == 1
                assert SystemPrompt.objects.filter(organization=legacy, key='legacy-key').count() == 1
                assert OrganizationMembership.objects.filter(
                    organization=legacy, user__username='legacy-owner', role='owner', status='active',
                ).count() == 1
                """
            )
            self.run_child(assert_code, env)
