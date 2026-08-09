import logging

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from intake.serializers.webhooks import TwilioVoiceWebhookSerializer
from intake.services.voice import process_voice_webhook
from users.utils.authentication import StaticBearerAuthentication
from channels.models import ChannelType
from channels.services import ChannelResolutionError, resolve_active_connection

logger = logging.getLogger(__name__)


def _verify_twilio_signature(request) -> bool:
    """
    Validate the X-Twilio-Signature header using the Twilio RequestValidator.
    If TWILIO_AUTH_TOKEN is not configured, validation is skipped with a warning
    (suitable for development/staging environments only).
    """
    auth_token = getattr(settings, 'TWILIO_AUTH_TOKEN', '')
    if not auth_token:
        return bool(settings.DEBUG or getattr(settings, 'TESTING', False))
    try:
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(auth_token)
        url = request.build_absolute_uri()
        post_params = dict(request.data) if request.data else {}
        post_params = {k: v[0] if isinstance(v, list) else v for k, v in post_params.items()}
        signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')
        return validator.validate(url, post_params, signature)
    except Exception as exc:
        logger.error('Twilio voice signature verification error: %s', type(exc).__name__)
        return False


class TwilioVoiceWebhookView(APIView):
    """
    Inbound Twilio Voice webhook endpoint.

    Handles three Twilio callback types through a single URL:
      1. Call status callbacks  (CallStatus present, no transcript)
      2. Recording callbacks    (RecordingUrl present)
      3. Transcription callbacks (TranscriptionText present)

    Twilio expects an HTTP 200 response on every call.  Processing errors are
    logged but do NOT return 5xx — this prevents Twilio from retrying and
    potentially creating duplicate records.
    """

    authentication_classes = [StaticBearerAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'intake_webhook'

    def post(self, request):
        if not _verify_twilio_signature(request):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        ser = TwilioVoiceWebhookSerializer(data=request.data)
        if not ser.is_valid():
            logger.warning('Twilio voice webhook: invalid payload — %s', ser.errors)
            return Response({'detail': 'Invalid payload'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            resolved = resolve_active_connection(
                provider='twilio',
                channel_type=ChannelType.VOICE,
                destination=ser.validated_data.get('To', ''),
            )
        except ChannelResolutionError:
            return Response(
                {'detail': 'Unknown or inactive destination.', 'code': 'unknown_destination'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            interaction = process_voice_webhook(
                ser.validated_data,
                organization=resolved.organization,
                channel_connection=resolved.connection,
            )
            return Response(
                {'status': 'accepted', 'interaction_id': str(interaction.id)},
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            # Always return 200 so Twilio does not retry and create duplicate records.
            logger.error(
                'Voice webhook processing error — %s: %s',
                type(exc).__name__, exc,
                exc_info=True,
            )
            return Response(
                {'status': 'received', 'detail': 'Processing error — check server logs'},
                status=status.HTTP_200_OK,
            )
