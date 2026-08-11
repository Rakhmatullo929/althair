from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.api.pagination import StandardPagination
from gmail_integration.models import GmailConnection
from gmail_integration.serializers import GmailConnectionSerializer
from gmail_integration.services import (
    GmailError,
    bounded_full_sync,
    cancel_initial_sync,
    complete_oauth,
    connection_health,
    create_oauth_state,
    disconnect_gmail,
    erase_gmail_contact_data,
    export_gmail_contact_data,
    integration_readiness,
    fetch_attachment,
    receive_pubsub,
    renew_watch,
)
from organizations.permissions import HasOrganizationRole, IsOrganizationMember, OrganizationContextMixin


class GmailErrorMixin:
    def handle_exception(self, exc):
        if isinstance(exc, GmailError):
            return Response({"error": {"code": exc.code}}, status=exc.status_code)
        return super().handle_exception(exc)


class GmailTenantView(GmailErrorMixin, OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = "manage_channels"

    def get_object(self, request, connection_id):
        return get_object_or_404(
            GmailConnection.objects.for_organization(request.organization).select_related(
                "channel_connection", "connected_by__user"
            ).prefetch_related("sync_runs"),
            pk=connection_id,
        )


class GmailReadinessView(GmailTenantView):
    def get(self, request):
        return Response(integration_readiness())


class GmailConnectionListView(GmailTenantView):
    def get(self, request):
        rows = GmailConnection.objects.for_organization(request.organization).select_related(
            "channel_connection"
        ).prefetch_related("sync_runs")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            GmailConnectionSerializer(page, many=True, context={"request": request}).data
        )


