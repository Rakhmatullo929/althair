from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from assistant_context.services import publish_assistant_profile
from ai_runtime.services import ensure_runtime_config
from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.models import ConversationAIState, Message, MessageDirection, MessageSenderType
from crm.services import send_outbound_message
from organizations.models import OrganizationMembership
from organizations.services import create_organization
from web_chat.models import InstallationAIMode, InstallationStatus, WebChatEvent, WebChatInstallation, WebChatSession
from web_chat.services import (
    WebChatError,
    ai_state_for_installation,
    cleanup_expired_sessions,
    create_origin_proof,
    normalize_allowed_origins,
    normalize_origin,
    token_hash,
    web_chat_autopilot_allowed,
)
from web_chat.tasks import cleanup_web_chat_retention


User = get_user_model()
ORIGIN = "http://localhost:3001"


@override_settings(
    DEBUG=True,
    WEB_CHAT_ENABLE_PUBLIC=True,
    WEB_CHAT_GLOBAL_KILL_SWITCH=False,
    WEB_CHAT_ALLOW_FAKE_AUTOPILOT=True,
    WEB_CHAT_SESSION_SIGNING_KEY="web-chat-test-signing-key",
    WEB_CHAT_WIDGET_ORIGINS=[ORIGIN],
    WEB_CHAT_SESSIONS_PER_IP_HOUR=20,
    WEB_CHAT_SESSIONS_PER_INSTALLATION_DAY=100,
    WEB_CHAT_SESSIONS_PER_ORGANIZATION_DAY=100,
    WEB_CHAT_MESSAGES_PER_SESSION_MINUTE=20,
    WEB_CHAT_MESSAGES_PER_INSTALLATION_MINUTE=100,
    WEB_CHAT_MAX_URLS_PER_MESSAGE=2,
    WEB_CHAT_BLOCKED_TERMS=["blocked-term"],
    ENABLE_CRM_TEST_CHANNEL=True,
    AI_RUNTIME_PROVIDER="fake",
    AI_RUNTIME_ENABLE_REAL_OPENAI=False,
    AI_RUNTIME_GLOBAL_KILL_SWITCH=False,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class WebChatTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="web-owner", email="web-owner@example.test", password="pw12345!")
        self.organization = create_organization(creator=self.owner, name="Web Clinic", slug="web-clinic")
        self.membership = OrganizationMembership.objects.get(organization=self.organization, user=self.owner)
        self.connection = ChannelConnection.objects.create(
            organization=self.organization, type=ChannelType.WEBCHAT, provider="public_web_chat",
            display_name="Website", external_identifier="wc_test", status=ChannelStatus.ACTIVE,
        )
        self.installation = WebChatInstallation.objects.create(
            organization=self.organization, channel_connection=self.connection, public_key="wc_test",
            display_name="Website", status=InstallationStatus.ACTIVE, allowed_origins=[ORIGIN],
            collect_email=True,
            created_by=self.membership, updated_by=self.membership,
        )
        self.other_owner = User.objects.create_user(username="web-other", email="other@example.test", password="pw12345!")
        self.other = create_organization(creator=self.other_owner, name="Other Web", slug="other-web")
        self.client.force_authenticate(self.owner)

    def tenant(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.organization).id)}

    def public(self):
        return {"HTTP_ORIGIN": ORIGIN, "REMOTE_ADDR": "127.0.0.22"}

    def create_session(self, consent=True):
        config = self.client.get(f"/api/v1/public/web-chat/installations/{self.installation.public_key}/config/", **self.public())
        self.assertEqual(config.status_code, 200, config.data)
        response = self.client.post(
            f"/api/v1/public/web-chat/installations/{self.installation.public_key}/sessions/",
            {"origin_proof": config.data["origin_proof"], "consent_accepted": consent, "language": "en"},
            format="json", **self.public(),
        )
        return response

    def auth(self, session):
        return {**self.public(), "HTTP_AUTHORIZATION": f"Bearer {session['session_token']}"}

    def publish_context(self):
        profile = self.organization.assistant_profile
        values = {
            "assistant_name": "Clinic", "business_summary": "Published facts", "business_description": "Support facts",
            "target_customers": "Patients", "products_services": "Consultations", "service_area": "Tashkent",
            "supported_languages": ["ru", "uz", "en"], "default_language": "ru", "tone_of_voice": "Calm",
            "introduction": "Clinic assistant", "escalation_instructions": "Ask a human when uncertain",
            "prohibited_topics": "Diagnosis", "prohibited_actions": "Booking and billing", "fallback_response": "A person will help",
            "additional_instructions": "Plain text only",
        }
        for key, value in values.items(): setattr(profile, key, value)
        profile.updated_by = self.owner
        profile.save()
        publish_assistant_profile(profile=profile, actor=self.owner)

    def test_origin_normalization_is_exact(self):
        self.assertEqual(normalize_origin("HTTP://LOCALHOST:3001/"), ORIGIN)
        self.assertEqual(normalize_allowed_origins([ORIGIN, ORIGIN]), [ORIGIN])
        for bad in ["*", "https://*.example.com", "https://example.com/path", "http://example.com"]:
            with self.assertRaises(ValidationError): normalize_origin(bad)

    def test_portal_crud_is_tenant_scoped_and_hides_secret_state(self):
        response = self.client.get("/api/v1/web-chat/installations/", **self.tenant())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertNotIn("token_hash", str(response.data))
        denied = self.client.get(f"/api/v1/web-chat/installations/{self.installation.id}/", **self.tenant(self.other))
        self.assertIn(denied.status_code, [403, 404])

    def test_create_rejects_wildcard_and_cross_tenant_branch(self):
        invalid = self.client.post("/api/v1/web-chat/installations/", {"display_name": "Unsafe", "allowed_origins": ["https://*.example.com"]}, format="json", **self.tenant())
        self.assertEqual(invalid.status_code, 400)
        other_branch = self.other.branches.create(name="Private", timezone="Asia/Tashkent")
        cross = self.client.post("/api/v1/web-chat/installations/", {"display_name": "Cross", "allowed_origins": [ORIGIN], "default_branch": str(other_branch.id)}, format="json", **self.tenant())
        self.assertEqual(cross.status_code, 400)

    def test_owner_full_installation_lifecycle_health_and_anonymize(self):
        created = self.client.post(
            "/api/v1/web-chat/installations/",
            {"display_name": "Second website", "allowed_origins": [ORIGIN]},
            format="json", **self.tenant(),
        )
        self.assertEqual(created.status_code, 201, created.data)
        installation_id = created.data["id"]
        updated = self.client.patch(
            f"/api/v1/web-chat/installations/{installation_id}/",
            {"greeting": "Welcome to our secure chat", "retention_days": 14},
            format="json", **self.tenant(),
        )
        self.assertEqual(updated.status_code, 200)
        activated = self.client.post(f"/api/v1/web-chat/installations/{installation_id}/activate/", {}, format="json", **self.tenant())
        self.assertEqual(activated.data["status"], "active")
        paused = self.client.post(f"/api/v1/web-chat/installations/{installation_id}/pause/", {}, format="json", **self.tenant())
        self.assertEqual(paused.data["status"], "paused")
        self.assertEqual(self.client.get(f"/api/v1/web-chat/installations/{installation_id}/sessions/", **self.tenant()).status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/web-chat/installations/{installation_id}/metrics/", **self.tenant()).status_code, 200)

        visitor = self.create_session().data
        session = WebChatSession.objects.get(public_session_id=visitor["session_id"])
        anonymized = self.client.post(
            f"/api/v1/web-chat/installations/{self.installation.id}/sessions/{session.public_session_id}/anonymize/",
            {}, format="json", **self.tenant(),
        )
        self.assertEqual(anonymized.status_code, 200)

    def test_demo_seed_and_retention_task_are_idempotent(self):
        output = StringIO()
        call_command("seed_web_chat_demo", organization=self.organization.slug, stdout=output)
        call_command("seed_web_chat_demo", organization=self.organization.slug, stdout=output)
        self.assertIn("Web Chat demo ready", output.getvalue())
        self.assertEqual(WebChatInstallation.objects.filter(public_key="wc_demo_portal_test").count(), 1)
        self.assertEqual(cleanup_web_chat_retention()["processed"], 0)

    def test_public_config_requires_allowed_origin_and_no_store(self):
        response = self.client.get(f"/api/v1/public/web-chat/installations/{self.installation.public_key}/config/", **self.public())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], ORIGIN)
        self.assertIn("no-store", response["Cache-Control"])
        blocked = self.client.get(f"/api/v1/public/web-chat/installations/{self.installation.public_key}/config/", HTTP_ORIGIN="http://localhost:3009")
        self.assertEqual(blocked.status_code, 403)

    @override_settings(WEB_CHAT_ENABLE_PUBLIC=False)
    def test_public_api_is_fail_closed(self):
        response = self.client.get(f"/api/v1/public/web-chat/installations/{self.installation.public_key}/config/", **self.public())
        self.assertEqual(response.status_code, 404)

    def test_consent_required_and_session_token_is_not_stored_raw(self):
        denied = self.create_session(consent=False)
        self.assertEqual(denied.status_code, 409)
        created = self.create_session().data
        row = WebChatSession.objects.get(public_session_id=created["session_id"])
        self.assertNotEqual(row.token_hash, created["session_token"])
        self.assertEqual(row.token_hash, token_hash(created["session_token"]))
        self.assertTrue(row.consented_at)

    def test_session_rejects_wrong_token_origin_and_expiry(self):
        created = self.create_session().data
        url = f"/api/v1/public/web-chat/sessions/{created['session_id']}/messages/"
        self.assertEqual(self.client.get(url, **{**self.public(), "HTTP_AUTHORIZATION": "Bearer wrong"}).status_code, 401)
        self.assertEqual(self.client.get(url, HTTP_ORIGIN="http://localhost:3009", HTTP_AUTHORIZATION=f"Bearer {created['session_token']}").status_code, 401)
        WebChatSession.objects.filter(public_session_id=created["session_id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get(url, **self.auth(created)).status_code, 401)

    def test_inbound_is_idempotent_and_creates_real_crm_records(self):
        created = self.create_session().data
        url = f"/api/v1/public/web-chat/sessions/{created['session_id']}/messages/"
        headers = {**self.auth(created), "HTTP_IDEMPOTENCY_KEY": "visitor-message-1"}
        first = self.client.post(url, {"body": "Hello team"}, format="json", **headers)
        second = self.client.post(url, {"body": "Hello team"}, format="json", **headers)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertTrue(first.data["created"]); self.assertFalse(second.data["created"])
        session = WebChatSession.objects.get(public_session_id=created["session_id"])
        self.assertEqual(session.conversation.organization, self.organization)
        self.assertEqual(session.contact.organization, self.organization)
        self.assertEqual(Message.objects.filter(conversation=session.conversation, direction=MessageDirection.INBOUND).count(), 1)

    def test_identity_fields_update_consent_owned_contact(self):
        created = self.create_session().data
        url = f"/api/v1/public/web-chat/sessions/{created['session_id']}/identity/"
        response = self.client.patch(url, {"name": "Aziza", "email": "aziza@example.test"}, format="json", **self.auth(created))
        self.assertEqual(response.status_code, 200)
        row = WebChatSession.objects.get(public_session_id=created["session_id"])
        self.assertEqual(row.contact.display_name, "Aziza")
        self.assertTrue(row.contact.identities.filter(type="email", normalized_value="aziza@example.test").exists())

    def test_plain_text_abuse_and_url_moderation(self):
        created = self.create_session().data
        url = f"/api/v1/public/web-chat/sessions/{created['session_id']}/messages/"
        for index, body in enumerate(["<b>unsafe</b>", "blocked-term", "https://a.test https://b.test https://c.test"]):
            response = self.client.post(url, {"body": body}, format="json", **{**self.auth(created), "HTTP_IDEMPOTENCY_KEY": f"blocked-{index}"})
            self.assertIn(response.status_code, [400, 429])

    def test_poll_sse_read_and_handoff_lifecycle(self):
        created = self.create_session().data
        base = f"/api/v1/public/web-chat/sessions/{created['session_id']}"
        self.client.post(f"{base}/messages/", {"body": "Need a human"}, format="json", **{**self.auth(created), "HTTP_IDEMPOTENCY_KEY": "handoff-message"})
        handoff = self.client.post(f"{base}/handoff/", {}, format="json", **self.auth(created))
        self.assertEqual(handoff.status_code, 200)
        session = WebChatSession.objects.get(public_session_id=created["session_id"])
        inbox = self.client.get(
            f"/api/v1/conversations/{session.conversation_id}/ai/runs/",
            **self.tenant(),
        )
        self.assertEqual(inbox.status_code, 200)
        self.assertEqual(inbox.data["handoffs"][0]["reason_code"], "customer_request")
        poll = self.client.get(f"{base}/messages/?after=0", **self.auth(created))
        self.assertTrue(any(item["type"] == "handoff" for item in poll.data["events"]))
        stream = self.client.get(f"{base}/events/?after=0", **self.auth(created))
        payload = b"".join(stream.streaming_content).decode()
        self.assertIn("event: handoff", payload)
        self.assertEqual(self.client.post(f"{base}/read/", {}, format="json", **self.auth(created)).status_code, 200)

    def test_unified_inbox_reply_reaches_widget_and_pauses_ai(self):
        created = self.create_session().data
        base = f"/api/v1/public/web-chat/sessions/{created['session_id']}"
        self.client.post(f"{base}/messages/", {"body": "Hello"}, format="json", **{**self.auth(created), "HTTP_IDEMPOTENCY_KEY": "first"})
        row = WebChatSession.objects.get(public_session_id=created["session_id"])
        with self.captureOnCommitCallbacks(execute=True):
            message, new = send_outbound_message(organization=self.organization, conversation=row.conversation, membership=self.membership, body="Operator reply", client_message_id="operator-1")
        self.assertTrue(new); self.assertEqual(message.sender_type, MessageSenderType.AGENT)
        row.conversation.refresh_from_db(); self.assertEqual(row.conversation.ai_state, ConversationAIState.PAUSED_BY_HUMAN)
        events = self.client.get(f"{base}/messages/?after=0", **self.auth(created)).data["events"]
        self.assertTrue(any(item.get("message", {}).get("body") == "Operator reply" for item in events))

    def test_close_rotates_token_and_resume_rotates_active_token(self):
        created = self.create_session().data
        base = f"/api/v1/public/web-chat/sessions/{created['session_id']}"
        resumed = self.client.post(f"{base}/resume/", {}, format="json", **self.auth(created))
        self.assertEqual(resumed.status_code, 200)
        self.assertNotEqual(resumed.data["session_token"], created["session_token"])
        updated = {**created, "session_token": resumed.data["session_token"]}
        self.assertEqual(self.client.post(f"{base}/close/", {}, format="json", **self.auth(updated)).status_code, 200)
        self.assertEqual(self.client.get(f"{base}/messages/", **self.auth(updated)).status_code, 401)

    def test_rotation_and_revoke_invalidate_public_entrypoint(self):
        rotated = self.client.post(f"/api/v1/web-chat/installations/{self.installation.id}/rotate-key/", {}, format="json", **self.tenant())
        self.assertEqual(rotated.status_code, 200)
        self.assertNotEqual(rotated.data["public_key"], "wc_test")
        revoked = self.client.post(f"/api/v1/web-chat/installations/{self.installation.id}/revoke/", {}, format="json", **self.tenant())
        self.assertEqual(revoked.data["status"], "revoked")
        self.assertEqual(self.client.get(f"/api/v1/public/web-chat/installations/{rotated.data['public_key']}/config/", **self.public()).status_code, 404)

    def test_anonymize_and_retention_remove_identity_and_events(self):
        created = self.create_session().data
        row = WebChatSession.objects.get(public_session_id=created["session_id"])
        row.expires_at = timezone.now() - timedelta(days=50); row.save(update_fields=["expires_at"])
        WebChatEvent.objects.create(organization=self.organization, session=row, sequence=99, event_type="old")
        self.installation.retention_days = 30; self.installation.save(update_fields=["retention_days"])
        self.assertEqual(cleanup_expired_sessions(), 1)
        row.refresh_from_db(); self.assertEqual(row.ip_hash, ""); self.assertFalse(row.events.exists())

    def test_web_chat_autopilot_requires_published_context_and_explicit_fake_gate(self):
        self.installation.ai_mode = InstallationAIMode.AUTOPILOT; self.installation.save(update_fields=["ai_mode"])
        self.assertFalse(web_chat_autopilot_allowed(self.installation))
        self.publish_context()
        config = ensure_runtime_config(self.organization)
        config.enabled = True; config.provider = "fake"; config.save(); config.allowed_channel_connections.add(self.connection)
        self.assertTrue(web_chat_autopilot_allowed(self.installation))
        self.assertEqual(ai_state_for_installation(self.organization, self.connection), ConversationAIState.AUTOPILOT_WEB_CHAT)

    def test_runtime_config_preserves_owned_public_web_chat_channel(self):
        config = ensure_runtime_config(self.organization)
        config.allowed_channel_connections.add(self.connection)
        internal = ChannelConnection.objects.create(
            organization=self.organization,
            type=ChannelType.WEBCHAT,
            provider="internal_test",
            display_name="Development test channel",
            external_identifier="internal-test",
            status=ChannelStatus.ACTIVE,
        )
        accepted = self.client.patch(
            "/api/v1/ai/runtime-config/",
            {"allowed_channel_connections": [str(self.connection.id), str(internal.id)]},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertCountEqual(
            accepted.data["allowed_channel_connections"],
            [self.connection.id, internal.id],
        )

        foreign = ChannelConnection.objects.create(
            organization=self.other,
            type=ChannelType.WEBCHAT,
            provider="public_web_chat",
            display_name="Private website",
            external_identifier="wc_private",
            status=ChannelStatus.ACTIVE,
        )
        rejected = self.client.patch(
            "/api/v1/ai/runtime-config/",
            {"allowed_channel_connections": [str(foreign.id)]},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(rejected.status_code, 400)

    @override_settings(WEB_CHAT_MESSAGES_PER_SESSION_MINUTE=1)
    def test_rate_limit_returns_safe_error(self):
        created = self.create_session().data
        url = f"/api/v1/public/web-chat/sessions/{created['session_id']}/messages/"
        one = self.client.post(url, {"body": "One"}, format="json", **{**self.auth(created), "HTTP_IDEMPOTENCY_KEY": "one"})
        two = self.client.post(url, {"body": "Two"}, format="json", **{**self.auth(created), "HTTP_IDEMPOTENCY_KEY": "two"})
        self.assertEqual(one.status_code, 201); self.assertEqual(two.status_code, 429)
        self.assertEqual(two.data["error"]["code"], "rate_limited")
