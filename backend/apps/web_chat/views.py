from __future__ import annotations

import json

from django.conf import settings
from django.db.models import Count, Q
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.pagination import StandardPagination
from crm.services import record_activity
from organizations.permissions import HasOrganizationRole, IsOrganizationMember, OrganizationContextMixin
from web_chat.models import InstallationStatus, WebChatInstallation, WebChatSession
from web_chat.serializers import (
    WebChatInstallationSerializer,
    WebChatSessionStaffSerializer,
    activate_installation,
    rotate_public_key,
)
from web_chat.services import (
    WebChatError,
    anonymize_session,
    authenticate_session,
    close_session,
    create_origin_proof,
    create_session,
    ingest_public_message,
    installation_for_public_key,
    mark_read,
    metric,
    normalize_origin,
    publish_event,
    request_handoff,
    request_origin,
    rotate_session_token,
    serialize_event,
    serialize_message,
    update_identity,
)


def installation_context(request):
    return {
        "request": request,
        "widget_base": settings.WEB_CHAT_WIDGET_BASE_URL,
        "public_api_enabled": settings.WEB_CHAT_ENABLE_PUBLIC and not settings.WEB_CHAT_GLOBAL_KILL_SWITCH,
    }


class InstallationBaseView(OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = "manage_channels"

    def get_object(self, request, installation_id):
        return get_object_or_404(
            WebChatInstallation.objects.for_organization(request.organization).select_related(
                "organization", "channel_connection", "default_branch"
            ),
            pk=installation_id,
        )


class InstallationListCreateView(InstallationBaseView):
    def get(self, request):
        rows = WebChatInstallation.objects.for_organization(request.organization).select_related(
            "organization", "channel_connection", "default_branch"
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            WebChatInstallationSerializer(page, many=True, context=installation_context(request)).data
        )

    def post(self, request):
        serializer = WebChatInstallationSerializer(data=request.data, context=installation_context(request))
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(
            WebChatInstallationSerializer(instance, context=installation_context(request)).data,
            status=status.HTTP_201_CREATED,
        )


class InstallationDetailView(InstallationBaseView):
    def get(self, request, installation_id):
        return Response(WebChatInstallationSerializer(
            self.get_object(request, installation_id), context=installation_context(request)
        ).data)

    def patch(self, request, installation_id):
        instance = self.get_object(request, installation_id)
        serializer = WebChatInstallationSerializer(
            instance, data=request.data, partial=True, context=installation_context(request)
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class InstallationActionView(InstallationBaseView):
    action = ""

    def post(self, request, installation_id):
        installation = self.get_object(request, installation_id)
        actor = request.organization_membership
        if self.action == "activate":
            activate_installation(installation, actor)
        elif self.action == "pause":
            installation.status = InstallationStatus.PAUSED
            installation.updated_by = actor
            installation.save(update_fields=["status", "updated_by", "updated_at"])
            installation.sync_connection_status()
        elif self.action == "revoke":
            installation.status = InstallationStatus.REVOKED
            installation.updated_by = actor
            installation.save(update_fields=["status", "updated_by", "updated_at"])
            installation.sync_connection_status()
            installation.sessions.filter(status__in=["active", "handed_off"]).update(status="blocked")
        elif self.action == "rotate":
            rotate_public_key(installation, actor)
        record_activity(
            organization=installation.organization,
            actor_membership=actor,
            event_type=f"web_chat.installation_{self.action}",
            summary=f"Web Chat installation action: {self.action}",
            metadata={"installation_id": str(installation.id)},
        )
        return Response(WebChatInstallationSerializer(
            installation, context=installation_context(request)
        ).data)


class ActivateInstallationView(InstallationActionView):
    action = "activate"


class PauseInstallationView(InstallationActionView):
    action = "pause"


class RevokeInstallationView(InstallationActionView):
    action = "revoke"


class RotateInstallationKeyView(InstallationActionView):
    action = "rotate"


class InstallationSessionsView(InstallationBaseView):
    def get(self, request, installation_id):
        installation = self.get_object(request, installation_id)
        rows = installation.sessions.select_related("conversation", "contact")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(WebChatSessionStaffSerializer(page, many=True).data)


class InstallationMetricsView(InstallationBaseView):
    def get(self, request, installation_id):
        installation = self.get_object(request, installation_id)
        rows = installation.metrics.values("event_type").annotate(count=Count("id")).order_by("event_type")
        return Response({"events": {item["event_type"]: item["count"] for item in rows}})


class StaffAnonymizeSessionView(InstallationBaseView):
    def post(self, request, installation_id, public_session_id):
        installation = self.get_object(request, installation_id)
        session = get_object_or_404(installation.sessions, public_session_id=public_session_id)
        anonymize_session(session, request.organization_membership)
        return Response(WebChatSessionStaffSerializer(session).data)


class PublicCorsMixin:
    permission_classes = [AllowAny]
    authentication_classes = []

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        origin = getattr(request, "web_chat_cors_origin", "")
        if origin:
            response["Access-Control-Allow-Origin"] = origin
            response["Vary"] = "Origin"
            response["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Idempotency-Key, Last-Event-ID"
            response["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
        response["Cache-Control"] = "no-store"
        return response

    def options(self, request, *args, **kwargs):
        try:
            request.web_chat_cors_origin = request_origin(request)
        except WebChatError:
            return Response(status=403)
        return Response(status=204)

    def handle_exception(self, exc):
        if isinstance(exc, WebChatError):
            return Response({"error": {"code": exc.code}}, status=exc.status_code)
        return super().handle_exception(exc)


class PublicConfigView(PublicCorsMixin, APIView):
    def get(self, request, public_key):
        installation = installation_for_public_key(public_key)
        origin = request_origin(request)
        proof = create_origin_proof(installation, origin)
        request.web_chat_cors_origin = origin
        metric(installation, "widget_load")
        return Response({
            "version": "v1",
            "installation_key": installation.public_key,
            "display_name": installation.display_name,
            "assistant_label": installation.assistant_label,
            "greeting": installation.greeting,
            "offline_message": installation.offline_message,
            "human_handoff_message": installation.human_handoff_message,
            "privacy_policy_url": installation.privacy_policy_url,
            "terms_url": installation.terms_url,
            "consent_text": installation.consent_text,
            "consent_version": installation.consent_version,
            "require_consent": installation.require_consent,
            "require_prechat_form": installation.require_prechat_form,
            "collect_name": installation.collect_name,
            "collect_email": installation.collect_email,
            "collect_phone": installation.collect_phone,
            "default_language": installation.default_language,
            "supported_languages": installation.supported_languages,
            "theme": installation.theme_config,
            "origin_proof": proof,
            "attachments_supported": False,
        })


class PublicCreateSessionView(PublicCorsMixin, APIView):
    def post(self, request, public_key):
        installation = installation_for_public_key(public_key)
        session, token = create_session(
            installation=installation,
            origin_proof=str(request.data.get("origin_proof", "")),
            consent_accepted=bool(request.data.get("consent_accepted", False)),
            language=str(request.data.get("language", installation.default_language)),
            request=request,
        )
        request.web_chat_cors_origin = request_origin(request)
        return Response({
            "session_id": str(session.public_session_id),
            "session_token": token,
            "expires_at": session.expires_at,
            "status": session.status,
        }, status=201)


def bearer_token(request):
    value = request.headers.get("Authorization", "")
    prefix, _, token = value.partition(" ")
    return token if prefix.casefold() == "bearer" else ""


class PublicSessionView(PublicCorsMixin, APIView):
    allow_closed = False

    def session(self, request, public_session_id):
        return authenticate_session(
            public_session_id=public_session_id,
            token=bearer_token(request),
            request=request,
            allow_closed=self.allow_closed,
        )


class PublicMessagesView(PublicSessionView):
    def get(self, request, public_session_id):
        session = self.session(request, public_session_id)
        after = max(0, int(request.query_params.get("after", "0") or 0))
        events = list(session.events.filter(sequence__gt=after).select_related("message").order_by("sequence")[:100])
        return Response({"events": [serialize_event(event) for event in events], "cursor": events[-1].sequence if events else after})

    def post(self, request, public_session_id):
        session = self.session(request, public_session_id)
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        message, created = ingest_public_message(
            session=session,
            body=str(request.data.get("body", "")),
            client_message_id=idempotency_key,
        )
        return Response({"message": serialize_message(message), "created": created}, status=201 if created else 200)


class PublicEventsView(PublicSessionView):
    def get(self, request, public_session_id):
        session = self.session(request, public_session_id)
        raw = request.headers.get("Last-Event-ID") or request.query_params.get("after", "0")
        try:
            after = max(0, int(raw or 0))
        except ValueError:
            after = 0
        events = list(session.events.filter(sequence__gt=after).select_related("message").order_by("sequence")[:100])

        def stream():
            for event in events:
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(serialize_event(event), ensure_ascii=False, separators=(',', ':'))}\n\n"
            yield ": heartbeat\n\n"

        response = StreamingHttpResponse(stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache, no-store"
        response["X-Accel-Buffering"] = "no"
        response["Access-Control-Allow-Origin"] = request.web_chat_cors_origin
        response["Vary"] = "Origin"
        return response


class PublicIdentityView(PublicSessionView):
    def patch(self, request, public_session_id):
        session = self.session(request, public_session_id)
        update_identity(
            session=session,
            name=str(request.data.get("name", "")),
            email=str(request.data.get("email", "")),
            phone=str(request.data.get("phone", "")),
        )
        return Response({"status": "updated"})


class PublicHandoffView(PublicSessionView):
    def post(self, request, public_session_id):
        session = self.session(request, public_session_id)
        request_handoff(session)
        return Response({"status": "requested"})


class PublicReadView(PublicSessionView):
    def post(self, request, public_session_id):
        session = self.session(request, public_session_id)
        mark_read(session, str(request.data.get("message_id", "")))
        return Response({"status": "recorded"})


class PublicCloseView(PublicSessionView):
    allow_closed = True

    def post(self, request, public_session_id):
        session = self.session(request, public_session_id)
        close_session(session)
        return Response({"status": "closed"})


class PublicResumeView(PublicSessionView):
    def post(self, request, public_session_id):
        session = self.session(request, public_session_id)
        token = rotate_session_token(session)
        session.refresh_from_db(fields=["expires_at"])
        return Response({"session_token": token, "expires_at": session.expires_at})
