from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import timedelta
from urllib.parse import urlencode
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase
from twilio.request_validator import RequestValidator

from ai_runtime.models import AIToolPolicy, ToolExecutionMode
from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.models import ContactIdentity, Conversation, CrmActivity, FollowUpTask, Lead
from organizations.models import OrganizationMembership, OrganizationStatus
from organizations.services import create_organization
from voice.context import VoiceSessionBuilder, disclosure_text, latest_published_context, voice_tools_for
from voice.controller import VoiceRealtimeController, claim_next_job, release_job
from voice.models import (
    VoiceAuditEvent,
    VoiceCall,
    VoiceCallStatus,
    VoiceCarrierStatusEvent,
    VoiceConnection,
    VoiceConnectionStatus,
    VoiceControllerJob,
    VoiceToolCall,
    VoiceTranscriptSegment,
    VoiceTransferAttempt,
    VoiceTransferDestination,
    VoiceUsageEvent,
    VoiceWebhookEnvelope,
)
from voice.providers import (
    FakeRealtimeVoiceProvider,
    OpenAIRealtimeSIPProvider,
    TwilioSIPCarrierProvider,
    VoiceProviderError,
    carrier_provider_for,
    realtime_provider_for,
)
from voice.services import (
    VoiceError,
    accept_or_reject_routed_call,
    connection_health,
    create_connection,
    create_transfer_destination,
    execute_voice_tool,
    finalize_call,
    human_takeover,
    integration_readiness,
    parse_incoming_event,
    privacy_delete_expired_transcripts,
    receive_carrier_status,
    record_transcript_consent,
    request_voice_transfer,
    route_verified_incoming_call,
    set_connection_state,
    sip_headers,
    store_final_transcript,
)


User = get_user_model()


