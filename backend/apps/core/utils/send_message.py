import logging

from twilio.rest import Client
from django.conf import settings

logger = logging.getLogger(__name__)


def send_message(message, to, user=None):
    if getattr(settings, 'DEBUG_SMS', False):
        logger.info('DEBUG_SMS enabled — skipping SMS to %s', to[:4] + '****')
        return

    if user and not getattr(user, 'is_sms_enabled', True):
        return

    sid = getattr(settings, 'TWILIO_ACCOUNT_SID', '')
    token = getattr(settings, 'TWILIO_AUTH_TOKEN', '')
    from_number = getattr(settings, 'TWILIO_PHONE_NUMBER', '')

    if not all([sid, token, from_number]):
        logger.warning('Twilio not configured — skipping SMS')
        return

    try:
        client = Client(sid, token)
        result = client.messages.create(
            body=message,
            from_=from_number,
            to=f'+{to}' if not to.startswith('+') else to,
        )
        return result.sid
    except Exception:
        logger.error('Twilio SMS failed', exc_info=True)
