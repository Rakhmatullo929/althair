from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from channels.models import ChannelConnection, ChannelStatus, ChannelType
from control_plane.authentication import create_platform_session
from control_plane.mfa import hash_recovery_code, totp_for, verify_mfa
from control_plane.models import (
    ControlKind,
    DataRequestStatus,
    DataRequestType,
    OperationalControl,
    OperationalJob,
    OrganizationEntitlement,
    PlatformAccessStatus,
    PlatformAuditEvent,
    PlatformDataRequest,
    PlatformIncident,
    PlatformMFADevice,
    PlatformRole,
    PlatformSession,
    PlatformStaffAccess,
)
from control_plane.policies import blocking_control, feature_allowed, operation_allowed
from control_plane.services import (
    ControlPlaneConflict,
    activate_control,
    approve_data_request,
    create_data_request,
    ensure_default_entitlement,
    export_manifest,
    organization_detail,
    public_operational_restrictions,
    record_audit,
    retry_job,
    run_approved_data_request,
    safe_summary,
    set_organization_lifecycle,
    transition_job,
    update_entitlement,
    verify_data_request_identity,
)
from organizations.models import (
    Organization,
    OrganizationMembership,
    OrganizationMembershipRole,
    OrganizationMembershipStatus,
    OrganizationStatus,
)
from users.models import User


TEST_ENCRYPTION_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


@override_settings(
    CONTROL_PLANE_ENABLE=True,
    CONTROL_PLANE_FAKE_MFA=True,
    CONTROL_PLANE_MFA_REQUIRED=True,
    CONTROL_PLANE_COOKIE_NAME="test-internal-session",
    CONTROL_PLANE_ALLOWED_IPS=[],
    FIELD_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY,
)
class ControlPlaneTestCase(TestCase):
    login_value = "synthetic-platform-password"

    def setUp(self):
        self.organization = Organization.objects.create(
            name="Synthetic Tenant", slug="synthetic-tenant", status=OrganizationStatus.ACTIVE
        )
        self.customer = User.objects.create_user(
            username="customer@example.test", email="customer@example.test", password=self.login_value
        )
        self.membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.customer,
            role=OrganizationMembershipRole.OWNER,
            status=OrganizationMembershipStatus.ACTIVE,
            joined_at=timezone.now(),
        )
        self.channel = ChannelConnection.objects.create(
            organization=self.organization,
            type=ChannelType.TELEGRAM,
            provider="fake_telegram",
            display_name="Synthetic Telegram",
            external_identifier="synthetic-telegram",
            status=ChannelStatus.ACTIVE,
        )
        self.owner_user, self.owner = self.create_staff("owner", PlatformRole.OWNER)
        self.second_owner_user, self.second_owner = self.create_staff("owner-two", PlatformRole.OWNER)
        self.admin_user, self.admin = self.create_staff("admin", PlatformRole.ADMIN)
        self.operations_user, self.operations = self.create_staff("operations", PlatformRole.OPERATIONS)
        self.support_user, self.support = self.create_staff("support", PlatformRole.SUPPORT)
        self.auditor_user, self.auditor = self.create_staff("auditor", PlatformRole.SECURITY_AUDITOR)
        self.client = self.login_client(self.owner_user)

    def create_staff(self, label, role):
        user = User.objects.create_user(
            username=f"{label}@example.test", email=f"{label}@example.test", password=self.login_value
        )
        access = PlatformStaffAccess.objects.create(
            user=user, role=role, status=PlatformAccessStatus.ACTIVE
        )
        PlatformMFADevice.objects.create(
            access=access,
            secret_encrypted=base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("="),
            recovery_code_hashes=[],
            enabled=True,
            confirmed_at=timezone.now(),
        )
        return user, access

    def csrf(self, client):
        response = client.get("/api/v1/internal/auth/csrf/")
        self.assertEqual(response.status_code, 200)
        return response.data["csrftoken"]

    def login_client(self, user):
        client = APIClient(enforce_csrf_checks=True)
        token = self.csrf(client)
        response = client.post(
            "/api/v1/internal/auth/login/",
            {"email": user.email, "password": self.login_value},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200, response.data)
        response = client.post(
            "/api/v1/internal/auth/mfa/verify/",
            {"code": "000000"},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200, response.data)
        client._internal_csrf = token
        return client

    def post(self, client, path, data):
        return client.post(path, data, format="json", HTTP_X_CSRFTOKEN=client._internal_csrf)

    def patch(self, client, path, data):
        return client.patch(path, data, format="json", HTTP_X_CSRFTOKEN=client._internal_csrf)


