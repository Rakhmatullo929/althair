import hashlib
import hmac
import json
import time

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from early_access.models import EarlyAccessLead
from early_access.serializers import EarlyAccessLeadSerializer


def _verify_secret(request) -> bool:
    expected = getattr(settings, "EARLY_ACCESS_WEBHOOK_SECRET", "")
    provided = request.headers.get("X-Lead-Webhook-Secret", "")
    if not expected:
        return bool(settings.DEBUG or getattr(settings, "TESTING", False))
    return hmac.compare_digest(provided, expected)


def _rate_limit_key(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",", 1)[0].strip()
    address = forwarded or request.META.get("REMOTE_ADDR", "unknown")
    digest = hmac.new(settings.SECRET_KEY.encode(), address.encode(), hashlib.sha256).hexdigest()[:24]
    return f"early-access:{digest}:{int(time.time() // 600)}"


def _is_rate_limited(request) -> bool:
    key = _rate_limit_key(request)
    if cache.add(key, 1, timeout=610):
        return False
    try:
        return cache.incr(key) > settings.EARLY_ACCESS_RATE_LIMIT
    except ValueError:
        cache.set(key, 1, timeout=610)
        return False


def _payload_hash(data: dict) -> str:
    replay_payload = {
        "full_name": data["fullName"],
        "company_name": data["companyName"],
        "contact": data["contact"],
        "industry": data["industry"],
        "preferred_channel": data["preferredChannel"],
        "note": data.get("note", ""),
        "consent": data["consent"],
        "locale": data["locale"],
        "source": data.get("source", "landing"),
        "utm": data.get("utm", {}),
    }
    encoded = json.dumps(replay_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class EarlyAccessLeadView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        if not _verify_secret(request):
            return Response(
                {"ok": False, "code": "INVALID_SECRET"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if _is_rate_limited(request):
            return Response(
                {"ok": False, "code": "RATE_LIMITED"},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        serializer = EarlyAccessLeadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"ok": False, "code": "INVALID", "fieldErrors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = serializer.validated_data
        if data.get("website"):
            return Response(
                {"ok": False, "code": "HONEYPOT_REJECTED"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload_hash = _payload_hash(data)
        try:
            lead, created = EarlyAccessLead.objects.get_or_create(
                payload_hash=payload_hash,
                defaults={
                    "full_name": data["fullName"],
                    "company_name": data["companyName"],
                    "contact": data["contact"],
                    "industry": data["industry"],
                    "preferred_channel": data["preferredChannel"],
                    "note": data.get("note", ""),
                    "consent": data["consent"],
                    "locale": data["locale"],
                    "source": data.get("source", "landing"),
                    "utm": data.get("utm", {}),
                },
            )
        except IntegrityError:
            created = False
            lead = EarlyAccessLead.objects.get(payload_hash=payload_hash)
        return Response(
            {"ok": True, "code": "STORED" if created else "DUPLICATE", "id": str(lead.id)},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
