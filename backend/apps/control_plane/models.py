from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from channels.models import ChannelConnection
from core.utils.encryption import EncryptedTextField
from organizations.models import Organization, validate_json_object


class PlatformRole(models.TextChoices):
    OWNER = "platform_owner", "Platform owner"
    ADMIN = "platform_admin", "Platform administrator"
    OPERATIONS = "operations", "Operations"
    SUPPORT = "support", "Support"
    SECURITY_AUDITOR = "security_auditor", "Security auditor"


class PlatformAccessStatus(models.TextChoices):
    INVITED = "invited", "Invited"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    REVOKED = "revoked", "Revoked"


class PlatformStaffAccess(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="platform_access"
    )
    role = models.CharField(max_length=24, choices=PlatformRole.choices, db_index=True)
    status = models.CharField(
        max_length=16, choices=PlatformAccessStatus.choices, default=PlatformAccessStatus.INVITED, db_index=True
    )
    mfa_required = models.BooleanField(default=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    last_privileged_action_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="created_accesses"
    )

    class Meta:
        ordering = ["user__email", "created_at"]
        indexes = [models.Index(fields=["status", "role"])]

    def clean(self):
        super().clean()
        if not self.pk:
            return
        previous = type(self).objects.filter(pk=self.pk).values("role", "status").first()
        removing_owner = previous and previous["role"] == PlatformRole.OWNER and (
            self.role != PlatformRole.OWNER or self.status != PlatformAccessStatus.ACTIVE
        )
        if removing_owner and not type(self).objects.filter(
            role=PlatformRole.OWNER, status=PlatformAccessStatus.ACTIVE
        ).exclude(pk=self.pk).exists():
            raise ValidationError("The last active platform owner cannot be removed or demoted.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PlatformMFADevice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    access = models.OneToOneField(PlatformStaffAccess, on_delete=models.CASCADE, related_name="mfa_device")
    label = models.CharField(max_length=120, default="Authenticator")
    secret_encrypted = EncryptedTextField(editable=False)
    recovery_code_hashes = models.JSONField(default=list, blank=True)
    last_time_step = models.BigIntegerField(default=-1)
    enabled = models.BooleanField(default=False, db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PlatformSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    access = models.ForeignKey(PlatformStaffAccess, on_delete=models.CASCADE, related_name="sessions")
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    user_agent_hash = models.CharField(max_length=64, blank=True, editable=False)
    ip_hash = models.CharField(max_length=64, blank=True, editable=False)
    mfa_verified_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["access", "revoked_at", "expires_at"])]


class ImmutableAuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Platform audit events are immutable.")

    def delete(self):
        raise ValidationError("Platform audit events are immutable.")


class PlatformAuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        PlatformStaffAccess, on_delete=models.PROTECT, null=True, blank=True, related_name="audit_events"
    )
    platform_role = models.CharField(max_length=24, choices=PlatformRole.choices, blank=True)
    action = models.CharField(max_length=120, db_index=True)
    target_type = models.CharField(max_length=80, db_index=True)
    target_id = models.CharField(max_length=80, blank=True, db_index=True)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, null=True, blank=True, related_name="platform_audit_events"
    )
    reason = models.CharField(max_length=1000)
    before_summary = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    after_summary = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    request_id = models.CharField(max_length=100, blank=True, db_index=True)
    network_hash = models.CharField(max_length=64, blank=True)
    mfa_fresh = models.BooleanField(default=False)
    result = models.CharField(max_length=40, default="success")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    objects = ImmutableAuditQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "-created_at"])]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Platform audit events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Platform audit events are immutable.")


class OrganizationOperationalState(models.Model):
    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, primary_key=True, related_name="operational_state"
    )
    marked_for_review = models.BooleanField(default=False)
    new_logins_disabled = models.BooleanField(default=False)
    provider_sends_disabled = models.BooleanField(default=False)
    ai_disabled = models.BooleanField(default=False)
    previous_status = models.CharField(max_length=20, blank=True)
    lifecycle_reason = models.CharField(max_length=1000, blank=True)
    updated_by = models.ForeignKey(
        PlatformStaffAccess, on_delete=models.PROTECT, null=True, blank=True, related_name="organization_states"
    )
    updated_at = models.DateTimeField(auto_now=True)


class ControlKind(models.TextChoices):
    GLOBAL_AI = "global_ai", "Global AI"
    GLOBAL_AI_TOOL = "global_ai_tool", "Global AI tool"
    GLOBAL_PROVIDER = "global_provider", "Global provider"
    ORGANIZATION_AI = "organization_ai", "Organization AI"
    ORGANIZATION_AI_TOOL = "organization_ai_tool", "Organization AI tool"
    ORGANIZATION_PROVIDER = "organization_provider", "Organization provider"
    CHANNEL_CONNECTION = "channel_connection", "Channel connection"
    VOICE_GLOBAL = "voice_global", "Global Voice calls"
    EXTERNAL_AUTOPILOT = "external_autopilot", "External autopilot"


