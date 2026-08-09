import logging

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from intake.serializers.webhooks import TwilioSmsWebhookSerializer
from intake.services.sms_chat import handle_inbound_sms
from channels.models import ChannelType
from channels.services import ChannelResolutionError, resolve_active_connection

logger = logging.getLogger(__name__)


def _verify_twilio_signature(request) -> bool:
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
        logger.error('Twilio SMS signature verification error: %s', type(exc).__name__)
        return False


class TwilioSmsWebhookView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'intake_webhook'

    def post(self, request):
        if not _verify_twilio_signature(request):
            logger.warning('Twilio SMS webhook rejected: invalid signature')
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        ser = TwilioSmsWebhookSerializer(data=request.data)
        if not ser.is_valid():
            logger.warning('Twilio SMS webhook: invalid payload shape')
            return Response({'detail': 'Invalid payload'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            resolved = resolve_active_connection(
                provider='twilio',
                channel_type=ChannelType.SMS,
                destination=ser.validated_data.get('To', ''),
            )
        except ChannelResolutionError:
            return Response(
                {'detail': 'Unknown or inactive destination.', 'code': 'unknown_destination'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            interaction = handle_inbound_sms(
                ser.validated_data,
                organization=resolved.organization,
                channel_connection=resolved.connection,
            )
            return Response(
                {'status': 'accepted', 'interaction_id': str(interaction.id)},
                status=status.HTTP_200_OK,
            )
        except Exception:
            # 5xx makes Twilio retry the same webhook repeatedly; acknowledge receipt with 200.
            logger.error('SMS webhook processing error', exc_info=True)
            return Response(
                {'status': 'error', 'detail': 'Processing failed; logged server-side'},
                status=status.HTTP_200_OK,
            )
