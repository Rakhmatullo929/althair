from rest_framework.permissions import SAFE_METHODS

from organizations.models import OrganizationMembershipRole, OrganizationStatus


ROLE_ACTIONS = {
    OrganizationMembershipRole.OWNER: frozenset({"read", "operate", "manage_crm", "manage_team", "manage_channels", "manage_settings", "manage_ownership"}),
    OrganizationMembershipRole.ADMIN: frozenset({"read", "operate", "manage_crm", "manage_team", "manage_channels", "manage_settings"}),
    OrganizationMembershipRole.MANAGER: frozenset({"read", "operate", "manage_crm", "manage_settings"}),
    OrganizationMembershipRole.AGENT: frozenset({"read", "operate"}),
    OrganizationMembershipRole.VIEWER: frozenset({"read"}),
}


def role_allows(role: str, action: str) -> bool:
    return action in ROLE_ACTIONS.get(role, frozenset())


def action_for_method(method: str, *, write_action: str = "manage_crm") -> str:
    return "read" if method in SAFE_METHODS else write_action


def organization_allows_method(status: str, method: str) -> bool:
    if status in {OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE}:
        return True
    return method in SAFE_METHODS
