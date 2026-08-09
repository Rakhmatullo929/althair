"""Shared helpers for the SMS chat agent."""

import datetime as _datetime
import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)


MAX_TOOL_ITERATIONS = 6
OPENAI_MODEL = 'gpt-4o-mini'
OPENAI_TEMPERATURE = 0.2
OPENAI_MAX_TOKENS = 900
OPENAI_TIMEOUT = 15.0


def normalize_phone(raw: str) -> str:
    """Strip everything but digits and a leading plus to get an E.164-ish form."""
    if not raw:
        return ''
    cleaned = ''.join(c for c in raw if c.isdigit() or c == '+')
    return cleaned or raw


def parse_iso_date(value):
    """Return a date for an ISO YYYY-MM-DD string, or None if it doesn't parse."""
    if not value:
        return None
    try:
        return _datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        logger.info('parse_iso_date: %r did not parse; returning None', value)
        return None


@lru_cache(maxsize=1)
def get_openai_client():
    """Return a lazily-created singleton OpenAI client.

    NOTE: a singleton holds a connection pool.  Tests should patch
    ``intake.services.sms_chat.agent.get_openai_client`` (the bound name)
    rather than this function, to avoid the cached real client leaking
    across tests.
    """
    import openai
    api_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key:
        logger.warning('OPENAI_API_KEY not set — agent calls will fail')
    return openai.OpenAI(api_key=api_key, timeout=OPENAI_TIMEOUT)