@override_settings(
    DEBUG=True,
    SECURE_SSL_REDIRECT=False,
    VOICE_ENABLE_LIVE=False,
    VOICE_FAKE_PROVIDER=True,
    VOICE_CARRIER_PROVIDER="fake",
    VOICE_REALTIME_PROVIDER="fake",
    VOICE_GLOBAL_KILL_SWITCH=False,
    VOICE_FAKE_WEBHOOK_SECRET="test-only-voice-webhook-secret",
    TWILIO_VOICE_PUBLIC_BASE_URL="https://api.example.test",
    TWILIO_VOICE_AUTH_TOKEN="test-only-twilio-voice-token",
    OPENAI_REALTIME_MODEL="configured-realtime-model",
    OPENAI_REALTIME_VOICE="marin",
    FIELD_ENCRYPTION_KEY="j64ChG14GGzpCY_wJAkkVx1fb0V3w_CVQvc--vvSeI8=",
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class VoiceTelephonyTests(APITransactionTestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username="voice-owner", email="voice-owner@example.test", password="pw12345!"
        )
        self.organization = create_organization(creator=self.owner, name="Voice Clinic", slug="voice-clinic")
        self.membership = OrganizationMembership.objects.get(organization=self.organization, user=self.owner)
        self.other_user = User.objects.create_user(
            username="voice-other", email="voice-other@example.test", password="pw12345!"
        )
        self.other = create_organization(creator=self.other_user, name="Other Voice", slug="other-voice")
        self.other_membership = OrganizationMembership.objects.get(organization=self.other, user=self.other_user)
        self.client.force_authenticate(self.owner)
        self.connection = create_connection(
            organization=self.organization,
            membership=self.membership,
            data={"carrier": "fake", "phone_number_e164": "+15550101111", "display_name": "Main Voice"},
        )

    def tenant(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.organization).id)}

    def incoming(self, *, event_id="evt-voice-1", call_id="call-voice-1", called=None, caller="+15550102222", headers=None):
        return {
            "id": event_id,
            "type": "realtime.call.incoming",
            "data": {
                "call_id": call_id,
                "sip_headers": headers or [
                    {"name": "fRoM", "value": f"sip:{caller}@carrier.example"},
                    {"name": "TO", "value": f"sip:{called or self.connection.phone_number_e164}@carrier.example"},
                    {"name": "Call-ID", "value": f"sip-{call_id}"},
                ],
            },
        }

    def signed_openai_post(self, event, signature=None):
        body = json.dumps(event, separators=(",", ":")).encode()
        signature = signature or hmac.new(
            b"test-only-voice-webhook-secret", body, hashlib.sha256
        ).hexdigest()
        return self.client.post(
            "/api/v1/webhooks/openai/realtime-calls/",
            body,
            content_type="application/json",
            HTTP_X_VOICE_FAKE_SIGNATURE=signature,
        )

    def create_call(self, **overrides):
        event = self.incoming(**{key: value for key, value in overrides.items() if key in {"event_id", "call_id", "called", "caller", "headers"}})
        result = route_verified_incoming_call(event)
        accept_or_reject_routed_call(result)
        return VoiceCall.objects.get(pk=result.call.pk)

    def test_connection_api_write_only_credentials_health_and_actions(self):
        response = self.client.get("/api/v1/integrations/voice/connections/", **self.tenant())
        self.assertEqual(response.status_code, 200)
        row = response.data["results"][0]
        self.assertNotIn("carrier_auth_token", row)
        self.assertEqual(row["recording_mode"], "disabled")
        self.assertTrue(row["health"]["realtime_ready"])
        paused = self.client.post(f"/api/v1/integrations/voice/{self.connection.id}/pause/", {}, format="json", **self.tenant())
        self.assertEqual(paused.data["status"], "paused")
        active = self.client.post(f"/api/v1/integrations/voice/{self.connection.id}/activate/", {}, format="json", **self.tenant())
        self.assertEqual(active.data["status"], "connected")

    def test_connection_organization_immutable_and_recording_cannot_enable(self):
        self.connection.organization = self.other
        with self.assertRaises(ValidationError):
            self.connection.save()
        self.connection.organization = self.organization
        self.connection.recording_mode = "enabled"
        with self.assertRaises(ValidationError):
            self.connection.full_clean()

    def test_transfer_destination_is_encrypted_write_only_and_tenant_scoped(self):
        response = self.client.post(
            f"/api/v1/integrations/voice/{self.connection.id}/transfers/",
            {"key": "front-desk", "display_name": "Front desk", "destination_type": "phone", "destination": "+15550103333"},
            format="json", **self.tenant(),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["has_destination"])
        self.assertNotIn("destination", response.data)
        destination = VoiceTransferDestination.objects.get()
        with self.assertRaises(VoiceTransferDestination.DoesNotExist):
            VoiceTransferDestination.objects.for_organization(self.other).get(pk=destination.pk)

    def test_sip_header_parsing_case_insensitive_and_called_number_routes_tenant(self):
        parsed = parse_incoming_event(self.incoming())
        self.assertEqual(parsed["called"], self.connection.phone_number_e164)
        self.assertEqual(parsed["caller"], "+15550102222")
        result = route_verified_incoming_call(self.incoming())
        self.assertEqual(result.call.organization, self.organization)
        self.assertEqual(result.call.called_e164, self.connection.phone_number_e164)
        self.assertEqual(ContactIdentity.objects.get().organization, self.organization)

    def test_signed_openai_webhook_accepts_and_duplicate_is_idempotent(self):
        first = self.signed_openai_post(self.incoming())
        second = self.signed_openai_post(self.incoming())
        self.assertEqual(first.status_code, 202, first.data)
        self.assertEqual(second.status_code, 202, second.data)
        self.assertFalse(first.data["duplicate"])
        self.assertTrue(second.data["duplicate"])
        self.assertEqual(VoiceCall.objects.count(), 1)
        self.assertEqual(VoiceWebhookEnvelope.objects.count(), 1)
        self.assertEqual(VoiceControllerJob.objects.count(), 1)

    def test_missing_or_invalid_openai_signature_has_no_side_effect(self):
        rejected = self.signed_openai_post(self.incoming(), signature="invalid")
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(VoiceCall.objects.count(), 0)
        missing = self.client.post(
            "/api/v1/webhooks/openai/realtime-calls/", self.incoming(), format="json"
        )
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(VoiceCall.objects.count(), 0)

    def test_unknown_called_number_and_suspended_tenant_fail_closed(self):
        unknown = self.signed_openai_post(self.incoming(called="+15550999999", call_id="unknown-call"))
        self.assertEqual(unknown.status_code, 202)
        self.assertEqual(unknown.data["status"], "rejected")
        self.assertEqual(VoiceCall.objects.count(), 0)
        self.organization.status = OrganizationStatus.SUSPENDED
        self.organization.save(update_fields=["status"])
        suspended = self.signed_openai_post(self.incoming(event_id="evt-suspended", call_id="suspended-call"))
        self.assertEqual(suspended.data["status"], "rejected")
        call = VoiceCall.objects.get(provider_call_id="suspended-call")
        self.assertEqual(call.rejection_reason, "organization_read_only")

    def test_global_kill_switch_and_concurrent_limit_reject(self):
        with override_settings(VOICE_GLOBAL_KILL_SWITCH=True):
            result = route_verified_incoming_call(self.incoming())
        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_reason, "global_kill_switch")
        self.connection.max_concurrent_calls = 1
        self.connection.save(update_fields=["max_concurrent_calls"])
        VoiceCall.objects.all().delete()
        VoiceWebhookEnvelope.objects.all().delete()
        VoiceControllerJob.objects.all().delete()
        self.create_call(event_id="evt-first", call_id="first-active")
        second = route_verified_incoming_call(self.incoming(event_id="evt-second", call_id="second-active"))
        self.assertFalse(second.accepted)
        self.assertEqual(second.rejection_reason, "concurrent_call_limit")

    def test_explicit_transcript_consent_and_final_only_storage(self):
        self.connection.disclosure_mode = "explicit_transcript_consent"
        self.connection.save(update_fields=["disclosure_mode"])
        call = self.create_call()
        self.assertEqual(call.consent_state, "pending")
        self.assertIsNone(store_final_transcript(call=call, speaker="caller", text="not persisted"))
        record_transcript_consent(call, granted=True)
        segment = store_final_transcript(call=call, speaker="caller", text="I consent", language="en")
        self.assertIsNotNone(segment)
        segment.final = False
        with self.assertRaises(ValidationError):
            segment.full_clean()

    def test_disabled_transcript_never_persists_and_disclosure_does_not_imply_recording(self):
        self.connection.transcript_retention_mode = "disabled"
        self.connection.disclosure_mode = "ai_and_transcript_disclosure"
        self.connection.save(update_fields=["transcript_retention_mode", "disclosure_mode"])
        call = self.create_call()
        record_transcript_consent(call, granted=True)
        self.assertIsNone(store_final_transcript(call=call, speaker="caller", text="ephemeral"))
        self.assertIn("not recorded", disclosure_text(self.connection))

    def test_fake_controller_lifecycle_interruption_language_usage_and_completion(self):
        call = self.create_call()
        events = [
            {"type": "voice.language", "language": "uz"},
            {"type": "input_audio_buffer.speech_started"},
            {"type": "voice.caller_transcript.final", "transcript": "Assalomu alaykum", "language": "uz"},
            {"type": "voice.assistant_transcript.final", "transcript": "Salom, yordam beraman.", "language": "uz"},
            {"type": "response.done", "response": {"usage": {"input_audio_tokens": 12, "output_audio_tokens": 8}}},
            {"type": "voice.completed", "outcome": "answered"},
        ]
        asyncio.run(VoiceRealtimeController(call_id=call.id, events=events).run())
        call.refresh_from_db()
        self.assertEqual(call.status, VoiceCallStatus.COMPLETED)
        self.assertEqual(call.selected_language, "uz")
        self.assertEqual(call.interruption_count, 1)
        self.assertEqual(call.transcript_segments.count(), 2)
        self.assertEqual(call.usage_event.input_audio_tokens, 12)

    def test_provider_disconnect_finalizes_safely(self):
        call = self.create_call()
        with self.assertRaises(VoiceProviderError):
            asyncio.run(VoiceRealtimeController(call_id=call.id, events=[{"type": "voice.provider_disconnect"}]).run())
        finalize_call(call, outcome="failed", hangup_actor="provider", error="provider_disconnect")
        call.refresh_from_db()
        self.assertEqual(call.status, VoiceCallStatus.FAILED)

    def test_voice_read_tools_and_mutations_require_confirmation_and_policy(self):
        call = self.create_call()
        output = execute_voice_tool(
            call=call, provider_call_id="tool-read", tool_name="get_company_profile", arguments={}
        )
        self.assertEqual(output["name"], "Voice Clinic")
        AIToolPolicy.objects.create(
            organization=self.organization, tool_name="create_lead", enabled=True,
            execution_mode=ToolExecutionMode.AUTOMATIC, configuration={"voice_allowed": True}, updated_by=self.membership,
        )
        with self.assertRaises(VoiceError):
            execute_voice_tool(
                call=call, provider_call_id="tool-lead-no-confirm", tool_name="create_lead",
                arguments={"title": "Consultation", "description": "Caller asked for details"},
            )
        result = execute_voice_tool(
            call=call, provider_call_id="tool-lead", tool_name="create_lead",
            arguments={"title": "Consultation", "description": "Caller asked for details"},
            confirmation_marker="segment:1",
        )
        repeated = execute_voice_tool(
            call=call, provider_call_id="tool-lead", tool_name="create_lead",
            arguments={"title": "Consultation", "description": "Caller asked for details"},
            confirmation_marker="segment:1",
        )
        self.assertEqual(result, repeated)
        self.assertEqual(Lead.objects.count(), 1)

    def test_arbitrary_tool_and_transfer_number_are_denied(self):
        call = self.create_call()
        with self.assertRaises(VoiceError):
            execute_voice_tool(call=call, provider_call_id="generic", tool_name="generic_http", arguments={})
        with self.assertRaises(VoiceError):
            request_voice_transfer(call=call, destination_key="+15550109999", idempotency_key="bad-transfer")
        self.assertEqual(VoiceTransferAttempt.objects.count(), 0)

    def test_configured_transfer_success_is_idempotent_and_hides_target(self):
        call = self.create_call()
        destination = create_transfer_destination(
            connection=self.connection, membership=self.membership,
            data={"key": "front-desk", "display_name": "Front desk", "destination_type": "phone", "destination": "+15550103333"},
        )
        first = request_voice_transfer(call=call, destination_key=destination.key, idempotency_key="refer-1")
        second = request_voice_transfer(call=call, destination_key=destination.key, idempotency_key="refer-1")
        self.assertEqual(first, second)
        call.refresh_from_db()
        self.assertEqual(call.status, VoiceCallStatus.TRANSFERRED)
        self.assertEqual(call.outcome, "transferred")
        self.assertEqual(VoiceTransferAttempt.objects.count(), 1)

    def test_failed_transfer_creates_truthful_callback(self):
        call = self.create_call()
        destination = create_transfer_destination(
            connection=self.connection, membership=self.membership,
            data={"key": "fail-desk", "display_name": "Fallback", "destination_type": "sip", "destination": "sip:fail@example.test", "fallback_behavior": "callback_task"},
        )
        result = request_voice_transfer(call=call, destination_key=destination.key, idempotency_key="refer-fail")
        self.assertEqual(result["status"], "callback")
        self.assertEqual(FollowUpTask.objects.count(), 1)
        self.assertEqual(call.conversation.ai_handoffs.count(), 1)

    def test_human_takeover_is_tenant_scoped_and_supersedes_ai(self):
        call = self.create_call()
        with self.assertRaises(VoiceError):
            human_takeover(call, membership=self.other_membership)
        updated = human_takeover(call, membership=self.membership)
        self.assertFalse(updated.ai_control_active)
        self.assertIsNotNone(updated.human_takeover_at)

    def test_twilio_carrier_signature_and_status_idempotency(self):
        self.connection.carrier_auth_token_encrypted = "test-only-twilio-voice-token"
        self.connection.save(update_fields=["carrier_auth_token_encrypted"])
        call = self.create_call()
        call.carrier_call_id = "CA11111111111111111111111111111111"
        call.save(update_fields=["carrier_call_id"])
        params = {"CallSid": call.carrier_call_id, "CallStatus": "completed", "SequenceNumber": "1", "FutureField": "accepted"}
        path = f"/api/v1/webhooks/twilio/voice/{self.connection.webhook_public_key}/status/"
        url = f"https://api.example.test{path}"
        signature = RequestValidator("test-only-twilio-voice-token").compute_signature(url, params)
        first = self.client.post(path, urlencode(params), content_type="application/x-www-form-urlencoded", HTTP_X_TWILIO_SIGNATURE=signature)
        second = self.client.post(path, urlencode(params), content_type="application/x-www-form-urlencoded", HTTP_X_TWILIO_SIGNATURE=signature)
        self.assertEqual(first.status_code, 202)
        self.assertFalse(first.data["duplicate"])
        self.assertTrue(second.data["duplicate"])
        self.assertEqual(VoiceCarrierStatusEvent.objects.count(), 1)
        invalid = self.client.post(path, urlencode({**params, "SequenceNumber": "2"}), content_type="application/x-www-form-urlencoded", HTTP_X_TWILIO_SIGNATURE="bad")
        self.assertEqual(invalid.status_code, 403)

    def test_call_api_cross_tenant_404_and_superuser_has_no_bypass(self):
        call = self.create_call()
        self.client.force_authenticate(self.other_user)
        response = self.client.get(f"/api/v1/voice/calls/{call.id}/", **self.tenant(self.other))
        self.assertEqual(response.status_code, 404)
        superuser = User.objects.create_superuser("root-voice", "root-voice@example.test", "pw12345!")
        self.client.force_authenticate(superuser)
        response = self.client.get(f"/api/v1/voice/calls/{call.id}/", **self.tenant())
        self.assertIn(response.status_code, {403, 404})

    def test_suspended_organization_is_read_only(self):
        self.organization.status = OrganizationStatus.SUSPENDED
        self.organization.save(update_fields=["status"])
        get_response = self.client.get(f"/api/v1/integrations/voice/{self.connection.id}/", **self.tenant())
        patch_response = self.client.patch(
            f"/api/v1/integrations/voice/{self.connection.id}/", {"greeting": "Changed"}, format="json", **self.tenant()
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(patch_response.status_code, 403)

    def test_session_uses_published_context_only_strict_tools_and_no_beta_header(self):
        call = self.create_call()
        session = VoiceSessionBuilder().build(call=call)
        self.assertEqual(session["type"], "realtime")
        self.assertNotIn("OpenAI-Beta", json.dumps(session))
        self.assertIn("request_human_handoff", {tool["name"] for tool in session["tools"]})
        self.assertIsNone(latest_published_context(self.organization))
        self.assertNotIn("chain-of-thought", session["instructions"].lower())

    def test_openai_provider_current_ga_endpoints_and_safety_identifier(self):
        provider = OpenAIRealtimeSIPProvider()
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.headers = {"x-request-id": "request-1"}
        with override_settings(VOICE_ENABLE_LIVE=True, OPENAI_API_KEY="test-only-openai-key"), patch("voice.providers.httpx.post", return_value=response) as post:
            provider.accept(call_id="call-1", session={"type": "realtime"}, safety_identifier="stable-safe-id")
            provider.refer(call_id="call-1", target_uri="tel:+15550103333", idempotency_key="refer-key")
            provider.hangup(call_id="call-1")
        urls = [entry.args[0] for entry in post.call_args_list]
        self.assertIn("https://api.openai.com/v1/realtime/calls/call-1/accept", urls)
        self.assertIn("https://api.openai.com/v1/realtime/calls/call-1/refer", urls)
        self.assertIn("https://api.openai.com/v1/realtime/calls/call-1/hangup", urls)
        for entry in post.call_args_list:
            self.assertNotIn("OpenAI-Beta", entry.kwargs["headers"])

    def test_worker_claim_is_single_controller_and_release_bounded_retry(self):
        call = self.create_call()
        job = VoiceControllerJob.objects.get(call=call)
        claimed = claim_next_job("worker-one")
        self.assertEqual(claimed.id, job.id)
        self.assertIsNone(claim_next_job("worker-two"))
        release_job(job.id, completed=False, error="temporary")
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.attempt_count, 1)

    def test_fake_test_call_api_and_mobile_safe_call_detail_shape(self):
        response = self.client.post(
            f"/api/v1/integrations/voice/{self.connection.id}/test-call/",
            {"caller": "+15550104444", "language": "ru", "utterance": "Позовите человека"},
            format="json", **self.tenant(),
        )
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(len(response.data["transcript"]), 2)
        serialized = json.dumps(response.data, default=str)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("audio_data", serialized)
        self.assertNotIn("reasoning", serialized)

    def test_management_api_covers_readiness_health_update_rotation_transfer_and_calls(self):
        readiness = self.client.get("/api/v1/integrations/voice/readiness/", **self.tenant())
        self.assertEqual(readiness.status_code, 200)
        self.assertTrue(readiness.data["fake_provider"])
        detail = self.client.patch(
            f"/api/v1/integrations/voice/{self.connection.id}/",
            {"greeting": "Welcome to Voice", "max_tools_per_call": 3},
            format="json", **self.tenant(),
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["greeting"], "Welcome to Voice")
        cached_health = self.client.get(
            f"/api/v1/integrations/voice/{self.connection.id}/health/", **self.tenant()
        )
        active_health = self.client.post(
            f"/api/v1/integrations/voice/{self.connection.id}/health/", {}, format="json", **self.tenant()
        )
        self.assertIsNone(cached_health.data["carrier_reachable"])
        self.assertTrue(active_health.data["carrier_reachable"])
        rotated = self.client.post(
            f"/api/v1/integrations/voice/{self.connection.id}/rotate-credentials/",
            {"carrier_api_key_sid": "SK-safe-id", "carrier_api_key_secret": "replacement-test-value"},
            format="json", **self.tenant(),
        )
        self.assertEqual(rotated.status_code, 200)
        self.assertNotIn("carrier_api_key_secret", rotated.data)
        created = self.client.post(
            "/api/v1/integrations/voice/connections/",
            {"carrier": "fake", "ownership_mode": "platform_managed", "phone_number_e164": "+15550107777"},
            format="json", **self.tenant(),
        )
        self.assertEqual(created.status_code, 201)
        destination = self.client.post(
            f"/api/v1/integrations/voice/{self.connection.id}/transfers/",
            {"key": "operator", "display_name": "Operator", "destination_type": "phone", "destination": "+15550106666"},
            format="json", **self.tenant(),
        )
        self.assertEqual(destination.status_code, 201)
        listed = self.client.get(
            f"/api/v1/integrations/voice/{self.connection.id}/transfers/", **self.tenant()
        )
        self.assertEqual(len(listed.data), 1)
        changed = self.client.patch(
            f"/api/v1/integrations/voice/{self.connection.id}/transfers/{destination.data['id']}/",
            {"display_name": "Main operator", "destination": "+15550105555"},
            format="json", **self.tenant(),
        )
        self.assertEqual(changed.data["display_name"], "Main operator")
        removed = self.client.delete(
            f"/api/v1/integrations/voice/{self.connection.id}/transfers/{destination.data['id']}/",
            **self.tenant(),
        )
        self.assertEqual(removed.status_code, 204)
        call = self.create_call(event_id="evt-management", call_id="call-management")
        calls = self.client.get("/api/v1/voice/calls/", **self.tenant())
        call_detail = self.client.get(f"/api/v1/voice/calls/{call.id}/", **self.tenant())
        takeover = self.client.post(f"/api/v1/voice/calls/{call.id}/takeover/", {}, format="json", **self.tenant())
        self.assertGreaterEqual(calls.data["count"], 1)
        self.assertEqual(call_detail.data["id"], str(call.id))
        self.assertFalse(takeover.data["ai_control_active"])

    def test_controller_handles_consent_tools_ephemeral_unclear_transfer_takeover_and_limit(self):
        self.connection.disclosure_mode = "explicit_transcript_consent"
        self.connection.save(update_fields=["disclosure_mode"])
        consent_call = self.create_call(event_id="evt-consent", call_id="call-consent")
        asyncio.run(VoiceRealtimeController(call_id=consent_call.id, events=[
            {"type": "voice.transcript_consent", "granted": True},
            {"type": "voice.caller_transcript.final", "text": "I consent", "language": "en"},
            {"type": "voice.tool_call", "call_id": "bad-tool", "name": "generic_http", "arguments": "not-json"},
            {"type": "response.done", "response": {"usage": {"input_audio_tokens": 3}}},
            {"type": "voice.completed", "outcome": "answered"},
        ]).run())
        consent_call.refresh_from_db()
        self.assertEqual(consent_call.consent_state, "granted")
        self.assertEqual(consent_call.transcript_segments.count(), 1)
        self.assertFalse(VoiceToolCall.objects.filter(call=consent_call).exists())

        self.connection.transcript_retention_mode = "disabled"
        self.connection.disclosure_mode = "ai_disclosure"
        self.connection.save(update_fields=["transcript_retention_mode", "disclosure_mode"])
        ephemeral = self.create_call(event_id="evt-ephemeral", call_id="call-ephemeral")
        controller = VoiceRealtimeController(call_id=ephemeral.id, events=[])
        asyncio.run(controller.handle({"type": "voice.caller_transcript.final", "text": "ephemeral"}))
        asyncio.run(controller.handle({"type": "voice.caller_transcript.final", "text": ""}))
        self.assertEqual(controller.ephemeral_segments[0]["text"], "ephemeral")
        self.assertFalse(VoiceTranscriptSegment.objects.filter(call=ephemeral).exists())

        unclear = self.create_call(event_id="evt-unclear", call_id="call-unclear")
        asyncio.run(VoiceRealtimeController(call_id=unclear.id, events=[
            {"type": "voice.unclear"}, {"type": "voice.unclear"}, {"type": "voice.unclear"},
        ]).run())
        unclear.refresh_from_db()
        self.assertEqual(unclear.outcome, "callback_requested")
        self.assertTrue(FollowUpTask.objects.filter(related_conversation=unclear.conversation).exists())

        destination = create_transfer_destination(
            connection=self.connection, membership=self.membership,
            data={"key": "controller-desk", "display_name": "Desk", "destination_type": "phone", "destination": "+15550104444"},
        )
        transferred = self.create_call(event_id="evt-controller-transfer", call_id="call-controller-transfer")
        asyncio.run(VoiceRealtimeController(call_id=transferred.id, events=[
            {"type": "voice.transfer", "id": "transfer-event", "destination_key": destination.key},
        ]).run())
        transferred.refresh_from_db()
        self.assertEqual(transferred.status, VoiceCallStatus.TRANSFERRED)

        takeover = self.create_call(event_id="evt-controller-takeover", call_id="call-controller-takeover")
        asyncio.run(VoiceRealtimeController(call_id=takeover.id, events=[{"type": "voice.human_takeover"}]).run())
        takeover.refresh_from_db()
        self.assertFalse(takeover.ai_control_active)

        limited = self.create_call(event_id="evt-controller-limit", call_id="call-controller-limit")
        with self.assertRaises(TimeoutError):
            asyncio.run(VoiceRealtimeController(call_id=limited.id, events=[{"type": "voice.max_duration"}]).run())

    def test_provider_selection_credentials_and_fail_closed_errors(self):
        self.assertTrue(carrier_provider_for(self.connection).health(self.connection)["carrier_reachable"])
        with override_settings(VOICE_FAKE_PROVIDER=False):
            with self.assertRaises(VoiceProviderError):
                carrier_provider_for(self.connection)
        live = TwilioSIPCarrierProvider()
        with self.assertRaises(VoiceProviderError):
            live.health(self.connection)
        self.connection.ownership_mode = "customer_owned"
        self.connection.carrier_account_sid = "AC-safe"
        self.connection.carrier_api_key_sid = "SK-safe"
        self.connection.carrier_api_key_secret_encrypted = "test-only-secret"
        self.assertEqual(live._credentials(self.connection), ("AC-safe", "SK-safe", "test-only-secret"))
        self.connection.carrier_api_key_secret_encrypted = ""
        self.connection.carrier_auth_token_encrypted = ""
        with self.assertRaises(VoiceProviderError):
            live._credentials(self.connection)
        fake_realtime = FakeRealtimeVoiceProvider([{"type": "one"}])
        fake_realtime.reject(call_id="one")
        fake_realtime.hangup(call_id="one")
        self.assertEqual(
            asyncio.run(self._collect_events(fake_realtime)), [{"type": "one"}]
        )
        with override_settings(VOICE_FAKE_PROVIDER=False):
            with self.assertRaises(VoiceProviderError):
                fake_realtime.accept(call_id="one", session={}, safety_identifier="safe")
        provider = OpenAIRealtimeSIPProvider()
        with self.assertRaises(VoiceProviderError):
            provider.reject(call_id="not-configured")
        self.connection.carrier = "twilio_sip"
        with override_settings(VOICE_REALTIME_PROVIDER="unsupported"):
            with self.assertRaises(VoiceProviderError):
                realtime_provider_for(self.connection)

    async def _collect_events(self, provider):
        return [event async for event in provider.events(call_id="one")]

    def test_finalization_is_idempotent_and_records_crm_activity(self):
        call = self.create_call()
        store_final_transcript(call=call, speaker="caller", text="Please call me back")
        first = finalize_call(call, outcome="callback_requested", hangup_actor="caller")
        second = finalize_call(call, outcome="failed", hangup_actor="provider", error="late_duplicate")
        self.assertEqual(first.ended_at, second.ended_at)
        self.assertEqual(second.outcome, "callback_requested")
        self.assertEqual(VoiceUsageEvent.objects.count(), 1)
        self.assertTrue(CrmActivity.objects.for_organization(self.organization).filter(event_type="voice.call_completed").exists())

    def test_retention_cleanup_deletes_transcript_not_call(self):
        call = self.create_call()
        store_final_transcript(call=call, speaker="caller", text="retained temporarily")
        call.ended_at = timezone.now() - timedelta(days=31)
        call.voice_connection.transcript_retention_mode = "30_days"
        call.voice_connection.save(update_fields=["transcript_retention_mode"])
        call.save(update_fields=["ended_at"])
        self.assertEqual(privacy_delete_expired_transcripts(), 1)
        self.assertTrue(VoiceCall.objects.filter(pk=call.pk).exists())

    def test_readiness_fake_defaults_require_no_credentials(self):
        result = integration_readiness()
        self.assertEqual(result["mode"], "development")
        self.assertTrue(result["enabled"])
        self.assertEqual(result["defaults"]["recording"], "disabled")
        self.assertFalse(result["live_ready"])

    def test_no_outbound_or_audio_storage_schema(self):
        call_fields = {field.name for field in VoiceCall._meta.fields}
        connection_fields = {field.name for field in VoiceConnection._meta.fields}
        self.assertNotIn("audio", call_fields)
        self.assertNotIn("recording_url", call_fields)
        self.assertNotIn("outbound_enabled", connection_fields)
        self.assertEqual(VoiceCall._meta.get_field("direction").default, "inbound")
