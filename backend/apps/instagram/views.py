from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.api.pagination import StandardPagination
from crm.models import Conversation, Message
from instagram.models import (
    InstagramConnection,
    InstagramConnectionStatus,
    InstagramWebhookEvent,
)
from instagram.serializers import InstagramConnectionSerializer
from instagram.services import (
    InstagramError,
    complete_oauth,
    connection_health,
    create_oauth_state,
    disconnect_instagram,
    integration_readiness,
    receive_webhook,
    reconnect_fake,
)
from organizations.permissions import (
    HasOrganizationRole,
    IsOrganizationMember,
    OrganizationContextMixin,
)


class InstagramErrorMixin:
    def handle_exception(self, exc):
        if isinstance(exc, InstagramError):
            return Response(
                {"error": {"code": exc.code}},
                status=exc.status_code,
            )
        return super().handle_exception(exc)


class InstagramTenantView(InstagramErrorMixin, OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = "manage_channels"

    def get_object(self, request, connection_id):
        return get_object_or_404(
            InstagramConnection.objects.for_organization(request.organization).select_related(
                "channel_connection", "organization", "connected_by__user"
            ),
            pk=connection_id,
        )


class InstagramConnectionListView(InstagramTenantView):
    def get(self, request):
        rows = InstagramConnection.objects.for_organization(request.organization).select_related(
            "channel_connection"
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            InstagramConnectionSerializer(page, many=True, context={"request": request}).data
        )


class InstagramConnectionDetailView(InstagramTenantView):
    def get(self, request, connection_id):
        return Response(
            InstagramConnectionSerializer(
                self.get_object(request, connection_id), context={"request": request}
            ).data
        )

    def patch(self, request, connection_id):
        connection = self.get_object(request, connection_id)
        serializer = InstagramConnectionSerializer(
            connection,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class InstagramOAuthStartView(InstagramTenantView):
    def get(self, request):
        intended_redirect = request.query_params.get(
            "redirect", f"/{request.organization.default_language}/app/settings/channels/instagram"
        )
        return Response(create_oauth_state(request=request, intended_redirect=intended_redirect))


class InstagramOAuthCallbackView(InstagramErrorMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        connection = complete_oauth(
            user=request.user,
            raw_state=request.query_params.get("state", ""),
            code=request.query_params.get("code", ""),
        )
        if settings.META_INSTAGRAM_ENABLE_LIVE:
            return HttpResponseRedirect(
                f"{settings.CLIENT_APP_URL.rstrip('/')}{connection.oauth_redirect.rstrip('/')}/{connection.id}?connected=1"
            )
        return Response(
            {
                "connection": InstagramConnectionSerializer(
                    connection, context={"request": request}
                ).data,
                "redirect": f"{connection.oauth_redirect.rstrip('/')}/{connection.id}",
            }
        )


class InstagramDisconnectView(InstagramTenantView):
    def post(self, request, connection_id):
        connection = disconnect_instagram(
            self.get_object(request, connection_id), request.organization_membership
        )
        return Response(
            InstagramConnectionSerializer(connection, context={"request": request}).data
        )


class InstagramReconnectView(InstagramTenantView):
    def post(self, request, connection_id):
        connection = reconnect_fake(
            self.get_object(request, connection_id), request.organization_membership
        )
        return Response(
            InstagramConnectionSerializer(connection, context={"request": request}).data
        )


class InstagramHealthView(InstagramTenantView):
    def get(self, request, connection_id):
        return Response(connection_health(self.get_object(request, connection_id)))

    def post(self, request, connection_id):
        return Response(
            connection_health(self.get_object(request, connection_id), run_provider=True)
        )


class InstagramBackfillView(InstagramTenantView):
    def post(self, request, connection_id):
        connection = self.get_object(request, connection_id)
        limit = max(1, min(int(request.data.get("limit", 50)), settings.META_INSTAGRAM_BACKFILL_MAX_ITEMS))
        from instagram.tasks import bounded_instagram_backfill

        bounded_instagram_backfill.delay(str(connection.id), limit)
        return Response({"status": "queued", "limit": limit}, status=202)


class InstagramWebhookView(InstagramErrorMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "instagram_webhook"

    def get(self, request):
        mode = request.query_params.get("hub.mode", "")
        challenge = request.query_params.get("hub.challenge", "")
        supplied = request.query_params.get("hub.verify_token", "")
        if (
            mode != "subscribe"
            or not settings.META_INSTAGRAM_VERIFY_TOKEN
            or not hmac.compare_digest(supplied, settings.META_INSTAGRAM_VERIFY_TOKEN)
        ):
            raise InstagramError("webhook_verification_failed", status_code=403)
        return HttpResponse(challenge, content_type="text/plain")

    def post(self, request):
        result = receive_webhook(
            raw_body=request.body,
            signature=request.headers.get("X-Hub-Signature-256", ""),
        )
        return Response(result, status=status.HTTP_202_ACCEPTED)


class InstagramTestEventView(InstagramTenantView):
    def post(self, request, connection_id):
        if not integration_readiness()["fake_provider"]:
            raise InstagramError("test_provider_unavailable", status_code=404)
        connection = self.get_object(request, connection_id)
        event_type = str(request.data.get("event_type", "message"))
        sender_id = str(request.data.get("sender_id", "igscoped_test_customer"))[:255]
        message_id = str(request.data.get("message_id") or f"ig_test_{uuid.uuid4().hex}")[:255]
        timestamp = int(request.data.get("timestamp") or timezone.now().timestamp() * 1000)
        item = {
            "sender": {"id": sender_id},
            "recipient": {"id": connection.instagram_user_id},
            "timestamp": timestamp,
        }
        if event_type in {"message", "story_reply", "shared_post", "echo", "edit"}:
            message = {
                "mid": message_id,
                "text": str(request.data.get("text", "Hello from Instagram"))[:10000],
            }
            if event_type == "story_reply":
                message["reply_to"] = {"mid": "story_reference"}
            if event_type == "shared_post":
                message["shares"] = {"link": "redacted-provider-reference"}
            if event_type == "echo":
                message["is_echo"] = True
                item["sender"] = {"id": connection.instagram_user_id}
            if event_type == "edit":
                message["is_edited"] = True
            item["message"] = message
        elif event_type == "reaction":
            item["reaction"] = {
                "mid": message_id,
                "reaction": str(request.data.get("reaction", "love"))[:40],
            }
        elif event_type == "read":
            item["read"] = {"watermark": timestamp}
        payload = {
            "object": "instagram",
            "entry": [{"id": connection.instagram_user_id, "messaging": [item]}],
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signature = "sha256=" + hmac.new(
            settings.META_APP_SECRET.encode("utf-8"), raw, hashlib.sha256
        ).hexdigest()
        return Response(receive_webhook(raw_body=raw, signature=signature), status=202)


class InstagramTestControlView(InstagramTenantView):
    def post(self, request, connection_id):
        if not integration_readiness()["fake_provider"]:
            raise InstagramError("test_provider_unavailable", status_code=404)
        connection = self.get_object(request, connection_id)
        action = str(request.data.get("action", ""))
        if action == "expire_token":
            connection.token_expires_at = timezone.now() - timedelta(minutes=1)
            connection.connection_status = InstagramConnectionStatus.EXPIRED
            connection.last_error_code = "access_token_expired"
            connection.save(
                update_fields=[
                    "token_expires_at",
                    "connection_status",
                    "last_error_code",
                    "updated_at",
                ]
            )
        elif action == "permission_missing":
            connection.permission_snapshot = ["instagram_business_basic"]
            connection.connection_status = InstagramConnectionStatus.DEGRADED
            connection.last_error_code = "permission_missing"
            connection.save(
                update_fields=[
                    "permission_snapshot",
                    "connection_status",
                    "last_error_code",
                    "updated_at",
                ]
            )
        elif action == "approve_human_agent":
            connection.human_agent_approved = True
            connection.save(update_fields=["human_agent_approved", "updated_at"])
        elif action == "expire_window":
            conversation = get_object_or_404(
                Conversation.objects.for_organization(request.organization),
                pk=request.data.get("conversation_id"),
                channel_connection=connection.channel_connection,
            )
            window = conversation.instagram_window
            window.standard_window_expires_at = timezone.now() - timedelta(minutes=1)
            window.save(update_fields=["standard_window_expires_at", "updated_at"])
        elif action == "restore":
            connection.connection_status = InstagramConnectionStatus.CONNECTED
            connection.permission_snapshot = [
                "instagram_business_basic",
                "instagram_business_manage_messages",
            ]
            connection.token_expires_at = timezone.now() + timedelta(days=60)
            connection.last_error_code = ""
            connection.channel_connection.status = "active"
            connection.channel_connection.save(update_fields=["status", "updated_at"])
            connection.save(
                update_fields=[
                    "connection_status",
                    "permission_snapshot",
                    "token_expires_at",
                    "last_error_code",
                    "updated_at",
                ]
            )
        else:
            raise InstagramError("test_action_invalid")
        return Response(
            InstagramConnectionSerializer(connection, context={"request": request}).data
        )


class InstagramOperationsView(InstagramTenantView):
    def get(self, request, connection_id):
        connection = self.get_object(request, connection_id)
        events = InstagramWebhookEvent.objects.for_organization(request.organization).filter(
            connection=connection
        )
        return Response(
            {
                "events": {
                    "received": events.count(),
                    "failed": events.filter(status__in=["failed", "dead_letter"]).count(),
                },
                "outbound": connection_health(connection)["queue"],
                "backfill": {
                    "scope": "bounded_recent_only",
                    "last_synced_at": connection.channel_connection.last_synced_at,
                },
            }
        )
