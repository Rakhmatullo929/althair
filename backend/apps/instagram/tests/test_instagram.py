from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from channels.models import ChannelStatus
from crm.models import ContactIdentity, ConversationAIState, Message, MessageStatus
from instagram.models import (
    InstagramConnection,
    InstagramConnectionStatus,
    InstagramConversationWindow,
    InstagramOAuthState,
    InstagramOutboundAttempt,
    InstagramOutboundStatus,
    InstagramWebhookEvent,
)
from instagram.services import (
    InstagramError,
    complete_oauth,
    process_webhook_event,
    send_ai_message,
    send_instagram_message,
    window_eligibility,
)
from instagram.providers import InstagramProviderError, LiveInstagramProvider
from instagram.tasks import (
    bounded_instagram_backfill,
    check_instagram_connections,
    retry_instagram_outbound,
    verify_instagram_subscriptions,
    warn_instagram_token_expiry,
)
from organizations.models import OrganizationMembership, OrganizationStatus
from organizations.services import create_organization


User = get_user_model()


@override_settings(
    DEBUG=True,
    META_APP_ID="fake-app-id",
    META_APP_SECRET="test-only-instagram-app-secret",
    META_INSTAGRAM_VERIFY_TOKEN="instagram-test-verify-token",
    META_INSTAGRAM_GRAPH_API_VERSION="v-test",
    META_INSTAGRAM_REDIRECT_URI="http://testserver/api/v1/integrations/instagram/oauth/callback/",
    META_INSTAGRAM_ENABLE_LIVE=False,
    META_INSTAGRAM_ENABLE_HUMAN_AGENT=True,
    META_INSTAGRAM_FAKE_PROVIDER=True,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class InstagramMessagingTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username="ig-owner", email="ig-owner@example.test", password="pw12345!"
        )
        self.organization = create_organization(
            creator=self.owner, name="Instagram Clinic", slug="instagram-clinic"
        )
        self.membership = OrganizationMembership.objects.get(
            organization=self.organization, user=self.owner
        )
        self.other_user = User.objects.create_user(
            username="ig-other", email="ig-other@example.test", password="pw12345!"
        )
        self.other = create_organization(
            creator=self.other_user, name="Other Instagram", slug="other-instagram"
        )
        self.client.force_authenticate(self.owner)

    def tenant(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.organization).id)}

    def start(self, redirect="/en/app/settings/channels/instagram"):
        return self.client.get(
            f"/api/v1/integrations/instagram/oauth/start/?redirect={redirect}",
            **self.tenant(),
        )

    def connect(self, account="ig_business_100", username="clinic"):
        started = self.start()
        self.assertEqual(started.status_code, 200, started.data)
        response = self.client.get(
            "/api/v1/integrations/instagram/oauth/callback/",
            {"state": started.data["state"], "code": f"fake_connect:{account}:{username}:BUSINESS"},
        )
        self.assertEqual(response.status_code, 200, response.data)
        return InstagramConnection.objects.get(pk=response.data["connection"]["id"])

    def signed_payload(self, connection, *, sender="igscoped_customer_1", text="Hello", mid="ig_mid_1", extra=None):
        item = {
            "sender": {"id": sender},
            "recipient": {"id": connection.instagram_user_id},
            "timestamp": int(timezone.now().timestamp() * 1000),
            "message": {"mid": mid, "text": text, **(extra or {})},
        }
        raw = json.dumps(
            {"object": "instagram", "entry": [{"id": connection.instagram_user_id, "messaging": [item]}]},
            separators=(",", ":"),
        ).encode()
        signature = "sha256=" + hmac.new(
            b"test-only-instagram-app-secret", raw, hashlib.sha256
        ).hexdigest()
        return raw, signature

    def receive(self, connection, **kwargs):
        raw, signature = self.signed_payload(connection, **kwargs)
        response = self.client.post(
            "/api/v1/webhooks/instagram/",
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )
        self.assertEqual(response.status_code, 202, response.data)
        event = InstagramWebhookEvent.objects.order_by("-received_at").first()
        process_webhook_event(event.id)
        return event

    def test_oauth_state_is_hashed_short_lived_and_write_only(self):
        response = self.start()
        self.assertEqual(response.status_code, 200)
        state = InstagramOAuthState.objects.get()
        self.assertNotEqual(state.state_hash, response.data["state"])
        self.assertGreater(state.expires_at, timezone.now())
        connection = self.connect(account="ig_business_oauth")
        payload = self.client.get(
            f"/api/v1/integrations/instagram/{connection.id}/", **self.tenant()
        ).data
        self.assertTrue(payload["has_encrypted_token"])
        self.assertNotIn("access_token", str(payload))
        self.assertNotIn("fake-access", str(payload))

    def test_oauth_rejects_bad_redirect_expiry_replay_and_wrong_user(self):
        self.assertEqual(self.start("https://evil.example/callback").status_code, 400)
        started = self.start()
        row = InstagramOAuthState.objects.get(state_hash=hashlib.sha256(started.data["state"].encode()).hexdigest())
        row.expires_at = timezone.now() - timedelta(seconds=1)
        row.save(update_fields=["expires_at"])
        expired = self.client.get("/api/v1/integrations/instagram/oauth/callback/", {"state": started.data["state"], "code": "fake_connect:ig_expired:user"})
        self.assertEqual(expired.status_code, 410)
        started = self.start()
        self.client.force_authenticate(self.other_user)
        wrong = self.client.get("/api/v1/integrations/instagram/oauth/callback/", {"state": started.data["state"], "code": "fake_connect:ig_wrong:user"})
        self.assertEqual(wrong.status_code, 403)
        self.client.force_authenticate(self.owner)
        connection = complete_oauth(user=self.owner, raw_state=started.data["state"], code="fake_connect:ig_replay:user")
        with self.assertRaisesMessage(InstagramError, "oauth_state_replayed"):
            complete_oauth(user=self.owner, raw_state=started.data["state"], code="fake_connect:ig_replay:user")
        self.assertEqual(connection.organization, self.organization)

    def test_duplicate_account_disconnect_reconnect_and_tenant_scope(self):
        connection = self.connect(account="ig_duplicate")
        started = self.start()
        duplicate = self.client.get("/api/v1/integrations/instagram/oauth/callback/", {"state": started.data["state"], "code": "fake_connect:ig_duplicate:other_name"})
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn(
            self.client.get(f"/api/v1/integrations/instagram/{connection.id}/", **self.tenant(self.other)).status_code,
            [403, 404],
        )
        disconnected = self.client.post(f"/api/v1/integrations/instagram/{connection.id}/disconnect/", {}, format="json", **self.tenant())
        self.assertEqual(disconnected.data["connection_status"], "disconnected")
        connection.refresh_from_db()
        self.assertFalse(connection.channel_connection.get_credentials())
        reconnected = self.client.post(f"/api/v1/integrations/instagram/{connection.id}/reconnect/", {}, format="json", **self.tenant())
        self.assertEqual(reconnected.data["connection_status"], "connected")

    def test_webhook_challenge_and_signature_fail_closed(self):
        okay = self.client.get("/api/v1/webhooks/instagram/?hub.mode=subscribe&hub.challenge=123&hub.verify_token=instagram-test-verify-token")
        self.assertEqual(okay.content, b"123")
        self.assertEqual(self.client.get("/api/v1/webhooks/instagram/?hub.mode=subscribe&hub.challenge=123&hub.verify_token=wrong").status_code, 403)
        connection = self.connect(account="ig_signatures")
        raw, _ = self.signed_payload(connection)
        for signature in ["", "sha256=bad"]:
            denied = self.client.post("/api/v1/webhooks/instagram/", raw, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)
            self.assertEqual(denied.status_code, 403)

    def test_unknown_and_inactive_recipients_fail_closed(self):
        connection = self.connect(account="ig_inactive")
        raw, signature = self.signed_payload(connection)
        connection.channel_connection.status = ChannelStatus.DISCONNECTED
        connection.channel_connection.save(update_fields=["status", "updated_at"])
        response = self.client.post("/api/v1/webhooks/instagram/", raw, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)
        self.assertEqual(response.status_code, 404)
        unknown = raw.replace(b"ig_inactive", b"ig_unknown1")
        unknown_signature = "sha256=" + hmac.new(b"test-only-instagram-app-secret", unknown, hashlib.sha256).hexdigest()
        self.assertEqual(self.client.post("/api/v1/webhooks/instagram/", unknown, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=unknown_signature).status_code, 404)

    def test_text_ingestion_identity_window_and_duplicate_are_scoped(self):
        connection = self.connect(account="ig_ingest")
        raw, signature = self.signed_payload(connection, sender="ig_scope_44", mid="ig_idempotent")
        with self.captureOnCommitCallbacks(execute=False):
            first = self.client.post("/api/v1/webhooks/instagram/", raw, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)
            second = self.client.post("/api/v1/webhooks/instagram/", raw, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)
        self.assertEqual(first.data["accepted"], 1)
        self.assertEqual(second.data["duplicates"], 1)
        process_webhook_event(InstagramWebhookEvent.objects.get().id)
        message = Message.objects.get(provider_message_id="ig_idempotent")
        self.assertEqual(message.organization, self.organization)
        identity = ContactIdentity.objects.get(external_user_id="ig_scope_44")
        self.assertEqual(identity.organization, self.organization)
        self.assertEqual(identity.channel_connection, connection.channel_connection)
        self.assertEqual(window_eligibility(message.conversation)["state"], "can_reply")

    def test_story_shared_media_metadata_and_profile_is_not_fetched_without_consent(self):
        connection = self.connect(account="ig_context")
        self.receive(connection, sender="ig_story_user", mid="ig_story", text="Story", extra={"reply_to": {"mid": "story_ref"}, "attachments": [{"type": "image", "payload": {"url": "https://secret.example/expiring"}}]})
        message = Message.objects.get(provider_message_id="ig_story")
        self.assertTrue(message.metadata["story_reply"])
        self.assertEqual(message.metadata["attachment_types"], ["image"])
        self.assertNotIn("secret.example", str(message.metadata))
        self.assertTrue(message.conversation.contact.display_name.startswith("Instagram user"))

    def test_standard_window_manual_send_idempotency_and_read_reaction_edit(self):
        connection = self.connect(account="ig_outbound")
        self.receive(connection, mid="ig_inbound_outbound")
        conversation = Message.objects.get(provider_message_id="ig_inbound_outbound").conversation
        sent, created = send_instagram_message(conversation=conversation, body="Manual reply", client_message_id="client-1", membership=self.membership)
        duplicate, created_again = send_instagram_message(conversation=conversation, body="Manual reply", client_message_id="client-1", membership=self.membership)
        self.assertTrue(created); self.assertFalse(created_again); self.assertEqual(sent.id, duplicate.id)
        self.assertTrue(sent.provider_message_id.startswith("ig_fake_"))
        now_ms = int((timezone.now() + timedelta(seconds=1)).timestamp() * 1000)
        for event_type, data in [
            ("reaction", {"reaction": {"mid": sent.provider_message_id, "reaction": "love"}}),
            ("read", {"read": {"watermark": now_ms}}),
            ("edit", {"message": {"mid": "ig_inbound_outbound", "text": "Edited", "is_edited": True}}),
        ]:
            item = {"sender": {"id": "igscoped_customer_1"}, "recipient": {"id": connection.instagram_user_id}, "timestamp": now_ms, **data}
            raw = json.dumps({"object": "instagram", "entry": [{"id": connection.instagram_user_id, "messaging": [item]}]}, separators=(",", ":")).encode()
            signature = "sha256=" + hmac.new(b"test-only-instagram-app-secret", raw, hashlib.sha256).hexdigest()
            with self.captureOnCommitCallbacks(execute=False):
                self.client.post("/api/v1/webhooks/instagram/", raw, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)
            process_webhook_event(InstagramWebhookEvent.objects.order_by("-received_at").first().id)
        sent.refresh_from_db()
        self.assertEqual(sent.status, MessageStatus.READ)
        self.assertEqual(sent.metadata["instagram_reaction"], "love")
        inbound = Message.objects.get(provider_message_id="ig_inbound_outbound")
        self.assertEqual(inbound.body, "Edited")

    def test_expired_window_human_agent_is_manual_only_and_customer_reopens(self):
        connection = self.connect(account="ig_window")
        connection.human_agent_approved = True
        connection.save(update_fields=["human_agent_approved", "updated_at"])
        self.receive(connection, mid="ig_window_first")
        conversation = Message.objects.get(provider_message_id="ig_window_first").conversation
        window = InstagramConversationWindow.objects.get(conversation=conversation)
        window.standard_window_expires_at = timezone.now() - timedelta(seconds=1)
        window.save(update_fields=["standard_window_expires_at", "updated_at"])
        self.assertEqual(window_eligibility(conversation)["state"], "human_agent_available")
        with self.assertRaisesMessage(InstagramError, "human_agent_available"):
            send_instagram_message(conversation=conversation, body="Normal", client_message_id="normal-expired", membership=self.membership)
        with self.assertRaisesMessage(InstagramError, "human_agent_manual_only"):
            send_instagram_message(conversation=conversation, body="AI misuse", client_message_id="ai-human", sender_type="ai", human_agent=True)
        sent, created = send_instagram_message(conversation=conversation, body="Manual support", client_message_id="human-1", membership=self.membership, human_agent=True)
        self.assertTrue(created); self.assertTrue(sent.metadata["human_agent"])
        self.receive(connection, mid="ig_window_reopen", text="New customer reply")
        self.assertEqual(window_eligibility(conversation)["state"], "can_reply")

    def test_provider_failures_token_expiry_suspension_and_health(self):
        connection = self.connect(account="ig_health")
        self.receive(connection, mid="ig_health_in")
        conversation = Message.objects.get(provider_message_id="ig_health_in").conversation
        for text, code in [("[meta-transient-error]", "provider_temporarily_unavailable"), ("[meta-policy-error]", "provider_policy_rejected")]:
            with self.assertRaisesMessage(InstagramError, code):
                send_instagram_message(conversation=conversation, body=text, client_message_id=code, membership=self.membership)
        connection.refresh_from_db()
        connection.connection_status = InstagramConnectionStatus.EXPIRED
        connection.token_expires_at = timezone.now() - timedelta(seconds=1)
        connection.save(update_fields=["connection_status", "token_expires_at", "updated_at"])
        self.assertEqual(window_eligibility(conversation)["state"], "connection_expired")
        connection.connection_status = InstagramConnectionStatus.CONNECTED
        connection.token_expires_at = timezone.now() + timedelta(days=1)
        connection.save(update_fields=["connection_status", "token_expires_at", "updated_at"])
        self.organization.status = OrganizationStatus.SUSPENDED
        self.organization.save(update_fields=["status", "updated_at"])
        self.assertEqual(window_eligibility(conversation)["state"], "organization_read_only")

    def test_ai_send_never_uses_human_agent_and_human_reply_pauses_ai(self):
        connection = self.connect(account="ig_ai")
        self.receive(connection, mid="ig_ai_in")
        conversation = Message.objects.get(provider_message_id="ig_ai_in").conversation
        conversation.ai_state = ConversationAIState.AUTOPILOT_INSTAGRAM
        conversation.save(update_fields=["ai_state", "updated_at"])
        run = type("Run", (), {"conversation": conversation})()
        ai_message, _ = send_ai_message(run=run, body="AI standard reply", client_message_id="ai-standard", metadata={"ai_run_id": "safe-id"})
        self.assertFalse(ai_message.metadata["human_agent"])
        manual, _ = send_instagram_message(conversation=conversation, body="Human takes over", client_message_id="manual-takeover", membership=self.membership)
        conversation.refresh_from_db()
        self.assertEqual(manual.sender_type, "agent")
        self.assertEqual(conversation.ai_state, ConversationAIState.PAUSED_BY_HUMAN)

    def test_lower_role_cannot_connect_or_disconnect_and_superuser_has_no_bypass(self):
        connection = self.connect(account="ig_roles")
        agent_user = User.objects.create_user(username="ig-agent", email="ig-agent@example.test", password="pw12345!")
        agent = OrganizationMembership.objects.create(organization=self.organization, user=agent_user, role="agent", status="active")
        self.client.force_authenticate(agent_user)
        self.assertEqual(self.start().status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/integrations/instagram/{connection.id}/disconnect/", {}, format="json", **self.tenant()).status_code, 403)
        superuser = User.objects.create_superuser(username="ig-root", email="ig-root@example.test", password="pw12345!")
        self.client.force_authenticate(superuser)
        self.assertIn(self.client.get(f"/api/v1/integrations/instagram/{connection.id}/", **self.tenant()).status_code, [403, 404])

    def test_connection_views_health_operations_and_all_safe_test_controls(self):
        connection = self.connect(account="ig_views")
        listed = self.client.get("/api/v1/integrations/instagram/", **self.tenant())
        self.assertEqual(listed.data["count"], 1)
        patched = self.client.patch(
            f"/api/v1/integrations/instagram/{connection.id}/",
            {"automation_mode": "suggest"}, format="json", **self.tenant(),
        )
        self.assertEqual(patched.data["automation_mode"], "suggest")
        self.assertEqual(self.client.get(f"/api/v1/integrations/instagram/{connection.id}/health/", **self.tenant()).status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/integrations/instagram/{connection.id}/health/", {}, format="json", **self.tenant()).status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/integrations/instagram/{connection.id}/backfill/", {"limit": 5000}, format="json", **self.tenant()).data["limit"], 100)
        self.assertEqual(self.client.get(f"/api/v1/integrations/instagram/{connection.id}/operations/", **self.tenant()).status_code, 200)
        for action, expected in [
            ("permission_missing", "degraded"),
            ("restore", "connected"),
            ("approve_human_agent", "connected"),
            ("expire_token", "expired"),
            ("restore", "connected"),
        ]:
            response = self.client.post(
                f"/api/v1/integrations/instagram/{connection.id}/test-control/",
                {"action": action}, format="json", **self.tenant(),
            )
            self.assertEqual(response.data["connection_status"], expected)
        invalid = self.client.post(
            f"/api/v1/integrations/instagram/{connection.id}/test-control/",
            {"action": "not-real"}, format="json", **self.tenant(),
        )
        self.assertEqual(invalid.status_code, 400)

    def test_fake_event_endpoint_supports_shared_echo_reaction_read_and_edit(self):
        connection = self.connect(account="ig_event_views")
        for index, event_type in enumerate(["shared_post", "echo", "reaction", "read", "edit"]):
            response = self.client.post(
                f"/api/v1/integrations/instagram/{connection.id}/test-event/",
                {"event_type": event_type, "message_id": f"view_event_{index}", "text": "Safe event"},
                format="json", **self.tenant(),
            )
            self.assertEqual(response.status_code, 202)
        self.assertGreaterEqual(InstagramWebhookEvent.objects.count(), 5)

    def test_periodic_jobs_backfill_and_retry_terminal_states(self):
        connection = self.connect(account="ig_tasks")
        connection.token_expires_at = timezone.now() + timedelta(days=1)
        connection.save(update_fields=["token_expires_at", "updated_at"])
        self.assertEqual(check_instagram_connections()["checked"], 1)
        self.assertEqual(warn_instagram_token_expiry()["warnings"], 1)
        self.assertEqual(verify_instagram_subscriptions()["checked"], 1)
        self.assertEqual(bounded_instagram_backfill(str(connection.id), 999)["requested_limit"], 100)
        self.receive(connection, mid="ig_retry_source")
        conversation = Message.objects.get(provider_message_id="ig_retry_source").conversation
        message = Message.objects.create(
            organization=self.organization,
            conversation=conversation,
            channel_connection=connection.channel_connection,
            direction="outbound",
            sender_type="agent",
            sender_membership=self.membership,
            client_message_id="retry-task-message",
            content_type="text",
            body="Retry succeeds",
            status="queued",
            occurred_at=timezone.now(),
        )
        attempt = InstagramOutboundAttempt.objects.create(
            organization=self.organization,
            connection=connection,
            message=message,
            status=InstagramOutboundStatus.QUEUED,
            attempt_count=1,
        )
        self.assertEqual(retry_instagram_outbound(str(attempt.id))["status"], "sent")
        exhausted_message = Message.objects.create(
            organization=self.organization,
            conversation=conversation,
            channel_connection=connection.channel_connection,
            direction="outbound",
            sender_type="agent",
            sender_membership=self.membership,
            client_message_id="retry-exhausted-message",
            content_type="text",
            body="Never retried",
            status="queued",
            occurred_at=timezone.now(),
        )
        exhausted = InstagramOutboundAttempt.objects.create(
            organization=self.organization,
            connection=connection,
            message=exhausted_message,
            status=InstagramOutboundStatus.QUEUED,
            attempt_count=3,
        )
        self.assertEqual(retry_instagram_outbound(str(exhausted.id))["status"], "dead_letter")


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.payload


