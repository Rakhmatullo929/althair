from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.middleware import csrf
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from ai_runtime.models import AIRun, AIUsageEvent, OrganizationAIRuntimeConfig
from channels.models import ChannelConnection
from control_plane.authentication import (
    PlatformSessionAuthentication,
    clear_session_cookie,
    create_platform_session,
    request_ip,
    set_session_cookie,
)
from control_plane.mfa import generate_setup, provisioning_uri, verify_mfa
from control_plane.models import (
    ControlKind,
    OperationalControl,
    OperationalJob,
    OrganizationEntitlement,
    PlanCatalog,
    PlatformAccessStatus,
    PlatformAuditEvent,
    PlatformDataRequest,
    PlatformIncident,
    PlatformMFADevice,
    PlatformRole,
    PlatformSession,
    PlatformStaffAccess,
)
from control_plane.permissions import (
    HasPlatformAccess,
    HasPlatformPermission,
    HasRecentPlatformMFA,
    mfa_is_fresh,
    role_allows,
)
from control_plane.serializers import (
    ControlMutationSerializer,
    DataRequestCreateSerializer,
    DataRequestSerializer,
    EntitlementSerializer,
    EntitlementWriteSerializer,
    IncidentSerializer,
    IncidentWriteSerializer,
    LoginSerializer,
    MFAVerifySerializer,
    OperationalControlSerializer,
    OperationalJobSerializer,
    ReasonSerializer,
    StaffAccessSerializer,
    StaffCreateSerializer,
    StaffUpdateSerializer,
)
from control_plane.services import (
    ControlPlaneConflict,
    ControlPlaneDenied,
    activate_control,
    approve_data_request,
    create_data_request,
    export_manifest,
    ensure_default_entitlement,
    organization_detail,
    overview_data,
    provider_health_data,
    record_audit,
    reject_data_request,
    require_reason,
    restore_control,
    retry_job,
    run_approved_data_request,
    set_organization_lifecycle,
    transition_job,
    update_entitlement,
    update_staff_access,
    verify_data_request_identity,
)
from organizations.models import Organization
from users.utils.authentication import enforce_csrf


User = get_user_model()


class InternalPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def paginated(request, queryset, serializer, view):
    paginator = InternalPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response(serializer(page, many=True).data)


def conflict_response(exc):
    code = "internal_operation_denied" if isinstance(exc, ControlPlaneDenied) else "internal_operation_conflict"
    http_status = status.HTTP_403_FORBIDDEN if isinstance(exc, ControlPlaneDenied) else status.HTTP_409_CONFLICT
    return Response({"detail": str(exc), "code": code}, status=http_status)


def internal_enabled():
    return bool(settings.CONTROL_PLANE_ENABLE or settings.TESTING)


class InternalCSRFView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        if not internal_enabled():
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response({"csrftoken": csrf.get_token(request)})


class InternalLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "internal_login"

    def post(self, request):
        if not internal_enabled():
            return Response(status=status.HTTP_404_NOT_FOUND)
        enforce_csrf(request)
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        password = serializer.validated_data["password"]
        user = User.objects.filter(email__iexact=email).first()
        authenticated = authenticate(request, username=user.username, password=password) if user else None
        access = PlatformStaffAccess.objects.filter(user=authenticated).first() if authenticated else None
        if not authenticated or not authenticated.is_active or not access or access.status != PlatformAccessStatus.ACTIVE:
            PlatformAuditEvent.objects.create(
                action="auth.login_failed", target_type="platform_session", reason="Generic internal login failure",
                before_summary={}, after_summary={}, request_id=str(getattr(request, "request_id", "")),
                network_hash="", result="denied",
            )
            return Response({"detail": "Invalid internal credentials."}, status=status.HTTP_401_UNAUTHORIZED)
        allowed_ips = settings.CONTROL_PLANE_ALLOWED_IPS
        if allowed_ips and request_ip(request) not in allowed_ips:
            return Response({"detail": "Internal access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        session, raw = create_platform_session(request, access)
        access.last_login_at = timezone.now()
        access.save(update_fields=["last_login_at", "updated_at"])
        has_device = PlatformMFADevice.objects.filter(access=access, enabled=True).exists()
        response = Response({
            "mfa_required": access.mfa_required or settings.CONTROL_PLANE_MFA_REQUIRED,
            "mfa_setup_required": not has_device,
            "role": access.role,
        })
        set_session_cookie(response, raw)
        PlatformAuditEvent.objects.create(
            actor=access, platform_role=access.role, action="auth.login_success",
            target_type="platform_session", target_id=str(session.id),
            reason="Internal login completed pending MFA", after_summary={"role": access.role},
            request_id=str(getattr(request, "request_id", "")), result="success",
        )
        return response


class InternalBaseView(APIView):
    authentication_classes = [PlatformSessionAuthentication]
    permission_classes = [HasPlatformAccess, HasPlatformPermission]
    platform_permission = "overview.read"
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "internal_api"


class InternalWriteView(InternalBaseView):
    permission_classes = [HasPlatformAccess, HasPlatformPermission, HasRecentPlatformMFA]


class MFASetupView(InternalBaseView):
    platform_permission = "settings.read"

    def post(self, request):
        existing = PlatformMFADevice.objects.filter(access=request.platform_access, enabled=True).first()
        if existing:
            return Response({"detail": "MFA is already configured.", "code": "mfa_already_configured"}, status=409)
        device, secret, recovery_codes = generate_setup(request.platform_access)
        return Response({
            "device_id": str(device.id), "secret": secret,
            "provisioning_uri": provisioning_uri(request.platform_access, secret),
            "recovery_codes": recovery_codes,
            "shown_once": True,
        }, status=status.HTTP_201_CREATED)


class MFAVerifyView(InternalBaseView):
    platform_permission = "settings.read"
    throttle_scope = "internal_mfa"

    def post(self, request):
        serializer = MFAVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device = PlatformMFADevice.objects.filter(access=request.platform_access).first()
        if not device or not verify_mfa(device, serializer.validated_data["code"]):
            return Response({"detail": "MFA verification failed."}, status=status.HTTP_401_UNAUTHORIZED)
        now = timezone.now()
        if not device.enabled:
            device.enabled = True
            device.confirmed_at = now
            device.save(update_fields=["enabled", "confirmed_at", "updated_at"])
        request.platform_session.mfa_verified_at = now
        request.platform_session.save(update_fields=["mfa_verified_at"])
        record_audit(request, action="auth.mfa_verified", target_type="platform_session",
                     target_id=request.platform_session.id, reason="Internal MFA verification completed",
                     after={"verified": True})
        return Response({"verified": True, "verified_at": now})


class InternalLogoutView(InternalBaseView):
    platform_permission = "settings.read"

    def post(self, request):
        request.platform_session.revoked_at = timezone.now()
        request.platform_session.save(update_fields=["revoked_at"])
        response = Response({"detail": "logged out"})
        clear_session_cookie(response)
        return response


class InternalMeView(InternalBaseView):
    platform_permission = "settings.read"

    def get(self, request):
        access = request.platform_access
        return Response({
            "id": str(access.id), "user_id": str(access.user_id), "email": access.user.email,
            "display_name": access.user.get_full_name() or access.user.username, "role": access.role,
            "status": access.status, "mfa_required": access.mfa_required,
            "mfa_verified": bool(request.platform_session.mfa_verified_at),
            "mfa_fresh": mfa_is_fresh(request), "session_expires_at": request.platform_session.expires_at,
            "environment": "development" if settings.DEBUG else "production",
        })


class SessionListView(InternalBaseView):
    platform_permission = "settings.read"

    def get(self, request):
        sessions = request.platform_access.sessions.order_by("-created_at")[:100]
        return Response([{
            "id": str(item.id), "current": item.id == request.platform_session.id,
            "mfa_verified_at": item.mfa_verified_at, "last_seen_at": item.last_seen_at,
            "expires_at": item.expires_at, "revoked_at": item.revoked_at,
        } for item in sessions])

    def post(self, request):
        enforce_csrf(request)
        reason = require_reason(request.data.get("reason"))
        session_id = request.data.get("session_id")
        target = get_object_or_404(PlatformSession, pk=session_id, access=request.platform_access)
        target.revoked_at = timezone.now()
        target.save(update_fields=["revoked_at"])
        record_audit(request, action="auth.session_revoke", target_type="platform_session", target_id=target.id,
                     reason=reason, after={"revoked": True})
        return Response({"revoked": True})


class OverviewView(InternalBaseView):
    platform_permission = "overview.read"

    def get(self, request):
        return Response(overview_data())


class OrganizationListView(InternalBaseView):
    platform_permission = "organization.read"

    def get(self, request):
        rows = Organization.objects.annotate(
            member_count=Count("memberships", filter=Q(memberships__status="active"), distinct=True),
            channel_count=Count("channels_channelconnections", distinct=True),
            internal_ai_enabled=Exists(
                OrganizationAIRuntimeConfig.objects.filter(organization_id=OuterRef("pk"), enabled=True)
            ),
        ).select_related("entitlement__plan").order_by("name")
        if value := request.query_params.get("q"):
            rows = rows.filter(Q(name__icontains=value) | Q(slug__icontains=value))
        for field in ("status", "industry"):
            if value := request.query_params.get(field):
                rows = rows.filter(**{field: value})
        if value := request.query_params.get("channel_type"):
            rows = rows.filter(channels_channelconnections__type=value).distinct()
        if value := request.query_params.get("ai_enabled"):
            rows = rows.filter(ai_runtime_config__enabled=value.lower() in {"1", "true", "yes"})
        if value := request.query_params.get("plan"):
            rows = rows.filter(entitlement__plan__key=value)
        paginator = InternalPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        data = [{
            "id": str(item.id), "name": item.name, "slug": item.slug, "status": item.status,
            "industry": item.industry, "created_at": item.created_at,
            "member_count": item.member_count, "channel_count": item.channel_count,
            "ai_enabled": item.internal_ai_enabled,
            "plan": getattr(getattr(getattr(item, "entitlement", None), "plan", None), "key", "unassigned"),
            "plan_id": str(getattr(getattr(item, "entitlement", None), "plan_id", "")),
        } for item in page]
        return paginator.get_paginated_response(data)


class OrganizationDetailView(InternalBaseView):
    platform_permission = "organization.read"

    def get(self, request, organization_id):
        reason = require_reason(request.headers.get("X-Internal-Reason"))
        organization = get_object_or_404(Organization, pk=organization_id)
        redacted = request.platform_access.role == PlatformRole.SUPPORT
        payload = organization_detail(organization, support_redaction=redacted)
        record_audit(request, action="organization.inspect", target_type="organization", target_id=organization.id,
                     organization=organization, reason=reason,
                     after={"redacted": redacted, "sections": list(payload.keys())})
        return Response(payload)


class OrganizationActionView(InternalWriteView):
    platform_permission = "organization.lifecycle"

    def post(self, request, organization_id, action):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = get_object_or_404(Organization, pk=organization_id)
        try:
            organization, state = set_organization_lifecycle(
                request, organization, action=action, reason=serializer.validated_data["reason"]
            )
        except (ControlPlaneConflict, ControlPlaneDenied) as exc:
            return conflict_response(exc)
        return Response({"organization": str(organization.id), "status": organization.status,
                         "new_logins_disabled": state.new_logins_disabled,
                         "provider_sends_disabled": state.provider_sends_disabled, "ai_disabled": state.ai_disabled})


class ControlsView(InternalBaseView):
    platform_permission = "overview.read"

    def get(self, request):
        rows = OperationalControl.objects.select_related("activated_by", "organization", "channel_connection")
        if request.query_params.get("active") in {"1", "true"}:
            rows = rows.filter(active=True)
        return paginated(request, rows, OperationalControlSerializer, self)

    def patch(self, request):
        if not role_allows(request.platform_access.role, "control.manage"):
            return Response({"detail": "Your internal role cannot manage controls."}, status=403)
        if not mfa_is_fresh(request):
            return Response({"detail": "Recent MFA verification is required."}, status=403)
        serializer = ControlMutationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            if data["action"] == "restore":
                control = get_object_or_404(OperationalControl, pk=data.get("control_id"))
                control = restore_control(request, control, reason=data["reason"])
            else:
                organization = get_object_or_404(Organization, pk=data["organization_id"]) if data.get("organization_id") else None
                connection = get_object_or_404(ChannelConnection, pk=data["channel_connection_id"]) if data.get("channel_connection_id") else None
                control, _ = activate_control(
                    request, kind=data["kind"], reason=data["reason"], organization=organization,
                    provider_type=data.get("provider_type", ""), channel_connection=connection,
                    expires_at=data.get("expires_at"),
                )
        except (ControlPlaneConflict, ControlPlaneDenied) as exc:
            return conflict_response(exc)
        return Response(OperationalControlSerializer(control).data)


class ProviderListView(InternalBaseView):
    platform_permission = "provider.read"

    def get(self, request, provider_type=""):
        return Response(provider_health_data(provider_type=provider_type))


class ProviderActionView(InternalWriteView):
    platform_permission = "provider.manage"

    def post(self, request, connection_id):
        connection = get_object_or_404(ChannelConnection.objects.select_related("organization"), pk=connection_id)
        action = request.data.get("action")
        reason = require_reason(request.data.get("reason"))
        if action == "refresh_health":
            connection.last_synced_at = timezone.now()
            connection.save(update_fields=["last_synced_at", "updated_at"])
        elif action == "pause":
            activate_control(request, kind=ControlKind.CHANNEL_CONNECTION, reason=reason,
                             organization=connection.organization, channel_connection=connection)
        elif action == "resume":
            active = OperationalControl.objects.filter(
                kind=ControlKind.CHANNEL_CONNECTION, channel_connection=connection, active=True
            ).first()
            if active:
                restore_control(request, active, reason=reason)
        elif action == "reset_circuit_breaker":
            connection.last_error_code = ""
            connection.last_error_message = ""
            connection.save(update_fields=["last_error_code", "last_error_message", "updated_at"])
            record_audit(request, action="provider.reset_circuit_breaker", target_type="channel_connection",
                         target_id=connection.id, organization=connection.organization, reason=reason,
                         after={"last_error_code": ""})
        else:
            return Response({"detail": "Unsupported safe provider action."}, status=400)
        return Response(provider_health_data(provider_type=connection.type))


class AIUsageView(InternalBaseView):
    platform_permission = "ai.read"

    def get(self, request):
        rows = AIUsageEvent.objects.values("organization_id", "provider", "model").annotate(
            runs=Count("id"), input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens")
        ).order_by("organization_id")[:500]
        return Response({"usage": list(rows), "runs": dict(AIRun.objects.values_list("status").annotate(total=Count("id")))})


class JobListView(InternalBaseView):
    platform_permission = "job.read"

    def get(self, request):
        rows = OperationalJob.objects.select_related("organization", "channel_connection")
        for field in ("status", "job_type"):
            if value := request.query_params.get(field):
                rows = rows.filter(**{field: value})
        if value := request.query_params.get("organization_id"):
            rows = rows.filter(organization_id=value)
        return paginated(request, rows, OperationalJobSerializer, self)


class JobActionView(InternalWriteView):
    platform_permission = "job.manage"

    def post(self, request, job_id, action):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = get_object_or_404(OperationalJob, pk=job_id)
        try:
            job = retry_job(request, job, reason=serializer.validated_data["reason"]) if action == "retry" else transition_job(
                request, job, action=action, reason=serializer.validated_data["reason"]
            )
        except (ControlPlaneConflict, ControlPlaneDenied) as exc:
            return conflict_response(exc)
        return Response(OperationalJobSerializer(job).data)


class IncidentListView(InternalBaseView):
    platform_permission = "incident.read"

    def get(self, request):
        return paginated(request, PlatformIncident.objects.prefetch_related("affected_organizations", "linked_jobs"), IncidentSerializer, self)

    @transaction.atomic
    def post(self, request):
        if request.platform_access.role not in {
            PlatformRole.OWNER, PlatformRole.ADMIN, PlatformRole.OPERATIONS, PlatformRole.SUPPORT
        }:
            return Response({"detail": "Your internal role cannot create incidents."}, status=403)
        serializer = IncidentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        incident = PlatformIncident.objects.create(
            severity=data["severity"], title=data["title"], safe_summary=data["safe_summary"],
            affected_provider=data.get("affected_provider", ""), created_by=request.platform_access,
            assigned_staff_id=data.get("assigned_staff_id"),
        )
        incident.affected_organizations.set(Organization.objects.filter(pk__in=data.get("organization_ids", [])))
        incident.linked_jobs.set(OperationalJob.objects.filter(pk__in=data.get("job_ids", [])))
        record_audit(request, action="incident.create", target_type="platform_incident", target_id=incident.id,
                     reason=data["reason"], after=IncidentSerializer(incident).data)
        return Response(IncidentSerializer(incident).data, status=201)


class IncidentDetailView(InternalWriteView):
    platform_permission = "incident.manage"

    @transaction.atomic
    def patch(self, request, incident_id):
        incident = get_object_or_404(PlatformIncident, pk=incident_id)
        serializer = IncidentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        requested_status = data.get("status", incident.status)
        if requested_status == PlatformIncident.Status.RESOLVED and incident.severity == PlatformIncident.Severity.CRITICAL and (
            request.platform_access.role not in {PlatformRole.OWNER, PlatformRole.ADMIN}
        ):
            return Response({"detail": "Critical resolution requires a platform administrator."}, status=403)
        before = IncidentSerializer(incident).data
        for field in ("severity", "status", "title", "safe_summary", "affected_provider"):
            if field in data:
                setattr(incident, field, data[field])
        incident.resolved_at = timezone.now() if incident.status == PlatformIncident.Status.RESOLVED else None
        incident.save()
        if "organization_ids" in data:
            incident.affected_organizations.set(Organization.objects.filter(pk__in=data["organization_ids"]))
        if "job_ids" in data:
            incident.linked_jobs.set(OperationalJob.objects.filter(pk__in=data["job_ids"]))
        record_audit(request, action="incident.update", target_type="platform_incident", target_id=incident.id,
                     reason=data["reason"], before=before, after=IncidentSerializer(incident).data)
        return Response(IncidentSerializer(incident).data)


class DataRequestListView(InternalBaseView):
    platform_permission = "data_request.read"

    def get(self, request):
        return paginated(request, PlatformDataRequest.objects.prefetch_related("approved_by"), DataRequestSerializer, self)

    def post(self, request):
        if not role_allows(request.platform_access.role, "data_request.approve") or not mfa_is_fresh(request):
            return Response({"detail": "Recent privileged approval is required."}, status=403)
        serializer = DataRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = get_object_or_404(Organization, pk=data["organization_id"])
        try:
            row, created = create_data_request(request, organization=organization, request_type=data["request_type"],
                                               reason=data["reason"], scope=data["scope"],
                                               idempotency_key=data["idempotency_key"])
        except (ControlPlaneConflict, ControlPlaneDenied) as exc:
            return conflict_response(exc)
        return Response(DataRequestSerializer(row).data, status=201 if created else 200)


class DataRequestActionView(InternalWriteView):
    platform_permission = "data_request.approve"

    def post(self, request, request_id, action):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = get_object_or_404(PlatformDataRequest, pk=request_id)
        download_url = None
        try:
            if action == "approve":
                row = approve_data_request(request, row, reason=serializer.validated_data["reason"])
            elif action == "reject":
                row = reject_data_request(request, row, reason=serializer.validated_data["reason"])
            elif action == "verify-identity":
                row = verify_data_request_identity(request, row, reason=serializer.validated_data["reason"])
            elif action == "run":
                row, raw_token = run_approved_data_request(request, row, reason=serializer.validated_data["reason"])
                if raw_token:
                    download_url = f"/api/v1/internal/data-requests/{row.id}/download/?token={raw_token}"
            else:
                return Response({"detail": "Unsupported data-request action."}, status=400)
        except (ControlPlaneConflict, ControlPlaneDenied) as exc:
            return conflict_response(exc)
        payload = DataRequestSerializer(row).data
        if download_url:
            payload["download_url"] = download_url
        return Response(payload)


class DataRequestDownloadView(InternalBaseView):
    platform_permission = "data_request.read"

    def get(self, request, request_id):
        row = get_object_or_404(PlatformDataRequest, pk=request_id)
        try:
            payload = export_manifest(row, request.query_params.get("token", ""))
        except ControlPlaneDenied as exc:
            return conflict_response(exc)
        record_audit(request, action="data_request.export_download", target_type="platform_data_request",
                     target_id=row.id, organization=row.organization,
                     reason="Authorized staff downloaded an approved export manifest",
                     after={"content_included": False, "secrets_included": False})
        return Response(payload)


class EntitlementView(InternalBaseView):
    platform_permission = "entitlement.read"

    def get(self, request, organization_id):
        organization = get_object_or_404(Organization, pk=organization_id)
        return Response(EntitlementSerializer(ensure_default_entitlement(organization)).data)

    def patch(self, request, organization_id):
        if not role_allows(request.platform_access.role, "entitlement.manage") or not mfa_is_fresh(request):
            return Response({"detail": "Recent privileged access is required."}, status=403)
        serializer = EntitlementWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = get_object_or_404(Organization, pk=organization_id)
        data = dict(serializer.validated_data)
        reason = data.pop("reason")
        try:
            entitlement = update_entitlement(request, organization, data, reason=reason)
        except (ControlPlaneConflict, ControlPlaneDenied) as exc:
            return conflict_response(exc)
        return Response(EntitlementSerializer(entitlement).data)


class AuditListView(InternalBaseView):
    platform_permission = "audit.read"

    def get(self, request):
        rows = PlatformAuditEvent.objects.select_related("actor", "organization")
        for field in ("action", "result", "organization_id", "request_id"):
            if value := request.query_params.get(field):
                rows = rows.filter(**{field: value})
        paginator = InternalPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response([{
            "id": str(item.id), "actor_role": item.platform_role, "action": item.action,
            "target_type": item.target_type, "target_id": item.target_id,
            "organization": str(item.organization_id) if item.organization_id else None,
            "reason": item.reason, "before": item.before_summary, "after": item.after_summary,
            "request_id": item.request_id, "mfa_fresh": item.mfa_fresh,
            "result": item.result, "created_at": item.created_at,
        } for item in page])


class StaffListView(InternalBaseView):
    platform_permission = "staff.read"

    def get(self, request):
        return paginated(request, PlatformStaffAccess.objects.select_related("user"), StaffAccessSerializer, self)

    @transaction.atomic
    def post(self, request):
        if request.platform_access.role != PlatformRole.OWNER or not mfa_is_fresh(request):
            return Response({"detail": "A recently verified platform owner is required."}, status=403)
        serializer = StaffCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = User.objects.filter(email__iexact=data["email"]).first()
        if not user:
            return Response({"detail": "The user must exist before internal access is provisioned."}, status=409)
        access, created = PlatformStaffAccess.objects.get_or_create(
            user=user, defaults={"role": data["role"], "status": PlatformAccessStatus.INVITED,
                                 "created_by": request.platform_access}
        )
        if not created:
            return Response({"detail": "Internal access already exists."}, status=409)
        record_audit(request, action="staff.create", target_type="platform_staff_access", target_id=access.id,
                     reason=data["reason"], after={"role": access.role, "status": access.status})
        return Response(StaffAccessSerializer(access).data, status=201)


class StaffDetailView(InternalWriteView):
    platform_permission = "staff.manage"

    def patch(self, request, access_id):
        if request.platform_access.role != PlatformRole.OWNER:
            return Response({"detail": "A platform owner is required."}, status=403)
        serializer = StaffUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        reason = data.pop("reason")
        target = get_object_or_404(PlatformStaffAccess, pk=access_id)
        try:
            target = update_staff_access(request, target, data, reason=reason)
        except Exception as exc:
            return Response({"detail": str(exc), "code": "staff_update_conflict"}, status=409)
        return Response(StaffAccessSerializer(target).data)


class SettingsView(InternalBaseView):
    platform_permission = "settings.read"

    def get(self, request):
        return Response({
            "control_plane_enabled": settings.CONTROL_PLANE_ENABLE,
            "mfa_required": settings.CONTROL_PLANE_MFA_REQUIRED,
            "fake_mfa": settings.CONTROL_PLANE_FAKE_MFA and (settings.DEBUG or settings.TESTING),
            "session_minutes": settings.CONTROL_PLANE_SESSION_MINUTES,
            "recent_mfa_minutes": settings.CONTROL_PLANE_RECENT_MFA_MINUTES,
            "export_storage_configured": bool(settings.CONTROL_PLANE_EXPORT_STORAGE),
            "ip_allow_list_configured": bool(settings.CONTROL_PLANE_ALLOWED_IPS),
            "break_glass_enabled": False,
            "impersonation_enabled": False,
        })
