from dataclasses import dataclass

from channels.models import ChannelConnection, ChannelStatus


class ChannelResolutionError(LookupError):
    pass


@dataclass(frozen=True)
class ResolvedChannel:
    connection: ChannelConnection
    organization: object


def resolve_active_connection(*, provider: str, channel_type: str, destination: str) -> ResolvedChannel:
    normalized_destination = (destination or "").strip()
    if not provider or not channel_type or not normalized_destination:
        raise ChannelResolutionError("Trusted destination routing data is incomplete.")
    try:
        connection = ChannelConnection.objects.select_related("organization", "branch").get(
            provider=provider,
            type=channel_type,
            external_identifier=normalized_destination,
            status=ChannelStatus.ACTIVE,
        )
    except (ChannelConnection.DoesNotExist, ChannelConnection.MultipleObjectsReturned) as exc:
        raise ChannelResolutionError("No unique active channel connection matches the destination.") from exc
    return ResolvedChannel(connection=connection, organization=connection.organization)
