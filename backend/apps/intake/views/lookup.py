"""
Context-lookup endpoint for the external Voice platform.

    GET /api/v1/intake/context-lookup/?phone=|site=|community=|address=

Returns a caller's already-known location context (site / address / city /
state / community / builder / lot) so the platform can confirm details instead
of re-asking them.  Read-only — uses existing Contact + JobRecord data only.

Auth: StaticBearerAuthentication + HasStaticToken (same static bearer token as
the inbound webhooks), so only the trusted Voice platform can query it.
"""

import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

from intake.services.recall import lookup_context
from users.utils.authentication import HasStaticToken, StaticBearerAuthentication
from channels.models import ChannelType
from channels.services import ChannelResolutionError, resolve_active_connection

logger = logging.getLogger(__name__)


class ContextLookupView(APIView):
    """Voice-platform context lookup by phone / site / community / address."""

    authentication_classes = [StaticBearerAuthentication]
    permission_classes = [HasStaticToken]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'intake_webhook'

    def get(self, request):
        destination = (request.query_params.get('destination') or '').strip()
        try:
            resolved = resolve_active_connection(
                provider='twilio',
                channel_type=ChannelType.VOICE,
                destination=destination,
            )
        except ChannelResolutionError:
            return Response(
                {'detail': 'Unknown or inactive destination.', 'code': 'unknown_destination'},
                status=status.HTTP_404_NOT_FOUND,
            )
        phone = (request.query_params.get('phone') or '').strip()
        site = (request.query_params.get('site') or '').strip()
        community = (request.query_params.get('community') or '').strip()
        address = (request.query_params.get('address') or '').strip()

        # Precedence: phone > site > community > address.
        if phone:
            lookup_type = 'phone'
        elif site:
            lookup_type = 'site'
        elif community:
            lookup_type = 'community'
        elif address:
            lookup_type = 'address'
        else:
            lookup_type = 'none'

        logger.info('[context-lookup] incoming request lookup_type=%s', lookup_type)

        if lookup_type == 'none':
            return Response(
                {
                    'found': False,
                    'match_type': 'none',
                    'detail': 'Provide one of: phone, site, community, address.',
                    'contact': None,
                    'most_recent': None,
                    'known_locations': [],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = lookup_context(
            organization=resolved.organization,
            phone=phone,
            site=site,
            community=community,
            address=address,
        )
        logger.info(
            '[context-lookup] lookup_type=%s found=%s records=%d',
            result.get('match_type'),
            result.get('found'),
            len(result.get('known_locations') or []),
        )
        return Response(result)