class GmailConnectionDetailView(GmailTenantView):
    def get(self, request, connection_id):
        return Response(GmailConnectionSerializer(self.get_object(request, connection_id), context={"request": request}).data)

    def patch(self, request, connection_id):
        connection = self.get_object(request, connection_id)
        serializer = GmailConnectionSerializer(connection, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class GmailOAuthStartView(GmailTenantView):
    def get(self, request):
        redirect = request.query_params.get(
            "redirect", f"/{request.organization.default_language}/app/settings/channels/gmail"
        )
        return Response(
            create_oauth_state(
                request=request,
                intended_redirect=redirect,
                initial_sync_mode=request.query_params.get("initial_sync_mode", "recent"),
                initial_sync_max_messages=request.query_params.get(
                    "initial_sync_max_messages", 100
                ),
            )
        )


class GmailOAuthCallbackView(GmailErrorMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        connection = complete_oauth(
            user=request.user,
            raw_state=request.query_params.get("state", ""),
            code=request.query_params.get("code", ""),
        )
        if settings.GOOGLE_GMAIL_ENABLE_LIVE:
            return HttpResponseRedirect(
                f"{settings.CLIENT_APP_URL.rstrip('/')}{connection.oauth_redirect.rstrip('/')}/{connection.id}?connected=1"
            )
        return Response(
            {
                "connection": GmailConnectionSerializer(connection, context={"request": request}).data,
                "redirect": f"{connection.oauth_redirect.rstrip('/')}/{connection.id}",
            }
        )


class GmailDisconnectView(GmailTenantView):
    def post(self, request, connection_id):
        connection = disconnect_gmail(self.get_object(request, connection_id), request.organization_membership)
        return Response(GmailConnectionSerializer(connection, context={"request": request}).data)


class GmailReconnectView(GmailTenantView):
    def post(self, request, connection_id):
        connection = self.get_object(request, connection_id)
        redirect = str(
            request.data.get(
                "redirect",
                f"/{request.organization.default_language}/app/settings/channels/gmail/{connection.id}",
            )
        )
        return Response(
            create_oauth_state(
                request=request,
                intended_redirect=redirect,
                initial_sync_mode=connection.initial_sync_mode,
                initial_sync_max_messages=connection.initial_sync_max_messages,
                reconnect_connection=connection,
            )
        )


class GmailHealthView(GmailTenantView):
    def get(self, request, connection_id):
        return Response(connection_health(self.get_object(request, connection_id)))

    def post(self, request, connection_id):
        return Response(connection_health(self.get_object(request, connection_id), run_provider=True))


class GmailRenewWatchView(GmailTenantView):
    def post(self, request, connection_id):
        connection = renew_watch(self.get_object(request, connection_id))
        return Response(GmailConnectionSerializer(connection, context={"request": request}).data)


class GmailResyncView(GmailTenantView):
    def post(self, request, connection_id):
        run = bounded_full_sync(self.get_object(request, connection_id), fallback_reason="manual_resync")
        return Response({"status": run.status, "sync_run_id": str(run.id), "imported": run.imported_count}, status=202)


class GmailCancelInitialSyncView(GmailTenantView):
    def post(self, request, connection_id):
        connection = cancel_initial_sync(
            self.get_object(request, connection_id), request.organization_membership
        )
        return Response(
            GmailConnectionSerializer(connection, context={"request": request}).data
        )


class GmailPrivacyView(GmailTenantView):
    required_action = "manage_channels"

    def get(self, request, connection_id):
        connection = self.get_object(request, connection_id)
        contact_id = request.query_params.get("contact_id", "")
        return Response(
            export_gmail_contact_data(connection=connection, contact_id=contact_id)
        )

    def post(self, request, connection_id):
        if request.data.get("confirm") is not True:
            raise GmailError("privacy_confirmation_required")
        connection = self.get_object(request, connection_id)
        return Response(
            erase_gmail_contact_data(
                connection=connection,
                contact_id=request.data.get("contact_id", ""),
                mode=str(request.data.get("mode") or "anonymize"),
                actor=request.organization_membership,
            )
        )


class GmailPubSubView(GmailErrorMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "gmail_pubsub"

    def post(self, request):
        result = receive_pubsub(
            authorization=request.headers.get("Authorization", ""), raw_body=request.body
        )
        return Response(result, status=status.HTTP_202_ACCEPTED)


def _fake_payload(
    *,
    connection,
    message_id,
    sender,
    subject,
    text,
    thread_id,
    automated=False,
    html=False,
    attachment=False,
):
    encoded = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
    headers = [
        {"name": "From", "value": sender},
        {"name": "To", "value": connection.mailbox_email},
        {"name": "Subject", "value": subject},
        {"name": "Message-ID", "value": f"<{message_id}@example.test>"},
        {"name": "Date", "value": datetime.now(dt_timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")},
    ]
    if automated:
        headers.append({"name": "Auto-Submitted", "value": "auto-generated"})
    body_part = {
        "mimeType": "text/html" if html else "text/plain",
        "headers": [],
        "body": {"data": encoded, "size": len(text)},
    }
    payload = body_part
    if attachment:
        attachment_data = base64.urlsafe_b64encode(b"%PDF-1.4 test invoice").decode().rstrip("=")
        payload = {
            "mimeType": "multipart/mixed",
            "headers": [],
            "parts": [
                body_part,
                {
                    "mimeType": "application/pdf",
                    "filename": "invoice.pdf",
                    "body": {
                        "attachmentId": "fake-attachment-1",
                        "data": attachment_data,
                        "size": 21,
                    },
                },
            ],
        }
    payload["headers"] = headers
    return {
        "id": message_id,
        "threadId": thread_id,
        "historyId": str(int(timezone.now().timestamp())),
        "labelIds": ["INBOX", "UNREAD"],
        "payload": payload,
    }


class GmailTestInboundView(GmailTenantView):
    def post(self, request, connection_id):
        if not integration_readiness()["fake_provider"]:
            raise GmailError("test_provider_unavailable", status_code=404)
        connection = self.get_object(request, connection_id)
        message_id = str(request.data.get("message_id") or f"gmail_test_{uuid.uuid4().hex}")[:255]
        thread_id = str(request.data.get("thread_id") or "gmail_test_thread")[:255]
        payload = _fake_payload(
            connection=connection,
            message_id=message_id,
            sender=str(request.data.get("sender") or "customer@example.test")[:320],
            subject=str(request.data.get("subject") or "Product question")[:500],
            text=str(request.data.get("text") or "Hello from Gmail")[:100000],
            thread_id=thread_id,
            automated=bool(request.data.get("automated")),
            html=bool(request.data.get("html")),
            attachment=bool(request.data.get("attachment")),
        )
        configuration = connection.channel_connection.configuration
        messages = dict(configuration.get("fake_messages") or {})
        messages.pop(message_id, None)
        messages[message_id] = payload
        configuration["fake_messages"] = dict(list(messages.items())[-100:])
        pending = list(configuration.get("fake_pending_message_ids") or [])
        if message_id not in pending:
            pending.append(message_id)
        configuration["fake_pending_message_ids"] = pending[-100:]
        configuration["fake_history_id"] = payload["historyId"]
        connection.channel_connection.configuration = configuration
        connection.channel_connection.save(update_fields=["configuration", "updated_at"])
        if request.data.get("defer_sync") is True:
            return Response(
                {
                    "status": "seeded",
                    "imported": 0,
                    "message_id": message_id,
                    "history_id": payload["historyId"],
                },
                status=202,
            )
        from gmail_integration.services import incremental_sync

        run = incremental_sync(connection, target_history_id=payload["historyId"])
        return Response({"status": run.status, "imported": run.imported_count, "message_id": message_id}, status=202)


class GmailTestStateView(GmailTenantView):
    def post(self, request, connection_id):
        if not integration_readiness()["fake_provider"]:
            raise GmailError("test_provider_unavailable", status_code=404)
        connection = self.get_object(request, connection_id)
        fake_state = str(request.data.get("state") or "healthy")
        if fake_state not in {
            "healthy",
            "revoked",
            "permission_missing",
            "watch_expired",
            "degraded",
        }:
            raise GmailError("test_state_invalid")
        configuration = connection.channel_connection.configuration
        configuration["fake_health_state"] = fake_state
        connection.channel_connection.configuration = configuration
        connection.channel_connection.save(update_fields=["configuration", "updated_at"])
        if fake_state == "watch_expired":
            connection.watch_expiration_at = timezone.now() - timedelta(minutes=1)
            connection.connection_status = "watch_expired"
        else:
            connection.connection_status = {
                "healthy": "connected",
                "revoked": "revoked",
                "permission_missing": "permission_missing",
                "degraded": "degraded",
            }[fake_state]
        connection.save(
            update_fields=["watch_expiration_at", "connection_status", "updated_at"]
        )
        return Response(
            GmailConnectionSerializer(connection, context={"request": request}).data
        )


class GmailAttachmentView(GmailTenantView):
    def get(self, request, record_id, index):
        metadata, content = fetch_attachment(
            organization=request.organization, record_id=record_id, index=index
        )
        response = HttpResponse(
            content,
            content_type=str(metadata.get("mime_type") or "application/octet-stream")[:120],
        )
        safe_name = re.sub(r"[^A-Za-z0-9_. -]", "_", str(metadata.get("filename") or "attachment"))[:255]
        response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response
