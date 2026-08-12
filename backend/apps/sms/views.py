from __future__ import annotations

import json
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
from crm.models import Contact, ContactIdentity, ContactIdentityType
from organizations.permissions import HasOrganizationRole, IsOrganizationMember, OrganizationContextMixin
from sms.models import SMSAuditEvent, SMSConnection, SMSProviderType, SMSWebhookEnvelope
from sms.parser import normalize_phone
from sms.serializers import SMSConnectionCreateSerializer, SMSConnectionSerializer
from sms.services import (
    SMSError,
    connection_health,
    create_connection,
    integration_readiness,
    privacy_erase,
    privacy_export,
    receive_verified_inbound,
    receive_verified_status,
    request_outbound_retry,
    resolve_webhook_candidate,
    rotate_credentials,
    set_connection_state,
    update_connection,
    update_consent_by_employee,
    webhook_auth_token,
)
from sms.verifier import SMSWebhookVerifier


logger = logging.getLogger("security.audit")


class SMSErrorMixin:
    def handle_exception(self, exc):
        if isinstance(exc, SMSError):
            payload = {"error": {"code": exc.code}}
            if exc.details:
                payload["error"]["details"] = exc.details
            return Response(payload, status=exc.status_code)
        return super().handle_exception(exc)


class SMSTenantView(SMSErrorMixin, OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = "manage_channels"

    def initial(self, request, *args, **kwargs):
        # OrganizationContextMixin resolves membership before DRF permissions.
        # Let IsAuthenticated handle anonymous requests first so an organization
        # header can never turn an unauthenticated request into a UUID lookup/500.
        if not request.user.is_authenticated:
            return APIView.initial(self, request, *args, **kwargs)
        return super().initial(request, *args, **kwargs)

    def get_object(self, request, connection_id):
        return get_object_or_404(
            SMSConnection.objects.for_organization(request.organization).select_related(
                "channel_connection", "organization", "connected_by__user"
            ),
            pk=connection_id,
        )


class SMSReadinessView(SMSTenantView):
    def get(self, request):
        return Response(integration_readiness())


class SMSConnectionListView(SMSTenantView):
    def get(self, request):
        rows = SMSConnection.objects.for_organization(request.organization).select_related("channel_connection")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(SMSConnectionSerializer(page, many=True).data)

    def post(self, request):
        serializer = SMSConnectionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = create_connection(
            organization=request.organization,
            membership=request.organization_membership,
            data=serializer.validated_data,
        )
        return Response(SMSConnectionSerializer(connection).data, status=201)


class SMSConnectionDetailView(SMSTenantView):
    def get(self, request, connection_id):
        return Response(SMSConnectionSerializer(self.get_object(request, connection_id)).data)

    def patch(self, request, connection_id):
        connection = update_connection(
            self.get_object(request, connection_id),
            membership=request.organization_membership,
            data=request.data,
        )
        return Response(SMSConnectionSerializer(connection).data)


class SMSConnectionHealthView(SMSTenantView):
    def get(self, request, connection_id):
        return Response(connection_health(self.get_object(request, connection_id)))

    def post(self, request, connection_id):
        return Response(connection_health(self.get_object(request, connection_id), run_provider=True))


class SMSRotateCredentialsView(SMSTenantView):
    def post(self, request, connection_id):
        connection = rotate_credentials(
            self.get_object(request, connection_id),
            membership=request.organization_membership,
            data=request.data,
        )
        return Response(SMSConnectionSerializer(connection).data)


class SMSConnectionActionView(SMSTenantView):
    def post(self, request, connection_id, action):
        connection = set_connection_state(
            self.get_object(request, connection_id),
            membership=request.organization_membership,
            action=action,
        )
        return Response(SMSConnectionSerializer(connection).data)


class SMSTestView(SMSTenantView):
    def post(self, request, connection_id):
        connection = self.get_object(request, connection_id)
        if connection.provider != SMSProviderType.FAKE:
            raise SMSError("test_provider_unavailable", status_code=404)
        from sms.services import process_inbound_envelope
        from sms.parser import inbound_event_key

        sender = normalize_phone(str(request.data.get("from") or "+15550108888"))
        sid_seed = str(request.data.get("message_sid") or f"SMTEST{int(timezone.now().timestamp() * 1000000)}")
        sid = sid_seed if sid_seed.startswith("SM") else f"SM{sid_seed}"
        envelope, created = SMSWebhookEnvelope.objects.get_or_create(
            organization=connection.organization,
            connection=connection,
            event_key=inbound_event_key(sid),
            defaults={
                "provider_message_sid": sid[:64],
                "event_type": "inbound",
                "from_address": sender,
                "to_address": connection.sender_address,
                "body": str(request.data.get("body") or "Hello from deterministic fake SMS")[:10000],
                "opt_out_type": str(request.data.get("opt_out_type") or "")[:16].upper(),
            },
        )
        process_inbound_envelope(envelope.id)
        return Response({"status": "accepted", "created": created, "envelope_id": str(envelope.id)}, status=202)


class SMSRetryOutboundView(SMSTenantView):
    def post(self, request, connection_id):
        attempt = request_outbound_retry(
            connection=self.get_object(request, connection_id),
            message_id=request.data.get("message_id"),
            membership=request.organization_membership,
        )
        return Response(
            {"status": "accepted", "attempt_id": str(attempt.id), "message_id": str(attempt.message_id)},
            status=202,
        )


class SMSPrivacyView(SMSTenantView):
    def _contact(self, request):
        return get_object_or_404(
            Contact.objects.for_organization(request.organization), pk=request.data.get("contact_id") or request.query_params.get("contact_id")
        )

    def get(self, request, connection_id):
        return Response(privacy_export(connection=self.get_object(request, connection_id), contact=self._contact(request)))

    def post(self, request, connection_id):
        if request.data.get("confirm") is not True:
            raise SMSError("confirmation_required")
        return Response(privacy_erase(
            connection=self.get_object(request, connection_id),
            contact=self._contact(request),
            membership=request.organization_membership,
            mode=str(request.data.get("mode") or "anonymize"),
        ))


class SMSConsentView(SMSTenantView):
    write_action = "operate"

    def post(self, request, connection_id):
        connection = self.get_object(request, connection_id)
        identity = get_object_or_404(
            ContactIdentity.objects.for_organization(request.organization),
            contact_id=request.data.get("contact_id"),
            channel_connection=connection.channel_connection,
            type=ContactIdentityType.PHONE,
        )
        consent = update_consent_by_employee(
            connection=connection,
            contact_identity=identity,
            membership=request.organization_membership,
            state=str(request.data.get("state") or ""),
        )
        return Response({"state": consent.state, "source": consent.source, "updated_at": consent.updated_at})


class TwilioSMSWebhookView(SMSErrorMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "sms_webhook"
    event_type = "inbound"

    def post(self, request, public_key):
        if len(request.body) > settings.SMS_MAX_WEBHOOK_BYTES:
            raise SMSError("payload_too_large", status_code=413)
        connection = resolve_webhook_candidate(public_key)
        result = SMSWebhookVerifier().verify(request=request, auth_token=webhook_auth_token(connection))
        if not result.valid:
            SMSAuditEvent.objects.create(
                organization=connection.organization,
                connection=connection,
                event_type="sms.webhook_signature_rejected",
                metadata={"event_type": self.event_type, "reason": result.reason},
            )
            logger.warning("SMS webhook signature rejected connection=%s event=%s", connection.id, self.event_type)
            raise SMSError("invalid_signature", status_code=403)
        content_type = str(request.content_type or "").split(";", 1)[0].lower()
        params = json.loads(request.body) if content_type == "application/json" else request.POST
        receiver = receive_verified_inbound if self.event_type == "inbound" else receive_verified_status
        envelope, created = receiver(connection=connection, params=params)
        return Response(
            {"status": "accepted", "duplicate": not created, "receipt_id": str(envelope.id)},
            status=status.HTTP_202_ACCEPTED,
        )


class TwilioSMSStatusView(TwilioSMSWebhookView):
    event_type = "status"
