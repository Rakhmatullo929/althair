from __future__ import annotations

import logging

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
from voice.controller import VoiceRealtimeController
from voice.models import VoiceAuditEvent, VoiceCall, VoiceConnection, VoiceTransferDestination
from voice.providers import FakeRealtimeVoiceProvider, OpenAIRealtimeSIPProvider, VoiceProviderError
from voice.serializers import (
    VoiceCallSerializer,
    VoiceConnectionCreateSerializer,
    VoiceConnectionSerializer,
    VoiceTransferDestinationSerializer,
)
from voice.services import (
    VoiceError,
    accept_or_reject_routed_call,
    connection_health,
    create_connection,
    create_transfer_destination,
    human_takeover,
    integration_readiness,
    finalize_call,
    parse_incoming_event,
    receive_carrier_status,
    rotate_credentials,
    route_verified_incoming_call,
    set_connection_state,
    update_connection,
    update_transfer_destination,
    webhook_auth_token,
)
from voice.verifiers import OpenAIIncomingCallVerifier, TwilioVoiceWebhookVerifier, VoiceSignatureError


logger = logging.getLogger("security.audit")


class VoiceErrorMixin:
    def handle_exception(self, exc):
        if isinstance(exc, VoiceError):
            payload = {"error": {"code": exc.code}}
            if exc.details:
                payload["error"]["details"] = exc.details
            return Response(payload, status=exc.status_code)
        return super().handle_exception(exc)


