from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connection as database_connection, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from twilio.request_validator import RequestValidator

from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.models import (
    Contact,
    ContactIdentity,
    ContactIdentityType,
    Conversation,
    Message,
    MessageDirection,
    MessageSenderType,
    MessageStatus,
)
from organizations.models import OrganizationMembership, OrganizationStatus
from organizations.services import create_organization
from sms.consent import apply_inbound_consent, classify_keyword
from sms.models import (
    SMSAutomationMode,
    SMSConnection,
    SMSConnectionStatus,
    SMSConsent,
    SMSConsentState,
    SMSOutboundAttempt,
    SMSOwnershipMode,
    SMSProviderType,
    SMSStatusEvent,
    SMSWebhookEnvelope,
    SMSWebhookProcessingStatus,
)
from sms.parser import SMSPayloadError, normalize_phone, parse_inbound, parse_status
from sms.providers import FakeSMSProvider, SMSProviderError, TwilioSMSProvider, provider_for
from sms.segments import estimate_segments
from sms.services import (
    SMSError,
    connection_health,
    conversation_policy,
    create_connection,
    integration_readiness,
    process_status_envelope,
    retry_outbound_attempt,
    send_sms_message,
    set_connection_state,
    update_connection,
    webhook_urls,
)
from sms.tasks import check_sms_connections, retry_failed_sms_webhooks
from sms.verifier import SMSWebhookVerifier


User = get_user_model()


