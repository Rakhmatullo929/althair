from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from control_plane.models import PlatformAccessStatus, PlatformSession
from users.utils.authentication import enforce_csrf


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def network_hash(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(f"{settings.SECRET_KEY}|network|{value}".encode("utf-8")).hexdigest()


def request_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0] if forwarded else request.META.get("REMOTE_ADDR", "")).strip()


def create_platform_session(request, access) -> tuple[PlatformSession, str]:
    now = timezone.now()
    raw = secrets.token_urlsafe(48)
    session = PlatformSession.objects.create(
        access=access,
        token_hash=token_hash(raw),
        user_agent_hash=network_hash(request.META.get("HTTP_USER_AGENT", "")),
        ip_hash=network_hash(request_ip(request)),
        last_seen_at=now,
        expires_at=now + timedelta(minutes=settings.CONTROL_PLANE_SESSION_MINUTES),
    )
    return session, raw


def set_session_cookie(response, raw_token: str) -> None:
    response.set_cookie(
        settings.CONTROL_PLANE_COOKIE_NAME,
        raw_token,
        max_age=settings.CONTROL_PLANE_SESSION_MINUTES * 60,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Strict",
        path="/api/v1/internal/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(settings.CONTROL_PLANE_COOKIE_NAME, path="/api/v1/internal/")


class PlatformSessionAuthentication(BaseAuthentication):
    """Authenticate only the dedicated, opaque internal session cookie."""

    def authenticate_header(self, request):
        return "Internal"

    def authenticate(self, request):
        raw = request.COOKIES.get(settings.CONTROL_PLANE_COOKIE_NAME, "")
        if not raw:
            return None
        now = timezone.now()
        session = PlatformSession.objects.select_related("access__user").filter(
            token_hash=token_hash(raw), revoked_at__isnull=True, expires_at__gt=now
        ).first()
        if not session:
            raise AuthenticationFailed("Internal session is invalid or expired.", code="internal_session_invalid")
        access = session.access
        if access.status != PlatformAccessStatus.ACTIVE or not access.user.is_active:
            session.revoked_at = now
            session.save(update_fields=["revoked_at"])
            raise AuthenticationFailed("Internal access is unavailable.", code="internal_access_denied")
        idle_deadline = session.last_seen_at + timedelta(minutes=settings.CONTROL_PLANE_INACTIVITY_MINUTES)
        if idle_deadline <= now:
            session.revoked_at = now
            session.save(update_fields=["revoked_at"])
            raise AuthenticationFailed("Internal session is inactive.", code="internal_session_inactive")
        if request.method not in {"GET", "HEAD", "OPTIONS", "TRACE"}:
            enforce_csrf(request)
        session.last_seen_at = now
        session.save(update_fields=["last_seen_at"])
        request.platform_access = access
        request.platform_session = session
        return access.user, session
