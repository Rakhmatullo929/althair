import logging

from django.conf import settings
from django.utils.deprecation import MiddlewareMixin

security_logger = logging.getLogger('security.audit')


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    Adds production security headers to all responses.
    - Content-Security-Policy
    - Permissions-Policy
    - Cache-Control for API responses
    - X-Request-ID propagation
    """

    def process_response(self, request, response):
        # Content-Security-Policy
        if not settings.DEBUG:
            csp = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; "
                "font-src 'self'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            )
            response['Content-Security-Policy'] = csp

        # Permissions-Policy — disable unused browser features
        response['Permissions-Policy'] = (
            'camera=(), microphone=(), geolocation=(), '
            'payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()'
        )

        # Prevent API responses from being cached by intermediaries
        if request.path.startswith('/api/'):
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
            response['Pragma'] = 'no-cache'

        # Propagate X-Request-ID for tracing
        request_id = request.META.get('HTTP_X_REQUEST_ID', '')
        if request_id:
            response['X-Request-ID'] = request_id

        return response

    def process_request(self, request):
        # Log suspicious patterns (SQL injection, path traversal)
        path = request.path
        suspicious_patterns = ["'", '"', '--', ';', '../', '..\\', '<script', 'UNION SELECT']
        for pattern in suspicious_patterns:
            if pattern.lower() in path.lower():
                security_logger.warning(
                    'Suspicious request path detected: %s from %s',
                    path[:200],
                    self._get_client_ip(request),
                )
                break

    @staticmethod
    def _get_client_ip(request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', 'unknown')
