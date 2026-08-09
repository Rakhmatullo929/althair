"""
Escalation create endpoint for the external Voice platform (human handoff).

    POST /api/v1/intake/escalate/

When the voice agent decides a human is needed, it POSTs the caller's name (if
given), transcript, summary and reason here.  We create an ESCALATION
Interaction that shows up in the same staff Escalation tab as SMS escalations.

Read-side (list / unread-count / acknowledge) is already handled by the
existing escalation queue — no changes there.

Auth: StaticBearerAuthentication (same static bearer token as the inbound
webhooks / context-lookup).
"""

import logging

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from intake.models import ChannelChoice
from intake.serializers.webhooks import VoiceEscalationSerializer
from intake.services.escalation import create_escalation
from users.utils.authentication import HasStaticToken, StaticBearerAuthentication
from channels.models import ChannelType
from channels.services import ChannelResolutionError, resolve_active_connection

logger = logging.getLogger(__name__)


class EscalationCreateView(APIView):
    """POST a voice escalation (human handoff) into the staff Escalation queue."""

    authentication_classes = [StaticBearerAuthentication]
    permission_classes = [HasStaticToken]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'intake_webhook'

    def post(self, request):
        serializer = VoiceEscalationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            resolved = resolve_active_connection(
                provider='twilio',
                channel_type=ChannelType.VOICE,
                destination=data['destination'],
            )
        except ChannelResolutionError:
            return Response(
                {'detail': 'Unknown or inactive destination.', 'code': 'unknown_destination'},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            '[escalation] incoming voice escalation has_phone=%s reason=%r has_transcript=%s',
            bool(data['phone']), (data['reason'] or 'unspecified'), bool(data['transcript']),
        )

        interaction = create_escalation(
            organization=resolved.organization,
            channel_connection=resolved.connection,
            channel=ChannelChoice.VOICE,
            phone=data['phone'],
            name=data['name'],
            reason=data['reason'],
            transcript=data['transcript'],
            summary=data['summary'],
            notes=data['notes'],
            call_sid=data['call_sid'],
            related_job_id=data['related_job_id'],
        )

        return Response(
            {'status': 'escalated', 'interaction_id': str(interaction.id)},
            status=status.HTTP_201_CREATED,
        )