class VoiceTenantView(VoiceErrorMixin, OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = "manage_channels"

    def initial(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return APIView.initial(self, request, *args, **kwargs)
        return super().initial(request, *args, **kwargs)

    def get_connection(self, request, connection_id):
        return get_object_or_404(
            VoiceConnection.objects.for_organization(request.organization).select_related(
                "channel_connection", "organization", "connected_by__user"
            ).prefetch_related("transfer_destinations"),
            pk=connection_id,
        )


class VoiceReadinessView(VoiceTenantView):
    def get(self, request):
        return Response(integration_readiness())


class VoiceConnectionListView(VoiceTenantView):
    def get(self, request):
        rows = VoiceConnection.objects.for_organization(request.organization).select_related(
            "channel_connection", "organization"
        ).prefetch_related("transfer_destinations")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(VoiceConnectionSerializer(page, many=True).data)

    def post(self, request):
        serializer = VoiceConnectionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = create_connection(
            organization=request.organization,
            membership=request.organization_membership,
            data=serializer.validated_data,
        )
        return Response(VoiceConnectionSerializer(connection).data, status=201)


class VoiceConnectionDetailView(VoiceTenantView):
    def get(self, request, connection_id):
        return Response(VoiceConnectionSerializer(self.get_connection(request, connection_id)).data)

    def patch(self, request, connection_id):
        connection = update_connection(
            self.get_connection(request, connection_id),
            membership=request.organization_membership,
            data=request.data,
        )
        return Response(VoiceConnectionSerializer(connection).data)


class VoiceConnectionHealthView(VoiceTenantView):
    def get(self, request, connection_id):
        return Response(connection_health(self.get_connection(request, connection_id)))

    def post(self, request, connection_id):
        return Response(connection_health(self.get_connection(request, connection_id), run_provider=True))


class VoiceRotateCredentialsView(VoiceTenantView):
    def post(self, request, connection_id):
        connection = rotate_credentials(
            self.get_connection(request, connection_id), membership=request.organization_membership, data=request.data
        )
        return Response(VoiceConnectionSerializer(connection).data)


class VoiceConnectionActionView(VoiceTenantView):
    def post(self, request, connection_id, action):
        connection = set_connection_state(
            self.get_connection(request, connection_id), membership=request.organization_membership, action=action
        )
        return Response(VoiceConnectionSerializer(connection).data)


class VoiceTransferListView(VoiceTenantView):
    def get(self, request, connection_id):
        connection = self.get_connection(request, connection_id)
        return Response(VoiceTransferDestinationSerializer(connection.transfer_destinations.all(), many=True).data)

    def post(self, request, connection_id):
        connection = self.get_connection(request, connection_id)
        destination = create_transfer_destination(
            connection=connection, membership=request.organization_membership, data=request.data
        )
        return Response(VoiceTransferDestinationSerializer(destination).data, status=201)


class VoiceTransferDetailView(VoiceTenantView):
    def get_object(self, request, connection_id, destination_id):
        return get_object_or_404(
            VoiceTransferDestination.objects.for_organization(request.organization).select_related("voice_connection"),
            pk=destination_id,
            voice_connection_id=connection_id,
        )

    def patch(self, request, connection_id, destination_id):
        destination = update_transfer_destination(
            self.get_object(request, connection_id, destination_id),
            membership=request.organization_membership,
            data=request.data,
        )
        return Response(VoiceTransferDestinationSerializer(destination).data)

    def delete(self, request, connection_id, destination_id):
        destination = self.get_object(request, connection_id, destination_id)
        destination.active = False
        destination.save(update_fields=["active", "updated_at"])
        return Response(status=204)


class VoiceCallListView(VoiceTenantView):
    write_action = "operate"

    def get(self, request):
        rows = VoiceCall.objects.for_organization(request.organization).select_related(
            "voice_connection", "conversation", "contact"
        ).prefetch_related("transcript_segments", "tool_calls", "transfer_attempts__destination")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(VoiceCallSerializer(page, many=True).data)


class VoiceCallDetailView(VoiceTenantView):
    write_action = "operate"

    def get_object(self, request, call_id):
        return get_object_or_404(
            VoiceCall.objects.for_organization(request.organization).select_related(
                "voice_connection", "conversation", "contact"
            ).prefetch_related("transcript_segments", "tool_calls", "transfer_attempts__destination"),
            pk=call_id,
        )

    def get(self, request, call_id):
        return Response(VoiceCallSerializer(self.get_object(request, call_id)).data)


class VoiceHumanTakeoverView(VoiceCallDetailView):
    def post(self, request, call_id):
        call = human_takeover(self.get_object(request, call_id), membership=request.organization_membership)
        return Response(VoiceCallSerializer(call).data)


class VoiceFakeTestCallView(VoiceTenantView):
    def post(self, request, connection_id):
        connection = self.get_connection(request, connection_id)
        if connection.carrier != "fake":
            raise VoiceError("test_provider_unavailable", status_code=404)
        nonce = str(request.data.get("call_id") or f"fake-call-{int(timezone.now().timestamp() * 1000000)}")
        caller = str(request.data.get("caller") or "+15550108888")
        event = {
            "id": f"evt-{nonce}",
            "type": "realtime.call.incoming",
            "data": {
                "call_id": nonce,
                "sip_headers": [
                    {"name": "From", "value": f"sip:{caller}@fake.invalid"},
                    {"name": "To", "value": f"sip:{connection.phone_number_e164}@fake.invalid"},
                    {"name": "Call-ID", "value": f"carrier-{nonce}"},
                ],
            },
        }
        result = route_verified_incoming_call(event)
        accept_or_reject_routed_call(result)
        events = request.data.get("events")
        if not isinstance(events, list):
            language = str(request.data.get("language") or connection.default_language)
            events = [
                {"type": "voice.language", "language": language},
                {"type": "voice.assistant_transcript.final", "transcript": "AI disclosure and greeting delivered.", "language": language},
                {"type": "voice.caller_transcript.final", "transcript": str(request.data.get("utterance") or "I need help from your team."), "language": language},
                {"type": "voice.completed", "outcome": "answered"},
            ]
        import asyncio

        try:
            asyncio.run(VoiceRealtimeController(call_id=result.call.id, events=events).run())
        except TimeoutError:
            finalize_call(result.call, outcome="failed", hangup_actor="system", error="max_duration")
        except Exception as exc:
            # The deterministic endpoint mirrors worker failure finalization while
            # returning only a stable category, never raw provider payloads.
            error = exc.code if isinstance(exc, VoiceProviderError) else "controller_failed"
            finalize_call(result.call, outcome="failed", hangup_actor="provider", error=error)
        call = VoiceCall.objects.get(pk=result.call.pk)
        return Response(VoiceCallSerializer(call).data, status=202)


class OpenAIRealtimeIncomingCallView(VoiceErrorMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "voice_webhook"

    def post(self, request):
        if len(request.body) > settings.VOICE_MAX_WEBHOOK_BYTES:
            raise VoiceError("payload_too_large", status_code=413)
        try:
            event = OpenAIIncomingCallVerifier().unwrap(raw_body=request.body, headers=request.headers)
        except VoiceSignatureError as exc:
            logger.warning("OpenAI Voice webhook signature rejected")
            return Response({"error": {"code": str(exc)}}, status=403)
        parsed = parse_incoming_event(event)
        try:
            result = route_verified_incoming_call(event)
            accept_or_reject_routed_call(result)
        except VoiceError as exc:
            provider = FakeRealtimeVoiceProvider() if settings.VOICE_REALTIME_PROVIDER == "fake" else OpenAIRealtimeSIPProvider()
            provider.reject(call_id=parsed["call_id"], status_code=603)
            return Response({"status": "rejected", "reason": exc.code}, status=202)
        return Response(
            {"status": "accepted" if result.accepted else "rejected", "duplicate": not result.created, "call_id": str(result.call.id)},
            status=202,
        )


class TwilioVoiceStatusView(VoiceErrorMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "voice_webhook"

    def post(self, request, public_key):
        connection = get_object_or_404(
            VoiceConnection.objects.select_related("organization"), webhook_public_key=public_key
        )
        result = TwilioVoiceWebhookVerifier().verify(request=request, auth_token=webhook_auth_token(connection))
        if not result.valid:
            VoiceAuditEvent.objects.create(
                organization=connection.organization, connection=connection,
                event_type="voice.carrier_signature_rejected", metadata={"reason": result.reason},
            )
            logger.warning("Twilio Voice webhook signature rejected connection=%s", connection.id)
            return Response({"error": {"code": "invalid_signature"}}, status=403)
        event, created = receive_carrier_status(connection=connection, params=request.POST)
        return Response({"status": "accepted", "duplicate": not created, "event_id": str(event.id)}, status=202)
