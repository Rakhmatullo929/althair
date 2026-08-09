from __future__ import annotations

import uuid

from rest_framework.permissions import BasePermission

from organizations.exceptions import (
    InvalidOrganizationHeader,
    MissingOrganizationHeader,
    OrganizationAccessDenied,
    OrganizationHeaderMismatch,
    OrganizationReadOnly,
)
from organizations.models import (
    Organization,
    OrganizationMembership,
    OrganizationMembershipStatus,
)
from organizations.policies import action_for_method, organization_allows_method, role_allows


def resolve_request_organization(request, *, expected_organization_id=None):
    raw = request.headers.get("X-Organization-ID", "").strip()
    if not raw:
        raise MissingOrganizationHeader()
    try:
        organization_id = uuid.UUID(raw)
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidOrganizationHeader() from exc
    if expected_organization_id and organization_id != uuid.UUID(str(expected_organization_id)):
        raise OrganizationHeaderMismatch()
    try:
        organization = Organization.objects.get(pk=organization_id)
        membership = OrganizationMembership.objects.select_related("organization").get(
            organization=organization,
            user=request.user,
            status=OrganizationMembershipStatus.ACTIVE,
        )
    except (Organization.DoesNotExist, OrganizationMembership.DoesNotExist) as exc:
        raise OrganizationAccessDenied() from exc
    if not organization_allows_method(organization.status, request.method):
        raise OrganizationReadOnly()
    request.organization = organization
    request.organization_membership = membership
    return organization, membership


class OrganizationContextMixin:
    expected_organization_kwarg: str | None = None

    def initial(self, request, *args, **kwargs):
        expected = kwargs.get(self.expected_organization_kwarg) if self.expected_organization_kwarg else None
        resolve_request_organization(request, expected_organization_id=expected)
        return super().initial(request, *args, **kwargs)


class IsOrganizationMember(BasePermission):
    def has_permission(self, request, view):
        return bool(getattr(request, "organization_membership", None))


class HasOrganizationRole(BasePermission):
    message = "Your organization role does not permit this action."

    def has_permission(self, request, view):
        membership = getattr(request, "organization_membership", None)
        if not membership:
            return False
        action = getattr(view, "required_action", None) or action_for_method(
            request.method,
            write_action=getattr(view, "write_action", "manage_crm"),
        )
        return role_allows(membership.role, action)


class IsPlatformAdmin(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff and user.is_superuser)
