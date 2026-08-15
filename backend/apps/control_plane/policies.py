from django.db.models import Q
from django.utils import timezone

from control_plane.models import (
    ControlKind,
    OperationalControl,
    OrganizationEntitlement,
    OrganizationOperationalState,
)


class OperationallyBlocked(Exception):
    def __init__(self, code: str = "operational_control_active"):
        self.code = code
        super().__init__(code)


def _active_controls():
    now = timezone.now()
    return OperationalControl.objects.filter(active=True).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


def blocking_control(
    *, organization=None, provider_type="", channel_connection=None, ai=False,
    voice=False, autopilot=False, tool_name="",
):
    controls = _active_controls()
    if channel_connection and controls.filter(
        kind=ControlKind.CHANNEL_CONNECTION, channel_connection=channel_connection
    ).exists():
        return "channel_connection_disabled"
    if organization:
        try:
            state = organization.operational_state
            if state.provider_sends_disabled and provider_type:
                return "organization_provider_sends_disabled"
            if state.ai_disabled and ai:
                return "organization_ai_disabled"
        except OrganizationOperationalState.DoesNotExist:
            pass
        if ai and controls.filter(kind=ControlKind.ORGANIZATION_AI, organization=organization).exists():
            return "organization_ai_disabled"
        if tool_name and controls.filter(
            kind=ControlKind.ORGANIZATION_AI_TOOL,
            organization=organization,
            provider_type=tool_name,
        ).exists():
            return "organization_ai_tool_disabled"
        if provider_type and controls.filter(
            kind=ControlKind.ORGANIZATION_PROVIDER, organization=organization, provider_type=provider_type
        ).exists():
            return "organization_provider_disabled"
    if ai and controls.filter(kind=ControlKind.GLOBAL_AI).exists():
        return "global_ai_disabled"
    if tool_name and controls.filter(
        kind=ControlKind.GLOBAL_AI_TOOL, provider_type=tool_name
    ).exists():
        return "global_ai_tool_disabled"
    if voice and controls.filter(kind=ControlKind.VOICE_GLOBAL).exists():
        return "global_voice_disabled"
    if autopilot and controls.filter(kind=ControlKind.EXTERNAL_AUTOPILOT).exists():
        return "external_autopilot_disabled"
    if provider_type and controls.filter(kind=ControlKind.GLOBAL_PROVIDER, provider_type=provider_type).exists():
        return "global_provider_disabled"
    return ""


def operation_allowed(**kwargs) -> bool:
    if blocking_control(**kwargs):
        return False
    organization = kwargs.get("organization")
    provider_type = kwargs.get("provider_type", "")
    if organization and provider_type in {"web_chat", "instagram", "telegram", "gmail", "sms", "voice"}:
        from billing.services import feature_allowed as billing_feature_allowed

        if not billing_feature_allowed(organization, provider_type):
            return False
    if organization and kwargs.get("ai"):
        from billing.services import feature_allowed as billing_feature_allowed

        if not billing_feature_allowed(organization, "ai_runtime"):
            return False
    if organization and kwargs.get("autopilot"):
        from billing.services import feature_allowed as billing_feature_allowed

        if not billing_feature_allowed(organization, "ai_autopilot"):
            return False
    return True


def ensure_operation_allowed(**kwargs) -> None:
    code = blocking_control(**kwargs)
    if code:
        raise OperationallyBlocked(code)


def feature_allowed(organization, feature: str) -> bool:
    from billing.services import feature_allowed as billing_feature_allowed

    return billing_feature_allowed(organization, feature)