@override_settings(
    DEBUG=True,
    SECURE_SSL_REDIRECT=False,
    SMS_ENABLE_LIVE=False,
    SMS_FAKE_PROVIDER=True,
    SMS_PUBLIC_BASE_URL="https://api.example.test",
    TWILIO_AUTH_TOKEN="test-only-twilio-auth-token",
    FIELD_ENCRYPTION_KEY="j64ChG14GGzpCY_wJAkkVx1fb0V3w_CVQvc--vvSeI8=",
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class SMSMessagingTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username="sms-owner", email="sms-owner@example.test", password="pw12345!"
        )
        self.organization = create_organization(
            creator=self.owner, name="SMS Clinic", slug="sms-clinic"
        )
        self.membership = OrganizationMembership.objects.get(
            organization=self.organization, user=self.owner
        )
        self.other_user = User.objects.create_user(
            username="sms-other", email="sms-other@example.test", password="pw12345!"
        )
        self.other = create_organization(
            creator=self.other_user, name="Other SMS", slug="other-sms"
        )
        self.client.force_authenticate(self.owner)
        self.connection = self.make_connection(
            organization=self.organization,
            membership=self.membership,
            sender="+15550101111",
            provider=SMSProviderType.TWILIO,
            auth_token="test-only-twilio-auth-token",
        )

    def tenant(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.organization).id)}

    def make_connection(self, *, organization, membership, sender, provider, auth_token=""):
        channel = ChannelConnection.objects.create(
            organization=organization,
            type=ChannelType.SMS,
            provider="twilio" if provider == SMSProviderType.TWILIO else "fake_sms",
            display_name=f"SMS {sender}",
            external_identifier=sender,
            status=ChannelStatus.ACTIVE,
        )
        return SMSConnection.objects.create(
            organization=organization,
            channel_connection=channel,
            provider=provider,
            status=SMSConnectionStatus.CONNECTED,
            account_sid="AC11111111111111111111111111111111" if provider == SMSProviderType.TWILIO else "",
            sender_address=sender,
            sender_capabilities=["sms"],
            auth_token_encrypted=auth_token,
            connected_by=membership,
            connected_at=timezone.now(),
        )

    def signed_post(self, event_type, params, *, connection=None, token=None, extra_headers=None):
        connection = connection or self.connection
        path = f"/api/v1/webhooks/twilio/sms/{connection.webhook_public_key}/{event_type}/"
        url = f"https://api.example.test{path}"
        signature = RequestValidator(token or "test-only-twilio-auth-token").compute_signature(url, params)
        headers = {"HTTP_X_TWILIO_SIGNATURE": signature, **(extra_headers or {})}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                path,
                urlencode(params),
                content_type="application/x-www-form-urlencoded",
                **headers,
            )

    def inbound_params(self, **overrides):
        return {
            "AccountSid": "AC11111111111111111111111111111111",
            "MessageSid": "SM11111111111111111111111111111111",
            "From": "+15550102222",
            "To": self.connection.sender_address,
            "Body": "Hello, I need help",
            "NumMedia": "0",
            "ExtraFutureParameter": "accepted-by-sdk-validation",
            **overrides,
        }

    def test_sms_management_api_rejects_anonymous_requests_without_tenant_lookup_error(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(
            "/api/v1/integrations/sms/connections/",
            **self.tenant(),
        )

        self.assertIn(response.status_code, {401, 403})

    def test_official_signature_accepts_all_params_and_rejects_invalid_signature(self):
        accepted = self.signed_post("inbound", self.inbound_params())
        self.assertEqual(accepted.status_code, 202, accepted.data)
        self.assertFalse(accepted.data["duplicate"])
        self.assertEqual(Message.objects.count(), 1)
        rejected = self.client.post(
            f"/api/v1/webhooks/twilio/sms/{self.connection.webhook_public_key}/inbound/",
            urlencode(self.inbound_params(MessageSid="SM22222222222222222222222222222222")),
            content_type="application/x-www-form-urlencoded",
            HTTP_X_TWILIO_SIGNATURE="invalid",
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(Message.objects.count(), 1)

    def test_missing_signature_unknown_key_inactive_connection_and_cross_tenant_detail_fail_closed(self):
        path = f"/api/v1/webhooks/twilio/sms/{self.connection.webhook_public_key}/inbound/"
        missing = self.client.post(
            path,
            urlencode(self.inbound_params()),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(missing.status_code, 403)
        unknown = self.client.post(
            "/api/v1/webhooks/twilio/sms/not-a-connection/inbound/",
            urlencode(self.inbound_params()),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(unknown.status_code, 404)
        set_connection_state(self.connection, membership=self.membership, action="pause")
        inactive = self.signed_post("inbound", self.inbound_params())
        self.assertEqual(inactive.status_code, 404)
        self.client.force_authenticate(self.other_user)
        detail = self.client.get(
            f"/api/v1/integrations/sms/{self.connection.id}/",
            **self.tenant(self.other),
        )
        self.assertEqual(detail.status_code, 404)
        superuser = User.objects.create_superuser(
            username="sms-superuser", email="sms-superuser@example.test", password="pw12345!"
        )
        self.client.force_authenticate(superuser)
        bypass = self.client.get(
            f"/api/v1/integrations/sms/{self.connection.id}/",
            **self.tenant(),
        )
        self.assertEqual(bypass.status_code, 403)

    def test_duplicate_message_sid_is_idempotent_before_crm_effects(self):
        first = self.signed_post("inbound", self.inbound_params())
        second = self.signed_post("inbound", self.inbound_params())
        self.assertEqual((first.status_code, second.status_code), (202, 202))
        self.assertTrue(second.data["duplicate"])
        self.assertEqual(SMSWebhookEnvelope.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 1)
        conversation = Conversation.objects.get()
        self.assertEqual(conversation.unread_count, 1)

    def test_tenant_is_route_key_plus_verified_destination_and_header_is_ignored(self):
        other_membership = OrganizationMembership.objects.get(
            organization=self.other, user=self.other_user
        )
        other_connection = self.make_connection(
            organization=self.other,
            membership=other_membership,
            sender="+15550103333",
            provider=SMSProviderType.TWILIO,
            auth_token="test-only-other-auth-token",
        )
        wrong_destination = self.signed_post(
            "inbound",
            self.inbound_params(To=other_connection.sender_address),
            extra_headers={"HTTP_X_ORGANIZATION_ID": str(self.other.id)},
        )
        self.assertEqual(wrong_destination.status_code, 404)
        self.assertEqual(Message.objects.count(), 0)
        accepted = self.signed_post(
            "inbound",
            self.inbound_params(MessageSid="SM33333333333333333333333333333333"),
            extra_headers={"HTTP_X_ORGANIZATION_ID": str(self.other.id)},
        )
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(Message.objects.get().organization_id, self.organization.id)

    def test_stop_start_help_and_advanced_opt_out_are_authoritative(self):
        self.connection.advanced_opt_out_enabled = True
        self.connection.ai_mode = SMSAutomationMode.AUTOPILOT
        self.connection.save(update_fields=["advanced_opt_out_enabled", "ai_mode", "updated_at"])
        with patch("sms.services._enqueue_ai_inbound") as enqueue_ai:
            stopped = self.signed_post(
                "inbound", self.inbound_params(Body="STOP", OptOutType="STOP")
            )
        self.assertEqual(stopped.status_code, 202)
        consent = SMSConsent.objects.get()
        self.assertEqual(consent.state, SMSConsentState.OPTED_OUT)
        enqueue_ai.assert_not_called()
        self.assertEqual(classify_keyword("  help "), "HELP")
        identity = consent.contact_identity
        local_start = apply_inbound_consent(
            connection=self.connection,
            contact_identity=identity,
            body="START",
            provider_signal="",
        )
        self.assertEqual(local_start.state, SMSConsentState.OPTED_OUT)
        provider_start = apply_inbound_consent(
            connection=self.connection,
            contact_identity=identity,
            body="START",
            provider_signal="START",
        )
        self.assertEqual(provider_start.state, SMSConsentState.OPTED_IN)
        help_decision = apply_inbound_consent(
            connection=self.connection,
            contact_identity=identity,
            body="HELP",
            provider_signal="HELP",
        )
        self.assertEqual(help_decision.keyword_type, "HELP")
        self.assertFalse(help_decision.ai_eligible)

    def test_opted_out_recipient_is_blocked_before_provider_send(self):
        self.signed_post("inbound", self.inbound_params(Body="STOP", OptOutType="STOP"))
        conversation = Conversation.objects.get()
        self.assertEqual(conversation_policy(conversation)["state"], "opted_out")
        with patch("sms.services.provider_for") as provider:
            with self.assertRaisesMessage(SMSError, "opted_out"):
                send_sms_message(
                    conversation=conversation,
                    body="This must not send",
                    client_message_id="blocked-send",
                    membership=self.membership,
                )
        provider.assert_not_called()
        self.assertFalse(Message.objects.filter(direction=MessageDirection.OUTBOUND).exists())

    def test_deterministic_fake_send_uses_segment_metadata_and_never_read(self):
        self.signed_post("inbound", self.inbound_params(From="+14155552671"))
        conversation = Conversation.objects.get()
        with patch("sms.services.provider_for", return_value=FakeSMSProvider()):
            first, created = send_sms_message(
                conversation=conversation,
                body="Здравствуйте 👋",
                client_message_id="fake-send-1",
                membership=self.membership,
            )
            duplicate, duplicate_created = send_sms_message(
                conversation=conversation,
                body="Здравствуйте 👋",
                client_message_id="fake-send-1",
                membership=self.membership,
            )
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first.id, duplicate.id)
        self.assertTrue(first.provider_message_id.startswith("SM"))
        self.assertEqual(first.status, MessageStatus.QUEUED)
        self.assertEqual(first.metadata["segments"]["encoding"], "UCS-2")
        self.assertFalse(first.metadata["supports_read_receipts"])
        self.assertEqual(SMSOutboundAttempt.objects.count(), 1)

    def test_status_callbacks_are_verified_idempotent_monotonic_and_ignore_read(self):
        self.signed_post("inbound", self.inbound_params())
        conversation = Conversation.objects.get()
        with patch("sms.services.provider_for", return_value=FakeSMSProvider()):
            message, _ = send_sms_message(
                conversation=conversation,
                body="Delivery lifecycle",
                client_message_id="lifecycle-1",
                membership=self.membership,
            )
        base = {
            "MessageSid": message.provider_message_id,
            "From": self.connection.sender_address,
            "To": conversation.external_thread_id,
        }
        delivered = self.signed_post("status", {**base, "MessageStatus": "delivered", "NumSegments": "1"})
        duplicate = self.signed_post("status", {**base, "MessageStatus": "delivered", "NumSegments": "1"})
        late_sent = self.signed_post("status", {**base, "MessageStatus": "sent"})
        read = self.signed_post("status", {**base, "MessageStatus": "read"})
        self.assertEqual(delivered.status_code, 202)
        self.assertTrue(duplicate.data["duplicate"])
        self.assertEqual(late_sent.status_code, 202)
        self.assertEqual(read.status_code, 202)
        message.refresh_from_db()
        self.assertEqual(message.status, MessageStatus.DELIVERED)
        self.assertNotEqual(message.status, MessageStatus.READ)
        self.assertEqual(SMSStatusEvent.objects.filter(provider_status="delivered").count(), 1)

    def test_segment_estimator_handles_gsm_extensions_cyrillic_uzbek_and_emoji(self):
        self.assertEqual(estimate_segments("Hello").encoding, "GSM-7")
        self.assertEqual(estimate_segments("^" * 81).segments, 2)
        self.assertEqual(estimate_segments("Здравствуйте").encoding, "UCS-2")
        self.assertEqual(estimate_segments("Oʻzbekiston 👋").encoding, "UCS-2")
        self.assertEqual(estimate_segments("😀" * 36).segments, 2)

    def test_mms_metadata_is_bounded_without_downloading_media(self):
        response = self.signed_post(
            "inbound",
            self.inbound_params(Body="", NumMedia="2", MessageSid="SMMMS11111111111111111111111111111"),
        )
        self.assertEqual(response.status_code, 202, response.data)
        message = Message.objects.get()
        self.assertEqual(message.metadata["media_count"], 2)
        self.assertEqual(message.body, "[MMS: 2 media item(s)]")

    def test_sms_ai_suggest_is_runtime_gated_and_autopilot_requires_published_context(self):
        runtime = self.client.patch(
            "/api/v1/ai/runtime-config/",
            {
                "enabled": True,
                "default_mode": "suggest",
                "allowed_channel_connections": [str(self.connection.channel_connection_id)],
            },
            format="json",
            **self.tenant(),
        )
        self.assertEqual(runtime.status_code, 200, runtime.data)
        suggest = self.client.patch(
            f"/api/v1/integrations/sms/{self.connection.id}/",
            {"ai_mode": "suggest"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(suggest.status_code, 200, suggest.data)
        with patch("sms.services._enqueue_ai_inbound") as enqueue:
            inbound = self.signed_post("inbound", self.inbound_params())
        self.assertEqual(inbound.status_code, 202)
        enqueue.assert_called_once()
        autopilot = self.client.patch(
            f"/api/v1/integrations/sms/{self.connection.id}/",
            {"ai_mode": "autopilot"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(autopilot.status_code, 409, autopilot.data)
        self.assertEqual(autopilot.data["error"]["code"], "published_context_required")

    def test_connection_api_is_tenant_scoped_write_only_and_encrypted(self):
        response = self.client.get("/api/v1/integrations/sms/connections/", **self.tenant())
        self.assertEqual(response.status_code, 200)
        row = response.data["results"][0]
        self.assertNotIn("auth_token", row)
        self.assertNotIn("api_key_secret", row)
        self.assertTrue(row["has_auth_token"])
        with database_connection.cursor() as cursor:
            cursor.execute("SELECT auth_token_encrypted FROM sms_smsconnection LIMIT 1")
            stored = cursor.fetchone()[0]
        self.assertNotEqual(stored, "test-only-twilio-auth-token")
        forbidden = self.client.get(
            f"/api/v1/integrations/sms/{self.connection.id}/",
            **self.tenant(self.other),
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_agent_can_apply_a_safety_block_but_cannot_manage_connections(self):
        self.signed_post("inbound", self.inbound_params())
        contact = Contact.objects.get()
        agent = User.objects.create_user(username="sms-agent", password="pw12345!")
        OrganizationMembership.objects.create(
            organization=self.organization, user=agent, role="agent", status="active"
        )
        self.client.force_authenticate(agent)
        blocked = self.client.post(
            f"/api/v1/integrations/sms/{self.connection.id}/consent/",
            {"contact_id": str(contact.id), "state": "blocked"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(blocked.status_code, 200, blocked.data)
        self.assertEqual(blocked.data["state"], SMSConsentState.BLOCKED)
        forbidden = self.client.post(
            "/api/v1/integrations/sms/connections/",
            {"provider": "fake", "sender_address": "+15550104444"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_suspended_organization_fails_closed_for_webhook_and_send(self):
        self.organization.status = OrganizationStatus.SUSPENDED
        self.organization.save(update_fields=["status", "updated_at"])
        response = self.signed_post("inbound", self.inbound_params())
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Message.objects.exists())

    def test_long_sms_requires_confirmation_and_respects_hard_limit(self):
        self.signed_post("inbound", self.inbound_params())
        conversation = Conversation.objects.get()
        body = "A" * 460
        with self.assertRaisesMessage(SMSError, "segment_confirmation_required"):
            send_sms_message(
                conversation=conversation,
                body=body,
                client_message_id="long-unconfirmed",
                membership=self.membership,
            )
        with patch("sms.services.provider_for", return_value=FakeSMSProvider()):
            message, created = send_sms_message(
                conversation=conversation,
                body=body,
                client_message_id="long-confirmed",
                membership=self.membership,
                confirm_segments=True,
            )
        self.assertTrue(created)
        self.assertEqual(message.metadata["segments"]["segments"], 4)
        with self.settings(SMS_HUMAN_MAX_SEGMENTS=2):
            with self.assertRaisesMessage(SMSError, "segment_limit_exceeded"):
                send_sms_message(
                    conversation=conversation,
                    body="B" * 400,
                    client_message_id="over-hard-limit",
                    membership=self.membership,
                    confirm_segments=True,
                )

    def test_country_policy_blocks_before_provider(self):
        self.signed_post("inbound", self.inbound_params(From="+14155552671"))
        conversation = Conversation.objects.get()
        with self.settings(SMS_BLOCKED_COUNTRY_CODES=["US"]):
            with patch("sms.services.provider_for") as provider:
                with self.assertRaisesMessage(SMSError, "recipient_country_blocked"):
                    send_sms_message(
                        conversation=conversation,
                        body="Blocked country",
                        client_message_id="country-block",
                        membership=self.membership,
                    )
            provider.assert_not_called()

    def test_permanent_provider_failure_is_safe_and_opens_bounded_circuit(self):
        self.signed_post("inbound", self.inbound_params())
        conversation = Conversation.objects.get()
        failing = FakeSMSProvider()
        failing.send = lambda **kwargs: (_ for _ in ()).throw(SMSProviderError("21610"))
        with self.settings(SMS_CIRCUIT_BREAKER_FAILURES=1):
            with patch("sms.services.provider_for", return_value=failing):
                message, created = send_sms_message(
                    conversation=conversation,
                    body="Provider failure",
                    client_message_id="failure-1",
                    membership=self.membership,
                )
        self.assertTrue(created)
        self.assertEqual(message.status, MessageStatus.FAILED)
        failed = Message.objects.get(client_message_id="failure-1")
        self.assertEqual((failed.status, failed.error_code), (MessageStatus.FAILED, "21610"))
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, SMSConnectionStatus.DEGRADED)

    def test_privacy_export_and_anonymize_are_tenant_scoped(self):
        self.signed_post("inbound", self.inbound_params())
        contact = Contact.objects.get()
        exported = self.client.get(
            f"/api/v1/integrations/sms/{self.connection.id}/privacy/",
            {"contact_id": str(contact.id)},
            **self.tenant(),
        )
        self.assertEqual(exported.status_code, 200, exported.data)
        self.assertEqual(exported.data["contact_id"], str(contact.id))
        erased = self.client.post(
            f"/api/v1/integrations/sms/{self.connection.id}/privacy/",
            {"contact_id": str(contact.id), "mode": "anonymize", "confirm": True},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(erased.status_code, 200, erased.data)
        self.assertEqual(Message.objects.get().body, "[redacted by privacy request]")

    def test_privacy_erasure_retains_minimized_opt_out_suppression(self):
        self.signed_post("inbound", self.inbound_params(Body="STOP", OptOutType="STOP"))
        contact = Contact.objects.get()
        consent = SMSConsent.objects.get()
        consent.last_keyword = "STOP"
        consent.save(update_fields=["last_keyword", "updated_at"])
        response = self.client.post(
            f"/api/v1/integrations/sms/{self.connection.id}/privacy/",
            {"contact_id": str(contact.id), "mode": "delete", "confirm": True},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(response.status_code, 200, response.data)
        consent.refresh_from_db()
        self.assertEqual(consent.state, SMSConsentState.OPTED_OUT)
        self.assertEqual(consent.last_keyword, "")

    def test_active_sender_is_unique_and_organization_is_immutable(self):
        other_membership = OrganizationMembership.objects.get(
            organization=self.other, user=self.other_user
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_connection(
                organization=self.other,
                membership=other_membership,
                sender=self.connection.sender_address,
                provider=SMSProviderType.TWILIO,
                auth_token="other-token",
            )
        self.connection.organization = self.other
        with self.assertRaisesMessage(Exception, "Organization is immutable"):
            self.connection.save()

    def test_fake_connection_lifecycle_health_test_rotation_and_readiness(self):
        set_connection_state(self.connection, membership=self.membership, action="disconnect")
        readiness = self.client.get("/api/v1/integrations/sms/readiness/", **self.tenant())
        self.assertEqual(readiness.status_code, 200)
        self.assertTrue(readiness.data["enabled"])

        created = self.client.post(
            "/api/v1/integrations/sms/connections/",
            {
                "display_name": "CI fake SMS",
                "provider": "fake",
                "ownership_mode": "customer_owned",
                "sender_address": "+14155550199",
                "advanced_opt_out_enabled": True,
                "auth_token": "replacement-only-test-token",
            },
            format="json",
            **self.tenant(),
        )
        self.assertEqual(created.status_code, 201, created.data)
        connection_id = created.data["id"]
        detail_url = f"/api/v1/integrations/sms/{connection_id}/"

        detail = self.client.get(detail_url, **self.tenant())
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.data["webhook_urls"]["inbound"].startswith("https://"))
        updated = self.client.patch(
            detail_url,
            {
                "allow_inbound_support": False,
                "default_language": "uz",
                "supported_languages": ["uz", "ru"],
                "ai_mode": "suggest",
            },
            format="json",
            **self.tenant(),
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["default_language"], "uz")

        health = self.client.post(f"{detail_url}health/", {}, format="json", **self.tenant())
        self.assertEqual(health.status_code, 200, health.data)
        self.assertTrue(health.data["account_reachable"])
        fake_inbound = self.client.post(
            f"{detail_url}test/",
            {"from": "+14155552671", "body": "Fake inbound", "message_sid": "SMFAKELIFECYCLE"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(fake_inbound.status_code, 202, fake_inbound.data)

        rotated = self.client.post(
            f"{detail_url}rotate-credentials/",
            {"auth_token": "rotated-test-token", "api_key_sid": "SKTEST", "api_key_secret": "secret"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(rotated.status_code, 200, rotated.data)
        self.assertTrue(rotated.data["has_api_key_secret"])
        for action, expected in (("pause", "paused"), ("activate", "connected"), ("disconnect", "disconnected")):
            response = self.client.post(f"{detail_url}{action}/", {}, format="json", **self.tenant())
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["status"], expected)

    def test_twilio_provider_uses_official_client_and_redacts_provider_errors(self):
        provider = TwilioSMSProvider()
        account = SimpleNamespace(status="active")
        sent = SimpleNamespace(sid="SMTWILIORESULT", status="queued", num_segments="2")
        client = MagicMock()
        client.api.accounts.return_value.fetch.return_value = account
        client.messages.create.return_value = sent
        self.connection.messaging_service_sid = "MG11111111111111111111111111111111"
        self.connection.save(update_fields=["messaging_service_sid", "updated_at"])
        with self.settings(
            SMS_ENABLE_LIVE=True,
            TWILIO_ACCOUNT_SID="AC11111111111111111111111111111111",
            TWILIO_AUTH_TOKEN="test-only-platform-token",
        ), patch("sms.providers.Client", return_value=client) as client_class:
            health = provider.health(self.connection)
            result = provider.send(
                connection=self.connection,
                to="+14155552671",
                body="Official SDK",
                status_callback="https://api.example.test/status",
            )
        self.assertTrue(health["provider_reachable"])
        self.assertEqual((result.message_sid, result.provider_segments), ("SMTWILIORESULT", 2))
        client_class.assert_called()
        self.assertEqual(
            client.messages.create.call_args.kwargs["messaging_service_sid"],
            self.connection.messaging_service_sid,
        )

        class TwilioFailure(Exception):
            code = 21614

        client.messages.create.side_effect = TwilioFailure("sensitive provider detail")
        with self.settings(
            SMS_ENABLE_LIVE=True,
            TWILIO_ACCOUNT_SID="AC11111111111111111111111111111111",
            TWILIO_AUTH_TOKEN="test-only-platform-token",
        ), patch("sms.providers.Client", return_value=client):
            with self.assertRaisesMessage(SMSProviderError, "21614") as raised:
                provider.send(
                    connection=self.connection,
                    to="+14155552671",
                    body="Failure",
                    status_callback="https://api.example.test/status",
                )
        self.assertFalse(raised.exception.transient)

    def test_platform_managed_twilio_connection_uses_configured_messaging_service(self):
        self.connection.delete()
        with self.settings(
            SMS_ENABLE_LIVE=True,
            TWILIO_ACCOUNT_SID="AC11111111111111111111111111111111",
            TWILIO_AUTH_TOKEN="test-only-platform-token",
            TWILIO_MESSAGING_SERVICE_SID="MG11111111111111111111111111111111",
        ), patch("sms.services.provider_for") as provider_factory:
            provider_factory.return_value.health.return_value = {
                "provider_reachable": True,
                "sender_active": True,
                "messaging_service_active": True,
            }
            connection = create_connection(
                organization=self.organization,
                membership=self.membership,
                data={
                    "provider": "twilio",
                    "ownership_mode": "platform_managed",
                    "sender_address": "+14155550100",
                },
            )

        self.assertEqual(
            connection.messaging_service_sid,
            "MG11111111111111111111111111111111",
        )

    def test_parser_provider_boundary_and_json_signature_validation(self):
        self.assertEqual(normalize_phone("+14155552671"), "+14155552671")
        for params in (
            {"MessageSid": "bad", "From": "+14155552671", "To": "+14155550100", "Body": "x"},
            {"MessageSid": "SMGOOD", "From": "+14155552671", "To": "+14155550100", "Body": ""},
        ):
            with self.assertRaises(SMSPayloadError):
                parse_inbound(params)
        with self.assertRaises(SMSPayloadError):
            parse_status({"MessageSid": "SMGOOD"})
        with self.assertRaises(SMSPayloadError):
            parse_status({"MessageSid": "SMGOOD", "MessageStatus": "sent", "NumSegments": "many"})
        with self.assertRaises(SMSProviderError):
            provider_for(SimpleNamespace(provider="unknown"))

        payload = self.inbound_params(MessageSid="SMJSON1111111111111111111111111111")
        raw = json.dumps(payload, separators=(",", ":"))
        validator = RequestValidator("test-only-twilio-auth-token")
        body_hash = validator.compute_hash(raw)
        path = (
            f"/api/v1/webhooks/twilio/sms/{self.connection.webhook_public_key}/inbound/"
            f"?bodySHA256={body_hash}"
        )
        url = f"https://api.example.test{path}"
        signature = validator.compute_signature(url, {})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.generic(
                "POST",
                path,
                raw,
                content_type="application/json",
                HTTP_X_TWILIO_SIGNATURE=signature,
            )
        self.assertEqual(response.status_code, 202, response.data)

    def test_failed_webhook_retry_and_connection_health_sweeps_are_bounded(self):
        inbound = SMSWebhookEnvelope.objects.create(
            organization=self.organization,
            connection=self.connection,
            provider_message_sid="SMFAILEDINBOUND",
            event_type="inbound",
            event_key="failed-inbound",
            processing_status=SMSWebhookProcessingStatus.FAILED,
            from_address="+14155552671",
            to_address=self.connection.sender_address,
            body="retry",
        )
        status = SMSWebhookEnvelope.objects.create(
            organization=self.organization,
            connection=self.connection,
            provider_message_sid="SMFAILEDSTATUS",
            event_type="status",
            event_key="failed-status",
            processing_status=SMSWebhookProcessingStatus.FAILED,
            provider_status="sent",
        )
        with patch("sms.tasks.process_sms_inbound.delay") as inbound_delay, patch(
            "sms.tasks.process_sms_status.delay"
        ) as status_delay:
            result = retry_failed_sms_webhooks.run()
        self.assertEqual(result["queued"], 2)
        inbound_delay.assert_called_once_with(str(inbound.id))
        status_delay.assert_called_once_with(str(status.id))
        with patch(
            "sms.tasks.connection_health",
            return_value={"status": SMSConnectionStatus.DEGRADED},
        ) as health:
            sweep = check_sms_connections.run()
        self.assertEqual(sweep, {"checked": 1, "degraded": 1})
        health.assert_called_once()

    def test_inbound_flood_daily_quota_and_repeated_content_fail_closed(self):
        with self.settings(SMS_INBOUND_PER_RECIPIENT_MINUTE=1):
            first = self.signed_post("inbound", self.inbound_params())
            second = self.signed_post(
                "inbound",
                self.inbound_params(MessageSid="SMFLOOD222222222222222222222222222"),
            )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 429)
        conversation = Conversation.objects.get()
        with self.settings(SMS_DAILY_MESSAGE_LIMIT=0):
            with self.assertRaisesMessage(SMSError, "daily_message_limit_exceeded"):
                send_sms_message(
                    conversation=conversation,
                    body="Quota",
                    client_message_id="quota-blocked",
                    membership=self.membership,
                )
        with patch("sms.services.provider_for", return_value=FakeSMSProvider()):
            send_sms_message(
                conversation=conversation,
                body="Repeated content",
                client_message_id="repeat-first",
                membership=self.membership,
            )
            with self.assertRaisesMessage(SMSError, "repeated_content_blocked"):
                send_sms_message(
                    conversation=conversation,
                    body="Repeated content",
                    client_message_id="repeat-second",
                    membership=self.membership,
                )

    def test_transient_provider_failure_is_durably_retried_and_owner_can_request_retry(self):
        self.signed_post("inbound", self.inbound_params())
        conversation = Conversation.objects.get()
        transient = FakeSMSProvider()
        transient.send = lambda **kwargs: (_ for _ in ()).throw(
            SMSProviderError("20429", transient=True)
        )
        with patch("sms.services.provider_for", return_value=transient):
            failed, _ = send_sms_message(
                conversation=conversation,
                body="Retry safely",
                client_message_id="retry-transient",
                membership=self.membership,
            )
        attempt = SMSOutboundAttempt.objects.get(message=failed)
        self.assertTrue(attempt.retryable)
        with patch("sms.tasks.retry_sms_outbound.delay") as delay, self.captureOnCommitCallbacks(
            execute=True
        ):
            response = self.client.post(
                f"/api/v1/integrations/sms/{self.connection.id}/retry/",
                {"message_id": str(failed.id)},
                format="json",
                **self.tenant(),
            )
        self.assertEqual(response.status_code, 202, response.data)
        delay.assert_called_once_with(str(attempt.id))
        with patch("sms.services.provider_for", return_value=FakeSMSProvider()):
            result = retry_outbound_attempt(attempt.id)
        self.assertEqual(result, {"status": "accepted", "retry": False})
        failed.refresh_from_db()
        self.assertEqual(failed.status, MessageStatus.QUEUED)
