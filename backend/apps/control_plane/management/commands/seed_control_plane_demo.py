import base64
import os
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from control_plane.models import (
    OperationalJob,
    PlatformAccessStatus,
    PlatformIncident,
    PlatformMFADevice,
    PlatformRole,
    PlatformStaffAccess,
)
from control_plane.services import ensure_default_entitlement
from organizations.models import Organization


class Command(BaseCommand):
    help = "Seed deterministic synthetic internal-control-plane data for local development and E2E."

    def handle(self, *args, **options):
        password = os.environ.get("CONTROL_PLANE_SEED_PASSWORD", "")
        if not password or len(password) < 12:
            raise CommandError("CONTROL_PLANE_SEED_PASSWORD with at least 12 characters is required.")
        User = get_user_model()
        accesses = {}
        for email, role, first_name in (
            ("platform-owner@example.test", PlatformRole.OWNER, "Platform Owner"),
            ("platform-owner-two@example.test", PlatformRole.OWNER, "Second Owner"),
            ("platform-admin@example.test", PlatformRole.ADMIN, "Platform Admin"),
            ("platform-operations@example.test", PlatformRole.OPERATIONS, "Operations Staff"),
            ("platform-support@example.test", PlatformRole.SUPPORT, "Support Staff"),
            ("platform-auditor@example.test", PlatformRole.SECURITY_AUDITOR, "Security Auditor"),
        ):
            user, _ = User.objects.get_or_create(username=email, defaults={"email": email, "first_name": first_name})
            user.email = email
            user.set_password(password)
            user.is_active = True
            user.save()
            access, _ = PlatformStaffAccess.objects.get_or_create(
                user=user, defaults={"role": role, "status": PlatformAccessStatus.ACTIVE}
            )
            access.role = role
            access.status = PlatformAccessStatus.ACTIVE
            access.save()
            PlatformMFADevice.objects.update_or_create(
                access=access,
                defaults={
                    "secret_encrypted": base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("="),
                    "recovery_code_hashes": [],
                    "enabled": True,
                },
            )
            accesses[role] = access
        access = accesses[PlatformRole.OWNER]
        organization = Organization.objects.order_by("created_at").first()
        if organization:
            ensure_default_entitlement(organization)
            OperationalJob.objects.get_or_create(
                idempotency_reference="e2e-safe-health-refresh",
                defaults={
                    "job_type": "provider.health_refresh",
                    "organization": organization,
                    "status": OperationalJob.Status.DEAD_LETTER,
                    "idempotent": True,
                    "safe_error_code": "provider_temporarily_unavailable",
                },
            )
            OperationalJob.objects.get_or_create(
                idempotency_reference="e2e-non-idempotent-send",
                defaults={
                    "job_type": "provider.external_send",
                    "organization": organization,
                    "status": OperationalJob.Status.DEAD_LETTER,
                    "idempotent": False,
                    "safe_error_code": "delivery_unknown",
                },
            )
            incident, _ = PlatformIncident.objects.get_or_create(
                title="Synthetic provider health review",
                defaults={
                    "severity": PlatformIncident.Severity.LOW,
                    "safe_summary": "Synthetic local-only incident for deterministic UI coverage.",
                    "created_by": access,
                },
            )
            incident.affected_organizations.add(organization)
        self.stdout.write(self.style.SUCCESS("Seeded synthetic internal control-plane data."))
