import hmac
import logging

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from intake.serializers.webhooks import OutlookEmailWebhookSerializer
from intake.services.email import process_email_webhook
from channels.models import ChannelType
from channels.services import ChannelResolutionError, resolve_active_connection

logger = logging.getLogger(__name__)


def _verify_outlook_secret(request) -> bool:
    secret = getattr(settings, 'OUTLOOK_WEBHOOK_SECRET', '')
    if not secret:
        return bool(settings.DEBUG or getattr(settings, 'TESTING', False))
    provided = request.META.get('HTTP_X_WEBHOOK_SECRET', '')
    return hmac.compare_digest(provided, secret)


class OutlookEmailWebhookView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'intake_webhook'

    def post(self, request):
        if not _verify_outlook_secret(request):
            logger.warning('Outlook email webhook rejected: invalid secret')
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        ser = OutlookEmailWebhookSerializer(data=request.data)
        if not ser.is_valid():
            logger.warning('Outlook email webhook: invalid payload — %s', ser.errors)
            return Response({'detail': 'Invalid payload'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            resolved = resolve_active_connection(
                provider='outlook',
                channel_type=ChannelType.GMAIL,
                destination=ser.validated_data['to_email'],
            )
        except ChannelResolutionError:
            return Response(
                {'detail': 'Unknown or inactive destination.', 'code': 'unknown_destination'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            interaction = process_email_webhook(
                ser.validated_data,
                organization=resolved.organization,
                channel_connection=resolved.connection,
            )
            logger.info(
                'Email webhook accepted: interaction_id=%s',
                str(interaction.id)[:8],
            )
            return Response(
                {'status': 'accepted', 'interaction_id': str(interaction.id)},
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            # Always return 200 so Power Automate does not mark the flow as failed
            # and trigger unwanted retries.  The error is fully logged for diagnosis.
            logger.error(
                'Email webhook processing error — %s: %s. '
                'Contact may have been created but Interaction was NOT saved. '
                'Most common cause: FIELD_ENCRYPTION_KEY not set or wrong in production.',
                type(exc).__name__, exc,
                exc_info=True,
            )
            return Response(
                {'status': 'received', 'detail': 'Processing error — check server logs'},
                status=status.HTTP_200_OK,
            )