class InternalAuthenticationTests(ControlPlaneTestCase):
    def test_internal_login_uses_separate_cookie_and_mfa(self):
        me = self.client.get("/api/v1/internal/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["role"], PlatformRole.OWNER)
        self.assertTrue(me.data["mfa_verified"])
        self.assertIn("test-internal-session", self.client.cookies)
        self.assertNotIn("jwt-auth", self.client.cookies)

    def test_invalid_login_is_generic_and_audited_without_email(self):
        client = APIClient(enforce_csrf_checks=True)
        token = self.csrf(client)
        response = client.post(
            "/api/v1/internal/auth/login/",
            {"email": "missing@example.test", "password": "incorrect-password"},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["detail"], "Invalid internal credentials.")
        event = PlatformAuditEvent.objects.get(action="auth.login_failed")
        self.assertNotIn("missing@example.test", str(event.before_summary) + str(event.after_summary))

    def test_customer_session_cannot_access_internal_api(self):
        client = APIClient(enforce_csrf_checks=True)
        token = client.get("/api/v1/users/auth/csrf/").data["csrftoken"]
        login = client.post(
            "/api/v1/users/auth/login/",
            {"email": self.customer.email, "password": self.login_value},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(client.get("/api/v1/internal/me/").status_code, 401)

    def test_django_superuser_has_no_internal_or_customer_tenant_bypass(self):
        superuser = User.objects.create_superuser(
            username="super@example.test", email="super@example.test", password=self.login_value
        )
        client = APIClient()
        client.force_authenticate(superuser)
        customer_response = client.get(
            f"/api/v1/organizations/{self.organization.id}/",
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )
        self.assertEqual(customer_response.status_code, 403)
        self.assertEqual(client.get("/api/v1/internal/me/").status_code, 403)

    def test_suspended_or_revoked_staff_fails_closed(self):
        client = self.login_client(self.operations_user)
        self.operations.status = PlatformAccessStatus.REVOKED
        self.operations.save()
        self.assertEqual(client.get("/api/v1/internal/me/").status_code, 401)
        self.assertTrue(PlatformSession.objects.filter(access=self.operations, revoked_at__isnull=False).exists())

    def test_csrf_is_required_for_internal_login_and_writes(self):
        client = APIClient(enforce_csrf_checks=True)
        response = client.post(
            "/api/v1/internal/auth/login/",
            {"email": self.owner_user.email, "password": self.login_value},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.patch(
            "/api/v1/internal/controls/",
            {"action": "activate", "kind": ControlKind.GLOBAL_AI, "reason": "Emergency test control"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_session_inactivity_and_expiry_are_enforced(self):
        session = PlatformSession.objects.filter(access=self.owner).latest("created_at")
        session.last_seen_at = timezone.now() - timedelta(minutes=30)
        session.save(update_fields=["last_seen_at"])
        self.assertEqual(self.client.get("/api/v1/internal/me/").status_code, 401)

    def test_logout_and_targeted_session_revocation(self):
        other = self.login_client(self.owner_user)
        other_session = PlatformSession.objects.filter(access=self.owner, revoked_at__isnull=True).latest("created_at")
        response = self.post(self.client, "/api/v1/internal/auth/sessions/", {
            "session_id": str(other_session.id), "reason": "Remove an old test device",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PlatformSession.objects.get(pk=other_session.id).revoked_at)
        response = self.post(self.client, "/api/v1/internal/auth/logout/", {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.cookies["test-internal-session"].value, "")

    def test_real_totp_replay_and_recovery_code_consumption(self):
        device = self.owner.mfa_device
        current = int(timezone.now().timestamp()) // 30
        code = totp_for(str(device.secret_encrypted), current)
        with override_settings(CONTROL_PLANE_FAKE_MFA=False):
            self.assertTrue(verify_mfa(device, code))
            self.assertFalse(verify_mfa(device, code))
            device.refresh_from_db()
            recovery_code = secrets.token_hex(10)
            device.recovery_code_hashes = [hash_recovery_code(recovery_code)]
            device.save(update_fields=["recovery_code_hashes"])
            self.assertTrue(verify_mfa(device, recovery_code))
            self.assertFalse(verify_mfa(device, recovery_code))

    def test_first_time_mfa_setup_shows_secret_once(self):
        user = User.objects.create_user(username="new@example.test", email="new@example.test", password=self.login_value)
        access = PlatformStaffAccess.objects.create(user=user, role=PlatformRole.SUPPORT, status=PlatformAccessStatus.ACTIVE)
        client = APIClient(enforce_csrf_checks=True)
        token = self.csrf(client)
        login = client.post("/api/v1/internal/auth/login/", {"email": user.email, "password": self.login_value}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertTrue(login.data["mfa_setup_required"])
        setup = client.post("/api/v1/internal/auth/mfa/setup/", {}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(setup.status_code, 201)
        self.assertIn("otpauth://totp/", setup.data["provisioning_uri"])
        self.assertEqual(len(setup.data["recovery_codes"]), 8)
        verify = client.post("/api/v1/internal/auth/mfa/verify/", {"code": "000000"}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(verify.status_code, 200)
        again = client.post("/api/v1/internal/auth/mfa/setup/", {}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(again.status_code, 409)


class PlatformRoleAndOrganizationTests(ControlPlaneTestCase):
    def test_support_sees_redacted_tenant_but_cannot_suspend(self):
        support = self.login_client(self.support_user)
        detail = support.get(
            f"/api/v1/internal/organizations/{self.organization.id}/",
            HTTP_X_INTERNAL_REASON="Investigate synthetic support request",
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn("***@", detail.data["members"][0]["email"])
        denied = self.post(support, f"/api/v1/internal/organizations/{self.organization.id}/suspend/", {
            "reason": "Synthetic support suspension attempt",
        })
        self.assertEqual(denied.status_code, 403)

    def test_security_auditor_and_operations_least_privilege(self):
        auditor = self.login_client(self.auditor_user)
        self.assertEqual(auditor.get("/api/v1/internal/audit/").status_code, 200)
        self.assertEqual(self.post(auditor, f"/api/v1/internal/jobs/{self._job().id}/retry/", {
            "reason": "Auditor should remain read only",
        }).status_code, 403)
        operations = self.login_client(self.operations_user)
        self.assertEqual(operations.get("/api/v1/internal/providers/").status_code, 200)
        self.assertEqual(operations.get("/api/v1/internal/platform-staff/").status_code, 403)

    def _job(self, **overrides):
        defaults = {"job_type": "provider.health", "organization": self.organization,
                    "status": OperationalJob.Status.DEAD_LETTER, "idempotent": True}
        defaults.update(overrides)
        return OperationalJob.objects.create(**defaults)

    def test_owner_and_admin_lifecycle_actions_are_reasoned_idempotent_and_audited(self):
        url = f"/api/v1/internal/organizations/{self.organization.id}/suspend/"
        first = self.post(self.client, url, {"reason": "Contain synthetic security incident"})
        second = self.post(self.client, url, {"reason": "Contain synthetic security incident"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.status, OrganizationStatus.SUSPENDED)
        self.assertTrue(public_operational_restrictions(self.organization)["restricted"])
        reactivate = self.post(self.client, f"/api/v1/internal/organizations/{self.organization.id}/reactivate/", {
            "reason": "Synthetic incident has been reviewed",
        })
        self.assertEqual(reactivate.status_code, 200)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.status, OrganizationStatus.ACTIVE)
        self.assertGreaterEqual(PlatformAuditEvent.objects.filter(organization=self.organization).count(), 3)

    def test_customer_login_is_blocked_without_a_platform_identity_leak(self):
        self.post(self.client, f"/api/v1/internal/organizations/{self.organization.id}/disable_logins/", {
            "reason": "Synthetic credential containment test",
        })
        customer = APIClient(enforce_csrf_checks=True)
        token = customer.get("/api/v1/users/auth/csrf/").data["csrftoken"]
        response = customer.post("/api/v1/users/auth/login/", {
            "email": self.customer.email, "password": self.login_value,
        }, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(self.owner_user.email, str(response.data))

    def test_organization_directory_filters_and_detail_reason(self):
        directory = self.client.get("/api/v1/internal/organizations/?q=Synthetic&status=active&channel_type=telegram")
        self.assertEqual(directory.status_code, 200)
        self.assertEqual(directory.data["count"], 1)
        missing_reason = self.client.get(f"/api/v1/internal/organizations/{self.organization.id}/")
        self.assertEqual(missing_reason.status_code, 409)
        detail = organization_detail(self.organization, support_redaction=False)
        self.assertEqual(detail["crm"]["messages"], 0)
        self.assertNotIn("encrypted_credentials", str(detail))

    def test_last_platform_owner_cannot_be_removed_or_demoted(self):
        self.second_owner.status = PlatformAccessStatus.REVOKED
        self.second_owner.save()
        self.owner.role = PlatformRole.ADMIN
        with self.assertRaises(ValidationError):
            self.owner.save()
        response = self.patch(self.client, f"/api/v1/internal/platform-staff/{self.owner.id}/", {
            "role": PlatformRole.ADMIN, "reason": "Attempt to remove the last owner",
        })
        self.assertEqual(response.status_code, 409)


class ControlsEntitlementsAndJobsTests(ControlPlaneTestCase):
    def test_global_tenant_provider_channel_voice_and_autopilot_controls(self):
        for kind, kwargs, expected in (
            (ControlKind.GLOBAL_AI, {"ai": True}, "global_ai_disabled"),
            (ControlKind.GLOBAL_AI_TOOL, {"organization": self.organization, "tool_name": "create_task"}, "global_ai_tool_disabled"),
            (ControlKind.GLOBAL_PROVIDER, {"provider_type": "telegram"}, "global_provider_disabled"),
            (ControlKind.ORGANIZATION_AI, {"organization": self.organization, "ai": True}, "organization_ai_disabled"),
            (ControlKind.ORGANIZATION_AI_TOOL, {"organization": self.organization, "tool_name": "create_task"}, "organization_ai_tool_disabled"),
            (ControlKind.ORGANIZATION_PROVIDER, {"organization": self.organization, "provider_type": "telegram"}, "organization_provider_disabled"),
            (ControlKind.CHANNEL_CONNECTION, {"organization": self.organization, "channel_connection": self.channel}, "channel_connection_disabled"),
            (ControlKind.VOICE_GLOBAL, {"voice": True}, "global_voice_disabled"),
            (ControlKind.EXTERNAL_AUTOPILOT, {"autopilot": True}, "external_autopilot_disabled"),
        ):
            create_kwargs = {"kind": kind, "reason": "Synthetic emergency control", "activated_by": self.owner}
            if kind in {ControlKind.ORGANIZATION_AI, ControlKind.ORGANIZATION_AI_TOOL, ControlKind.ORGANIZATION_PROVIDER, ControlKind.CHANNEL_CONNECTION}:
                create_kwargs["organization"] = self.organization
            if kind in {ControlKind.GLOBAL_PROVIDER, ControlKind.ORGANIZATION_PROVIDER, ControlKind.GLOBAL_AI_TOOL, ControlKind.ORGANIZATION_AI_TOOL}:
                create_kwargs["provider_type"] = "create_task" if "tool" in kind else "telegram"
            if kind == ControlKind.CHANNEL_CONNECTION:
                create_kwargs["channel_connection"] = self.channel
            control = OperationalControl.objects.create(**create_kwargs)
            self.assertEqual(blocking_control(**kwargs), expected)
            self.assertFalse(operation_allowed(**kwargs))
            control.active = False
            control.save(update_fields=["active"])

    def test_control_api_requires_recent_mfa_and_restore_is_explicit(self):
        response = self.patch(self.client, "/api/v1/internal/controls/", {
            "action": "activate", "kind": ControlKind.GLOBAL_AI, "reason": "Synthetic global AI incident",
        })
        self.assertEqual(response.status_code, 200)
        control_id = response.data["id"]
        restore = self.patch(self.client, "/api/v1/internal/controls/", {
            "action": "restore", "control_id": control_id, "reason": "Synthetic global AI recovery",
        })
        self.assertEqual(restore.status_code, 200)
        self.assertFalse(restore.data["active"])
        session = PlatformSession.objects.filter(access=self.owner).latest("created_at")
        session.mfa_verified_at = timezone.now() - timedelta(hours=1)
        session.save(update_fields=["mfa_verified_at"])
        denied = self.patch(self.client, "/api/v1/internal/controls/", {
            "action": "activate", "kind": ControlKind.GLOBAL_AI, "reason": "Stale MFA should not work",
        })
        self.assertEqual(denied.status_code, 403)

    def test_entitlement_unknown_features_fail_closed_and_overrides_are_audited(self):
        entitlement = ensure_default_entitlement(self.organization)
        self.assertTrue(feature_allowed(self.organization, "voice"))
        self.assertFalse(feature_allowed(self.organization, "unknown_feature"))
        updated = update_entitlement(
            self._request(self.owner), self.organization,
            {"feature_overrides": {"voice": False}, "limit_overrides": {"voice_minutes": 20}},
            reason="Synthetic entitlement limit review",
        )
        self.assertFalse(feature_allowed(self.organization, "voice"))
        self.assertEqual(updated.limit_overrides["voice_minutes"], 20)
        self.assertEqual(PlatformAuditEvent.objects.filter(action="entitlement.update").count(), 1)

    def _request(self, access):
        class Request:
            platform_access = access
            platform_session = PlatformSession.objects.filter(access=access).order_by("-created_at").first()
            request_id = "synthetic-request"
            META = {"REMOTE_ADDR": "127.0.0.1"}
        return Request()

    def test_idempotent_job_retry_non_idempotent_rejection_cancel_and_acknowledge(self):
        safe = OperationalJob.objects.create(
            job_type="provider.health", organization=self.organization,
            status=OperationalJob.Status.DEAD_LETTER, idempotent=True,
        )
        unsafe = OperationalJob.objects.create(
            job_type="provider.send", organization=self.organization,
            status=OperationalJob.Status.DEAD_LETTER, idempotent=False,
        )
        request = self._request(self.owner)
        retried = retry_job(request, safe, reason="Retry deterministic health refresh")
        self.assertEqual(retried.status, OperationalJob.Status.RETRYING)
        with self.assertRaises(ControlPlaneConflict):
            retry_job(request, unsafe, reason="Unsafe provider send retry attempt")
        queued = OperationalJob.objects.create(job_type="sync", status=OperationalJob.Status.QUEUED, idempotent=True)
        self.assertEqual(transition_job(request, queued, action="cancel", reason="Cancel obsolete synthetic job").status, OperationalJob.Status.CANCELLED)
        dead = OperationalJob.objects.create(job_type="webhook", status=OperationalJob.Status.DEAD_LETTER, idempotent=True)
        self.assertEqual(transition_job(request, dead, action="acknowledge", reason="Acknowledge reviewed dead letter").status, OperationalJob.Status.ACKNOWLEDGED)

    def test_provider_health_redacts_credentials_and_supports_safe_actions(self):
        self.channel.encrypted_credentials = "synthetic-secret-value"
        self.channel.last_error_message = "raw upstream error with sensitive context"
        self.channel.last_error_code = "provider_unavailable"
        self.channel.save()
        response = self.client.get("/api/v1/internal/providers/")
        self.assertEqual(response.status_code, 200)
        serialized = str(response.data)
        self.assertNotIn("synthetic-secret-value", serialized)
        self.assertNotIn("raw upstream error", serialized)
        self.assertIn("provider_unavailable", serialized)
        self.assertIn("infrastructure-database", serialized)


class ControlPlaneAPISurfaceTests(ControlPlaneTestCase):
    def test_safe_read_surfaces_and_filters(self):
        ensure_default_entitlement(self.organization)
        OperationalJob.objects.create(
            job_type="provider.health", organization=self.organization,
            status=OperationalJob.Status.DEAD_LETTER, idempotent=True,
        )
        for path in (
            "/api/v1/internal/overview/",
            "/api/v1/internal/organizations/?q=Synthetic&status=active&channel_type=telegram&plan=manual",
            "/api/v1/internal/controls/?active=true",
            "/api/v1/internal/providers/infrastructure/",
            "/api/v1/internal/ai/usage/",
            "/api/v1/internal/jobs/?status=dead_letter&job_type=provider.health",
            "/api/v1/internal/incidents/",
            "/api/v1/internal/data-requests/",
            f"/api/v1/internal/entitlements/{self.organization.id}/",
            "/api/v1/internal/audit/?result=success",
            "/api/v1/internal/platform-staff/",
            "/api/v1/internal/settings/",
            "/api/v1/internal/auth/sessions/",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, (path, response.data))
            self.assertNotIn("synthetic-platform-password", str(response.data))

    def test_provider_and_job_safe_actions(self):
        for action in ("refresh_health", "pause", "resume", "reset_circuit_breaker"):
            response = self.post(self.client, f"/api/v1/internal/provider-connections/{self.channel.id}/action/", {
                "action": action, "reason": f"Synthetic provider {action} review",
            })
            self.assertEqual(response.status_code, 200, (action, response.data))
        unsupported = self.post(self.client, f"/api/v1/internal/provider-connections/{self.channel.id}/action/", {
            "action": "arbitrary_http", "reason": "Reject an unsafe provider operation",
        })
        self.assertEqual(unsupported.status_code, 400)

        safe = OperationalJob.objects.create(
            job_type="provider.health", organization=self.organization,
            status=OperationalJob.Status.DEAD_LETTER, idempotent=True,
        )
        retried = self.post(self.client, f"/api/v1/internal/jobs/{safe.id}/retry/", {
            "reason": "Retry a deterministic health operation",
        })
        self.assertEqual(retried.status_code, 200)
        cancelled = self.post(self.client, f"/api/v1/internal/jobs/{safe.id}/cancel/", {
            "reason": "Cancel the reviewed retry operation",
        })
        self.assertEqual(cancelled.status_code, 200)

    def test_export_workflow_and_expiring_manifest_api(self):
        created = self.post(self.client, "/api/v1/internal/data-requests/", {
            "organization_id": str(self.organization.id), "request_type": "export",
            "reason": "Synthetic export requested by verified owner", "scope": {"profile": True},
            "idempotency_key": "synthetic-export-api-key",
        })
        self.assertEqual(created.status_code, 201, created.data)
        request_id = created.data["id"]
        for action, reason in (
            ("verify-identity", "Synthetic ownership proof was reviewed"),
            ("approve", "Approve the synthetic export request"),
            ("run", "Generate the safe synthetic export manifest"),
        ):
            response = self.post(self.client, f"/api/v1/internal/data-requests/{request_id}/{action}/", {"reason": reason})
            self.assertEqual(response.status_code, 200, (action, response.data))
        download = self.client.get(response.data["download_url"])
        self.assertEqual(download.status_code, 200)
        self.assertFalse(download.data["secrets_included"])
        denied = self.client.get(f"/api/v1/internal/data-requests/{request_id}/download/?token=wrong")
        self.assertEqual(denied.status_code, 403)

    def test_owner_provisions_and_updates_internal_staff(self):
        user = User.objects.create_user(
            username="new-staff@example.test", email="new-staff@example.test", password=self.login_value
        )
        created = self.post(self.client, "/api/v1/internal/platform-staff/", {
            "email": user.email, "role": PlatformRole.SUPPORT,
            "reason": "Provision synthetic support access",
        })
        self.assertEqual(created.status_code, 201, created.data)
        duplicate = self.post(self.client, "/api/v1/internal/platform-staff/", {
            "email": user.email, "role": PlatformRole.SUPPORT,
            "reason": "Reject duplicate synthetic access",
        })
        self.assertEqual(duplicate.status_code, 409)
        updated = self.patch(self.client, f"/api/v1/internal/platform-staff/{created.data['id']}/", {
            "status": PlatformAccessStatus.ACTIVE, "reason": "Activate reviewed synthetic support access",
        })
        self.assertEqual(updated.status_code, 200, updated.data)


class IncidentsDataRequestsAndAuditTests(ControlPlaneTestCase):
    def _request(self, access):
        class Request:
            platform_access = access
            platform_session = PlatformSession.objects.filter(access=access).order_by("-created_at").first()
            request_id = "synthetic-request"
            META = {"REMOTE_ADDR": "127.0.0.1"}
        return Request()

    def test_incident_create_update_and_critical_resolution_permissions(self):
        operations = self.login_client(self.operations_user)
        created = self.post(operations, "/api/v1/internal/incidents/", {
            "severity": "critical", "title": "Synthetic critical incident",
            "safe_summary": "Provider operations are degraded in a synthetic test.",
            "organization_ids": [str(self.organization.id)], "reason": "Create synthetic incident for review",
        })
        self.assertEqual(created.status_code, 201)
        incident_id = created.data["id"]
        denied = self.patch(operations, f"/api/v1/internal/incidents/{incident_id}/", {
            "severity": "critical", "status": "resolved", "title": "Synthetic critical incident",
            "safe_summary": "Provider operations recovered in a synthetic test.",
            "reason": "Resolve synthetic critical incident",
        })
        self.assertEqual(denied.status_code, 403)
        resolved = self.patch(self.client, f"/api/v1/internal/incidents/{incident_id}/", {
            "severity": "critical", "status": "resolved", "title": "Synthetic critical incident",
            "safe_summary": "Provider operations recovered in a synthetic test.",
            "reason": "Owner confirms synthetic recovery",
        })
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.data["status"], "resolved")

    def test_export_request_identity_approval_completion_and_expiring_manifest(self):
        request = self._request(self.owner)
        row, created = create_data_request(
            request, organization=self.organization, request_type=DataRequestType.EXPORT,
            reason="Synthetic verified export request", scope={"profile": True}, idempotency_key="export-test-key",
        )
        self.assertTrue(created)
        duplicate, created = create_data_request(
            request, organization=self.organization, request_type=DataRequestType.EXPORT,
            reason="Synthetic verified export request", scope={"profile": True}, idempotency_key="export-test-key",
        )
        self.assertFalse(created)
        self.assertEqual(duplicate.id, row.id)
        with self.assertRaises(ControlPlaneConflict):
            approve_data_request(request, row, reason="Approve before identity verification")
        row = verify_data_request_identity(request, row, reason="Identity verified using synthetic ownership proof")
        row = approve_data_request(request, row, reason="Approve synthetic export request")
        self.assertEqual(row.status, DataRequestStatus.APPROVED)
        row, token = run_approved_data_request(request, row, reason="Generate synthetic export manifest")
        self.assertEqual(row.status, DataRequestStatus.COMPLETED)
        manifest = export_manifest(row, token)
        self.assertFalse(manifest["content_included"])
        self.assertFalse(manifest["secrets_included"])
        with self.assertRaises(Exception):
            export_manifest(row, "wrong-token")

    def test_destructive_request_requires_owner_and_two_distinct_approvals(self):
        request = self._request(self.owner)
        row, _ = create_data_request(
            request, organization=self.organization, request_type=DataRequestType.DELETE,
            reason="Synthetic staged deletion review", scope={"tenant": True}, idempotency_key="delete-test-key",
        )
        verify_data_request_identity(request, row, reason="Synthetic ownership identity verified")
        row = approve_data_request(request, row, reason="First owner approves staged deletion")
        self.assertEqual(row.status, DataRequestStatus.IDENTITY_VERIFICATION)
        admin_request = self._request(self.admin)
        with self.assertRaises(Exception):
            approve_data_request(admin_request, row, reason="Administrator cannot approve deletion")
        second_request = self._request(self.second_owner)
        row = approve_data_request(second_request, row, reason="Second owner approves staged deletion")
        self.assertEqual(row.status, DataRequestStatus.APPROVED)
        row, token = run_approved_data_request(second_request, row, reason="Queue reviewed destructive workflow")
        self.assertEqual(row.status, DataRequestStatus.RUNNING)
        self.assertIsNone(token)
        self.assertTrue(Organization.objects.filter(pk=self.organization.pk).exists())

    def test_audit_is_immutable_and_redacts_forbidden_fields(self):
        request = self._request(self.owner)
        event = record_audit(
            request, action="security.synthetic", target_type="test", reason="Synthetic security audit event",
            before={"api_token": "never-store", "safe": "before"},
            after={"password": "never-store", "safe": "after", "nested": {"message_body": "hidden"}},
        )
        serialized = str(event.before_summary) + str(event.after_summary)
        self.assertNotIn("never-store", serialized)
        self.assertNotIn("message_body", serialized)
        event.reason = "Changed reason"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            PlatformAuditEvent.objects.filter(pk=event.pk).update(reason="changed")
        with self.assertRaises(ValidationError):
            event.delete()

    def test_safe_summary_and_overview_never_return_secret_values(self):
        value = safe_summary({
            "provider_secret": "hidden", "raw_payload": "hidden", "status": "ok",
            "nested": {"transcript": "hidden", "safe_code": "temporary"},
        })
        self.assertEqual(value, {"status": "ok", "nested": {"safe_code": "temporary"}})
        response = self.client.get("/api/v1/internal/overview/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("organizations", response.data)
        self.assertNotIn("secret", str(response.data).lower())
