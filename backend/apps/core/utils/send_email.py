# core/utils/send_email.py
import time
from typing import Iterable, Union, List, Tuple

import sendgrid
from sendgrid.helpers.mail import (
    Mail, Email, To, Personalization, MailSettings, SandBoxMode, CustomArg
)
from django.conf import settings

# --- email-validator с фолбэком ---
try:
    from email_validator import validate_email, EmailNotValidError
    _HAS_EMAIL_VALIDATOR = True
except Exception:
    _HAS_EMAIL_VALIDATOR = False
    class EmailNotValidError(Exception): ...
    import re
    _EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _soft_validate_email(addr: str) -> str | None:
    s = (addr or "").strip()
    if not s:
        return None
    if _HAS_EMAIL_VALIDATOR:
        try:
            v = validate_email(s, allow_smtputf8=False)
            return v.normalized
        except EmailNotValidError:
            return None
    # Простой фолбэк-валидатор
    return s if _EMAIL_RE.match(s) else None


API_KEY = getattr(settings, "SENDGRID_API_KEY", None)
FROM_EMAIL = Email(
    email=getattr(settings, "SENDGRID_FROM_EMAIL", "noreply@example.com"),
    name=getattr(settings, "SENDGRID_FROM_NAME", "App"),
)
MAX_RECIPIENTS = 50
RETRY_ATTEMPTS = 3
RETRY_BASE_SLEEP = 0.6


def _normalize_and_validate(recipients: Union[str, Iterable[str]]) -> List[str]:
    candidates = [recipients] if isinstance(recipients, str) else list(recipients or [])
    cleaned: List[str] = []
    seen: set[str] = set()

    for raw in candidates:
        addr = _soft_validate_email(raw)
        if not addr:
            continue
        if addr not in seen:
            seen.add(addr)
            cleaned.append(addr)

    if len(cleaned) > MAX_RECIPIENTS:
        cleaned = cleaned[:MAX_RECIPIENTS]
    return cleaned


def _should_retry(status_code: int) -> bool:
    return status_code in (429, 500, 502, 503, 504)


def _mail_request_body(mail: Mail) -> dict:
    """
    Универсальная сериализация письма под разные версии sendgrid SDK:
    try mail.get(), fallback на mail.to_dict().
    """
    if hasattr(mail, "get"):
        return mail.get()  # type: ignore[no-any-return]
    if hasattr(mail, "to_dict"):
        return mail.to_dict()  # type: ignore[no-any-return]
    # На всякий случай (не должно понадобиться)
    raise RuntimeError("SendGrid Mail object has no serializer (.get()/.to_dict())")


def send_email(
    template_id: str,
    data: dict,
    to_emails: Union[str, Iterable[str]],
    *,
    user=None,
    request_id: str | None = None,   # для трассировки/идемпотентности
    sandbox: bool | None = None,     # форсировать песочницу в тестах
) -> Tuple[bool, List[str]]:
    """
    Отправка по SendGrid Dynamic Template нескольким адресатам.
    Возвращает (ok, sent_recipients). Без логирования PII.
    """
    # Предохранители конфигурации
    if not API_KEY:
        raise RuntimeError("SendGrid API key is not configured (SENDGRID_API_KEY)")

    if user and getattr(user, "is_email_enabled", True) is False:
        return True, []

    recipients = _normalize_and_validate(to_emails)
    if not recipients:
        raise ValueError("No valid recipients")

    sg = sendgrid.SendGridAPIClient(api_key=API_KEY)

    mail = Mail()
    mail.from_email = FROM_EMAIL
    mail.template_id = template_id

    # Sandbox по умолчанию в DEBUG (или если явно задан)
    effective_sandbox = sandbox if sandbox is not None else bool(getattr(settings, "DEBUG", False))
    if effective_sandbox:
        ms = MailSettings()
        ms.sandbox_mode = SandBoxMode(True)
        mail.mail_settings = ms

    # Безопасные custom args (никакой PII)
    common_args: dict[str, str] = {}
    if request_id:
        common_args["request_id"] = str(request_id)

    # Персонализации по каждому получателю
    for rcpt in recipients:
        p = Personalization()
        p.add_to(To(rcpt))
        p.dynamic_template_data = data
        for k, v in common_args.items():
            p.add_custom_arg(CustomArg(k, v))  # <-- правильный способ
        mail.add_personalization(p)

    # Ретраи на 429/5xx с экспоненциальным бэкоффом
    attempt = 0
    while True:
        attempt += 1
        try:
            body = _mail_request_body(mail)
            resp = sg.client.mail.send.post(request_body=body)

            if 200 <= resp.status_code < 300:
                # письма отправлены один раз на каждого уникального валидного адресата
                return True, recipients

            if _should_retry(resp.status_code) and attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BASE_SLEEP * (2 ** (attempt - 1)))
                continue

            raise RuntimeError(f"SendGrid non-success: {resp.status_code}")
        except Exception:
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BASE_SLEEP * (2 ** (attempt - 1)))
                continue
            raise
