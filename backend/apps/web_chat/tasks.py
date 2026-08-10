from celery import shared_task

from web_chat.services import cleanup_expired_sessions


@shared_task
def cleanup_web_chat_retention():
    return {"processed": cleanup_expired_sessions(limit=500)}
