from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.permissions import BasePermission

from control_plane.models import PlatformAccessStatus, PlatformRole


ROLE_PERMISSIONS = {
    PlatformRole.OWNER: {"*"},
    PlatformRole.ADMIN: {
        "overview.read", "organization.read", "organization.lifecycle", "control.manage",
        "provider.read", "provider.manage", "ai.read", "ai.manage", "job.read", "job.manage",
        "incident.read", "incident.manage", "entitlement.read", "entitlement.manage",
        "data_request.read", "data_request.approve", "audit.read", "staff.read", "settings.read",
    },
    PlatformRole.OPERATIONS: {
        "overview.read", "organization.read", "provider.read", "provider.manage", "ai.read",
        "job.read", "job.manage", "incident.read", "incident.manage", "entitlement.read",
        "data_request.read", "audit.read", "settings.read",
    },
    PlatformRole.SUPPORT: {
        "overview.read", "organization.read", "provider.read", "ai.read", "job.read",
        "incident.read", "incident.create", "entitlement.read", "settings.read",
    },
    PlatformRole.SECURITY_AUDITOR: {
        "overview.read", "organization.read", "provider.read", "ai.read", "job.read",
        "incident.read", "data_request.read", "audit.read", "staff.read", "settings.read",
    },
}


def role_allows(role: str, permission: str) -> bool:
    allowed = ROLE_PERMISSIONS.get(role, set())
    return "*" in allowed or permission in allowed


def mfa_is_fresh(request) -> bool:
    verified = getattr(getattr(request, "platform_session", None), "mfa_verified_at", None)
    return bool(verified and verified >= timezone.now() - timedelta(minutes=settings.CONTROL_PLANE_RECENT_MFA_MINUTES))


class HasPlatformAccess(BasePermission):
    message = "Active internal platform access is required."

    def has_permission(self, request, view):
        access = getattr(request, "platform_access", None)
        return bool(access and access.status == PlatformAccessStatus.ACTIVE)


class HasPlatformPermission(BasePermission):
    message = "Your internal platform role does not permit this action."

    def has_permission(self, request, view):
        access = getattr(request, "platform_access", None)
        permission = getattr(view, "platform_permission", "")
        return bool(access and permission and role_allows(access.role, permission))


class HasRecentPlatformMFA(BasePermission):
    message = "Recent MFA verification is required."

    def has_permission(self, request, view):
        return mfa_is_fresh(request)