class OperationalControl(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=32, choices=ControlKind.choices, db_index=True)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, null=True, blank=True, related_name="operational_controls"
    )
    provider_type = models.CharField(max_length=40, blank=True, db_index=True)
    channel_connection = models.ForeignKey(
        ChannelConnection, on_delete=models.CASCADE, null=True, blank=True, related_name="operational_controls"
    )
    active = models.BooleanField(default=True, db_index=True)
    reason = models.CharField(max_length=1000)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    activated_by = models.ForeignKey(
        PlatformStaffAccess, on_delete=models.PROTECT, related_name="controls_activated"
    )
    restored_by = models.ForeignKey(
        PlatformStaffAccess, on_delete=models.PROTECT, null=True, blank=True, related_name="controls_restored"
    )
    restored_reason = models.CharField(max_length=1000, blank=True)
    restored_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["active", "kind", "provider_type"])]

    def clean(self):
        super().clean()
        if self.channel_connection_id and self.organization_id and (
            self.channel_connection.organization_id != self.organization_id
        ):
            raise ValidationError("The connection belongs to another organization.")


class PlanCatalog(models.Model):
    key = models.SlugField(max_length=80, primary_key=True)
    display_name = models.CharField(max_length=160)
    active = models.BooleanField(default=True, db_index=True)
    feature_flags = models.JSONField(default=dict, validators=[validate_json_object])
    default_limits = models.JSONField(default=dict, validators=[validate_json_object])
    internal_notes = models.TextField(max_length=2000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class OrganizationEntitlement(models.Model):
    class Status(models.TextChoices):
        TRIAL = "trial", "Trial"
        ACTIVE = "active", "Active"
        GRACE = "grace", "Grace"
        SUSPENDED = "suspended", "Suspended"
        MANUAL = "manual", "Manual"

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, primary_key=True, related_name="entitlement"
    )
    plan = models.ForeignKey(PlanCatalog, on_delete=models.PROTECT, related_name="organization_entitlements")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.MANUAL, db_index=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    feature_overrides = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    limit_overrides = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    updated_by = models.ForeignKey(
        PlatformStaffAccess, on_delete=models.PROTECT, null=True, blank=True, related_name="entitlements_updated"
    )
    updated_at = models.DateTimeField(auto_now=True)


class OperationalJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RETRYING = "retrying", "Retrying"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        DEAD_LETTER = "dead_letter", "Dead letter"
        CANCELLED = "cancelled", "Cancelled"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_type = models.CharField(max_length=120, db_index=True)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, null=True, blank=True, related_name="operational_jobs"
    )
    channel_connection = models.ForeignKey(
        ChannelConnection, on_delete=models.SET_NULL, null=True, blank=True, related_name="operational_jobs"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    safe_error_code = models.CharField(max_length=80, blank=True)
    idempotency_reference = models.CharField(max_length=64, blank=True, db_index=True)
    idempotent = models.BooleanField(default=False, db_index=True)
    metadata = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status", "-created_at"])]


class PlatformIncident(models.Model):
    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        MITIGATED = "mitigated", "Mitigated"
        RESOLVED = "resolved", "Resolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    severity = models.CharField(max_length=12, choices=Severity.choices, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)
    title = models.CharField(max_length=240)
    safe_summary = models.TextField(max_length=4000)
    affected_provider = models.CharField(max_length=40, blank=True)
    affected_organizations = models.ManyToManyField(Organization, blank=True, related_name="platform_incidents")
    linked_jobs = models.ManyToManyField(OperationalJob, blank=True, related_name="incidents")
    assigned_staff = models.ForeignKey(
        PlatformStaffAccess, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_incidents"
    )
    created_by = models.ForeignKey(PlatformStaffAccess, on_delete=models.PROTECT, related_name="created_incidents")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class DataRequestType(models.TextChoices):
    EXPORT = "export", "Export"
    ANONYMIZE = "anonymize", "Anonymize"
    DELETE = "delete", "Delete"


class DataRequestStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    IDENTITY_VERIFICATION = "identity_verification", "Identity verification"
    APPROVED = "approved", "Approved"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class PlatformDataRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="data_requests")
    request_type = models.CharField(max_length=16, choices=DataRequestType.choices, db_index=True)
    requested_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="platform_data_requests"
    )
    requested_by_staff = models.ForeignKey(
        PlatformStaffAccess, on_delete=models.PROTECT, null=True, blank=True, related_name="data_requests_created"
    )
    status = models.CharField(
        max_length=28, choices=DataRequestStatus.choices, default=DataRequestStatus.REQUESTED, db_index=True
    )
    reason = models.CharField(max_length=1000)
    scope = models.JSONField(default=dict, validators=[validate_json_object])
    approval_required = models.PositiveSmallIntegerField(default=1)
    identity_verified_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ManyToManyField(PlatformStaffAccess, blank=True, related_name="data_requests_approved")
    idempotency_key_hash = models.CharField(max_length=64, unique=True, editable=False)
    export_reference = models.CharField(max_length=160, blank=True, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status", "-created_at"])]


class PlatformSupportNote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="platform_support_notes")
    author = models.ForeignKey(PlatformStaffAccess, on_delete=models.PROTECT, related_name="support_notes")
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
