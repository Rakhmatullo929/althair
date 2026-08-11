from __future__ import annotations

import json
import uuid

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.api.pagination import StandardPagination
from organizations.permissions import HasOrganizationRole, IsOrganizationMember, OrganizationContextMixin
from telegram.models import (
    TelegramBotConnection,
    TelegramManagedBotRequest,
    TelegramManagerEvent,
    TelegramWebhookEvent,
)
from telegram.serializers import TelegramBotConnectionSerializer, TelegramManagedBotRequestSerializer, TelegramUserLinkSerializer
from telegram.services import (
    TelegramError,
    active_user_link,
    connect_existing_bot,
    connection_health,
    create_managed_request,
    create_user_link,
    integration_readiness,
    manager_health,
    process_manager_event,
    process_webhook_event,
    receive_bot_update,
    receive_manager_update,
    revoke_user_link,
    rotate_token,
    set_connection_state,
    update_access_settings,
)


class TelegramErrorMixin:
    def handle_exception(self, exc):
        if isinstance(exc, TelegramError):
            return Response({"error": {"code": exc.code}}, status=exc.status_code)
        return super().handle_exception(exc)


class TelegramTenantView(TelegramErrorMixin, OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = "manage_channels"

    def get_object(self, request, connection_id):
        return get_object_or_404(
            TelegramBotConnection.objects.for_organization(request.organization).select_related("channel_connection", "organization", "connected_by__user"),
            pk=connection_id,
        )


class TelegramReadinessView(TelegramTenantView):
    def get(self, request):
        return Response(manager_health())

    def post(self, request):
        return Response(manager_health(run_provider=True))


class TelegramIdentityView(TelegramTenantView):
    def get(self, request):
        link = active_user_link(request.user)
        return Response(TelegramUserLinkSerializer(link).data if link else {"status": "not_linked"})

    def post(self, request):
        return Response(create_user_link(user=request.user), status=201)

    def delete(self, request):
        return Response(revoke_user_link(user=request.user))


class TelegramManagedRequestListView(TelegramTenantView):
    def get(self, request):
        rows = TelegramManagedBotRequest.objects.for_organization(request.organization).select_related("requested_by")
        return Response(TelegramManagedBotRequestSerializer(rows, many=True).data)

    def post(self, request):
        result = create_managed_request(
            organization=request.organization,
            membership=request.organization_membership,
            user=request.user,
            suggested_name=str(request.data.get("suggested_name") or ""),
            suggested_username=str(request.data.get("suggested_username") or ""),
        )
        return Response({"request": TelegramManagedBotRequestSerializer(result["request"]).data, "creation_url": result["creation_url"]}, status=201)


class TelegramExistingBotView(TelegramTenantView):
    def post(self, request):
        connection = connect_existing_bot(organization=request.organization, membership=request.organization_membership, token=str(request.data.get("token") or ""))
        return Response(TelegramBotConnectionSerializer(connection, context={"request": request}).data, status=201)


class TelegramConnectionListView(TelegramTenantView):
    def get(self, request):
        rows = TelegramBotConnection.objects.for_organization(request.organization).select_related("channel_connection")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(TelegramBotConnectionSerializer(page, many=True, context={"request": request}).data)


class TelegramConnectionDetailView(TelegramTenantView):
    def get(self, request, connection_id):
        return Response(TelegramBotConnectionSerializer(self.get_object(request, connection_id), context={"request": request}).data)

    def patch(self, request, connection_id):
        connection = self.get_object(request, connection_id)
        serializer = TelegramBotConnectionSerializer(connection, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class TelegramConnectionHealthView(TelegramTenantView):
    def get(self, request, connection_id):
        return Response(connection_health(self.get_object(request, connection_id)))

    def post(self, request, connection_id):
        return Response(connection_health(self.get_object(request, connection_id), run_provider=True))


class TelegramRotateTokenView(TelegramTenantView):
    def post(self, request, connection_id):
        connection = rotate_token(connection=self.get_object(request, connection_id), membership=request.organization_membership, replacement_token=str(request.data.get("replacement_token") or ""))
        return Response(TelegramBotConnectionSerializer(connection, context={"request": request}).data)


class TelegramAccessSettingsView(TelegramTenantView):
    def post(self, request, connection_id):
        connection = update_access_settings(connection=self.get_object(request, connection_id), membership=request.organization_membership, restricted=bool(request.data.get("access_restricted")), user_ids=request.data.get("permitted_telegram_user_ids") or [])
        return Response(TelegramBotConnectionSerializer(connection, context={"request": request}).data)


class TelegramConnectionActionView(TelegramTenantView):
    def post(self, request, connection_id, action):
        connection = set_connection_state(connection=self.get_object(request, connection_id), membership=request.organization_membership, action=action)
        return Response(TelegramBotConnectionSerializer(connection, context={"request": request}).data)


class TelegramManagerWebhookView(TelegramErrorMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "telegram_manager_webhook"

    def post(self, request):
        return Response(receive_manager_update(raw_body=request.body, secret_header=request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")), status=status.HTTP_202_ACCEPTED)


class TelegramBotWebhookView(TelegramErrorMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "telegram_bot_webhook"

    def post(self, request, public_key):
        return Response(receive_bot_update(public_key=public_key, raw_body=request.body, secret_header=request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")), status=status.HTTP_202_ACCEPTED)


class TelegramTestManagerEventView(TelegramTenantView):
    def post(self, request):
        if not integration_readiness()["fake_provider"]:
            raise TelegramError("test_provider_unavailable", status_code=404)
        event_type = str(request.data.get("event_type") or "identity_link")
        update_id = int(request.data.get("update_id") or int(timezone.now().timestamp() * 1000))
        if event_type == "identity_link":
            start_parameter = str(request.data.get("start_parameter") or "")
            payload = {"update_id": update_id, "message": {"message_id": 1, "from": {"id": int(request.data.get("telegram_user_id") or 700001), "is_bot": False, "first_name": "Portal owner", "username": str(request.data.get("telegram_username") or "portal_owner")}, "chat": {"id": int(request.data.get("telegram_user_id") or 700001), "type": "private"}, "text": f"/start {start_parameter}"}}
        elif event_type == "managed_bot":
            managed_request = get_object_or_404(TelegramManagedBotRequest.objects.for_organization(request.organization), pk=request.data.get("request_id"))
            bot_id = int(request.data.get("bot_user_id") or 900000 + update_id % 100000)
            payload = {"update_id": update_id, "managed_bot": {"user": {"id": managed_request.linked_telegram_user_id, "is_bot": False, "first_name": "Owner"}, "bot": {"id": bot_id, "is_bot": True, "first_name": managed_request.suggested_name, "username": managed_request.suggested_username}}}
        else:
            raise TelegramError("test_event_invalid")
        response = receive_manager_update(raw_body=json.dumps(payload, separators=(",", ":")).encode(), secret_header=settings.TELEGRAM_MANAGER_WEBHOOK_SECRET)
        event = TelegramManagerEvent.objects.get(update_id=update_id)
        process_manager_event(event.id)
        return Response(response, status=202)


class TelegramTestBotEventView(TelegramTenantView):
    def post(self, request, connection_id):
        if not integration_readiness()["fake_provider"]:
            raise TelegramError("test_provider_unavailable", status_code=404)
        connection = self.get_object(request, connection_id)
        event_type = str(request.data.get("event_type") or "message")
        update_id = int(request.data.get("update_id") or int(timezone.now().timestamp() * 1000))
        user_id = int(request.data.get("telegram_user_id") or 710001)
        chat_id = int(request.data.get("chat_id") or user_id)
        base_message = {"message_id": int(request.data.get("message_id") or update_id % 1000000), "date": int(timezone.now().timestamp()), "from": {"id": user_id, "is_bot": False, "first_name": str(request.data.get("first_name") or "Telegram customer"), "username": str(request.data.get("username") or "tg_customer")}, "chat": {"id": chat_id, "type": str(request.data.get("chat_type") or "private")}, "text": str(request.data.get("text") or "Hello from Telegram")}
        if request.data.get("reply_to_message_id"):
            base_message["reply_to_message"] = {"message_id": int(request.data["reply_to_message_id"])}
        media_type = str(request.data.get("media_type") or "")
        if media_type:
            base_message.pop("text", None)
            base_message[media_type] = [{"file_id": "test-file-id", "file_size": 1024}] if media_type == "photo" else {"file_id": "test-file-id", "file_size": 1024, "mime_type": "application/octet-stream"}
        if event_type in {"message", "edited_message"}:
            payload = {"update_id": update_id, event_type: base_message}
        elif event_type == "callback_query":
            payload = {"update_id": update_id, "callback_query": {"id": f"callback-{update_id}", "from": base_message["from"], "message": base_message, "data": str(request.data.get("callback_data") or "approved_action")}}
        elif event_type == "my_chat_member":
            payload = {"update_id": update_id, "my_chat_member": {"from": base_message["from"], "chat": base_message["chat"], "new_chat_member": {"status": str(request.data.get("new_status") or "kicked")}}}
        else:
            raise TelegramError("test_event_invalid")
        secret = str(connection.channel_connection.get_credentials().get("webhook_secret") or "")
        response = receive_bot_update(public_key=connection.webhook_public_key, raw_body=json.dumps(payload, separators=(",", ":")).encode(), secret_header=secret)
        event = TelegramWebhookEvent.objects.get(connection=connection, update_id=update_id)
        process_webhook_event(event.id)
        return Response(response, status=202)
