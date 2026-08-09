# apps/users/authentication.py
import logging

from rest_framework_simplejwt.authentication import JWTAuthentication
from django.conf import settings
import hashlib
import hmac

from rest_framework.authentication import BaseAuthentication
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import BasePermission

logger = logging.getLogger(__name__)


class CookieJWTAuthentication(JWTAuthentication):
    """
    Читает JWT из HttpOnly cookie (fallback, если нет Authorization).
    """

    def get_header(self, request):
        header = super().get_header(request)
        if header:
            return header
        cookie_name = settings.SIMPLE_JWT.get("AUTH_COOKIE", "jwt-auth")
        raw = request.COOKIES.get(cookie_name)
        if raw:
            return f"Bearer {raw}".encode()
        return None

    def authenticate(self, request):
        authorization_header = JWTAuthentication.get_header(self, request)
        using_cookie = not authorization_header and bool(
            request.COOKIES.get(settings.SIMPLE_JWT.get("AUTH_COOKIE", "jwt-auth"))
        )
        result = super().authenticate(request)
        if result and using_cookie and request.method not in {"GET", "HEAD", "OPTIONS", "TRACE"}:
            enforce_csrf(request)
        return result


def enforce_csrf(request) -> None:
    """Apply Django's origin and double-submit CSRF checks to JSON APIs."""
    check = CSRFCheck(lambda incoming_request: None)
    check.process_request(request)
    reason = check.process_view(request, None, (), {})
    if reason:
        raise PermissionDenied(f"CSRF verification failed: {reason}")


class StaticBearerAuthentication(BaseAuthentication):
    """
    Static token authentication using SHA256 hash validation.
    Token should be provided in Authorization header: Bearer <token>
    Returns None if no Bearer header is present (allows other authenticators to try).
    Raises AuthenticationFailed if token is present but invalid.
    """

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return None  # No Bearer token — let other authenticators try

        token = auth_header[7:].strip()

        if not token:
            raise AuthenticationFailed("Token not provided")

        expected_hash = getattr(settings, "EXPECTED_API_TOKEN_SHA256", "")

        if not expected_hash:
            raise AuthenticationFailed("Server configuration error")

        incoming_hash = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(incoming_hash, expected_hash):
            raise AuthenticationFailed("Invalid token")

        # Never retain or expose the raw bearer token on the request object.
        return (None, "static-bearer-verified")


class HasStaticToken(BasePermission):
    """
    Allows access only to requests authenticated via StaticTokenAuthentication.
    """

    def has_permission(self, request, view):
        return request.auth is not None