@override_settings(
    META_APP_ID="live-app",
    META_APP_SECRET="test-only-live-provider-secret",
    META_INSTAGRAM_GRAPH_API_VERSION="v-current",
    META_INSTAGRAM_REDIRECT_URI="https://api.example.test/api/v1/integrations/instagram/oauth/callback/",
)
class LiveInstagramProviderContractTests(APITestCase):
    def test_oauth_send_health_and_permission_snapshot_contract(self):
        responses = [
            FakeHTTPResponse({"access_token": "short-lived"}),
            FakeHTTPResponse({"access_token": "long-lived", "expires_in": 3600}),
            FakeHTTPResponse({"user_id": "ig_live_1", "username": "live_shop", "name": "Live Shop", "account_type": "BUSINESS"}),
            FakeHTTPResponse({"data": [{"permission": "instagram_business_basic", "status": "granted"}, {"permission": "instagram_business_manage_messages", "status": "granted"}]}),
        ]
        provider = LiveInstagramProvider()
        with patch("instagram.providers.urllib.request.urlopen", side_effect=responses) as urlopen:
            snapshot = provider.exchange_code("authorization-code")
        self.assertEqual(snapshot.instagram_user_id, "ig_live_1")
        self.assertEqual(set(snapshot.permissions), {"instagram_business_basic", "instagram_business_manage_messages"})
        self.assertNotIn("live-test-secret", str(urlopen.call_args_list))

        channel = type("Channel", (), {"get_credentials": lambda _self: {"access_token": "encrypted-token-value"}})()
        connection = type("Connection", (), {
            "id": "connection-live", "instagram_user_id": "ig_live_1", "graph_api_version": "v-current",
            "channel_connection": channel, "permission_snapshot": list(snapshot.permissions),
        })()
        with patch("instagram.providers.urllib.request.urlopen", return_value=FakeHTTPResponse({"message_id": "provider-mid", "request_id": "request-safe"})):
            sent = provider.send_text(connection=connection, recipient_id="ig-scoped-customer", text="Hello", human_agent=False)
        self.assertEqual(sent.message_id, "provider-mid")
        health_responses = [
            FakeHTTPResponse({"user_id": "ig_live_1", "username": "live_shop"}),
            FakeHTTPResponse({"data": [{"permission": "instagram_business_basic", "status": "granted"}]}),
        ]
        with patch("instagram.providers.urllib.request.urlopen", side_effect=health_responses):
            health = provider.health(connection)
        self.assertTrue(health["account_matches"])
        self.assertEqual(health["permissions"], ["instagram_business_basic"])

    def test_live_provider_redacts_http_and_network_failures(self):
        provider = LiveInstagramProvider()
        error = urllib.error.HTTPError("https://graph.instagram.com", 429, "rate", {}, None)
        with patch("instagram.providers.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesMessage(InstagramProviderError, "provider_rate_limited"):
                provider._json_request("https://graph.instagram.com/v-current/me")
        with patch("instagram.providers.urllib.request.urlopen", side_effect=urllib.error.URLError("secret upstream detail")):
            with self.assertRaisesMessage(InstagramProviderError, "provider_temporarily_unavailable"):
                provider._json_request("https://graph.instagram.com/v-current/me")
